import json, re, io, difflib, os, base64, secrets
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Query, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from . import db, engine, vision, privacy, quality, health

HERE = os.path.dirname(__file__)
CHANNELS = ["Social media", "Email", "Website", "Press release", "Brochure"]
STATUSES = ("Draft", "In Review", "Changes Requested", "Approved")

@asynccontextmanager
async def lifespan(app):
    import threading
    threading.Thread(target=quality.get_eval, daemon=True).start()  # warm the evaluation cache used for confidence calibration
    db.init()
    seed_demo()   # idempotent: only seeds when the demo workspace has no content yet
    yield

app = FastAPI(title="BrandLens", description="Explainable brand-governance review with human control", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

@app.exception_handler(RequestValidationError)
async def _validation(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "Please check your input: a field is missing, too long or in the wrong format."})

@app.exception_handler(Exception)
async def _unexpected(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server. Please try again."})

def get_db():
    c = db.connect()
    try:
        yield c
    except Exception:
        c.rollback(); raise
    finally:
        c.close()

def current_user(authorization: Optional[str] = Header(None), c=Depends(get_db)):
    token = (authorization or "").replace("Bearer ", "").strip()
    u = c.one("SELECT u.id,u.username,u.name,u.role,u.workspace_id,w.name AS workspace FROM sessions s JOIN users u ON u.id=s.user_id JOIN workspaces w ON w.id=u.workspace_id WHERE s.token=?", (token,)) if token else None
    if not u:
        raise HTTPException(401, "Please log in")
    return u

def need_manager(u):
    if u["role"] != "manager":
        raise HTTPException(403, "Only a brand manager can change the knowledge base")

def get_kb(c, ws):
    return {"clauses": c.rows("SELECT * FROM clauses WHERE workspace_id=? ORDER BY id", (ws,)), "terms": c.rows("SELECT * FROM terms WHERE workspace_id=? ORDER BY id", (ws,)),
            "facts": c.rows("SELECT * FROM facts WHERE workspace_id=? ORDER BY id", (ws,))}

def log(c, ws, sid, uid, action, detail=""):
    c.insert("INSERT INTO audit(workspace_id,submission_id,user_id,action,detail,created_at) VALUES(?,?,?,?,?,?)", (ws, sid, uid, action, detail, db.now()))

# ---------------- review pipeline ----------------
def logo_image(c, ws):
    a = c.one("SELECT data FROM assets WHERE workspace_id=? AND kind='logo' ORDER BY id DESC", (ws,))
    if not a:
        return None
    try:
        return vision.open_image(a["data"])
    except Exception:
        return None

def run_version(c, sub, n, text, colors, uid, note, image=None):
    """image = dict(bytes, mime, alt, kind) or None. Runs all checks, calibrates confidence, stores findings, carries over earlier dismissals."""
    ws, sid = sub["workspace_id"], sub["id"]
    kb = get_kb(c, ws)
    found, ai_status = engine.review(text, colors, sub["purpose"], sub["channel"], kb)
    if image:
        cl = {x["check_type"]: x for x in kb["clauses"]}
        params = {k: cl[k] for k in ("alt", "palette", "logo", "imgquality") if k in cl}
        params["logo_img"] = logo_image(c, ws)
        try:
            img = vision.open_image(image["bytes"])
            found += vision.review_image(img, image["kind"], image["alt"], sub["channel"], params)
            aif, alt_s, st = vision.ai_image_review(image["bytes"], image["alt"], kb["clauses"], sub["channel"])
            found += aif
            if alt_s:
                for f in found:
                    if f["rule"] == "alt" and f["start"] < 0:
                        f["suggestion"] = alt_s
            ai_status += " | " + st
        except Exception:
            ai_status += " | image could not be analysed"
    stats = quality.live_stats(c, ws)
    prev = c.rows("SELECT * FROM findings WHERE submission_id=? AND version_n=? AND status='dismissed'", (sid, n - 1)) if n > 1 else []
    c.insert("INSERT INTO versions(submission_id,n,text,colors,note,ai_status,created_by,created_at,image,image_mime,image_alt,image_kind) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             (sid, n, text, colors, note, ai_status, uid, db.now(), image["bytes"] if image else None, image["mime"] if image else None, image["alt"] if image else None, image["kind"] if image else None))
    for f in found:
        acc, dis, _ = stats.get(f["rule"], (0, 0, 0))
        raw = f["confidence"]; conf, prior = quality.calibrate(f["rule"], raw, acc, dis)
        meta = dict(f.get("meta") or {}); meta.update({"raw_confidence": raw, "eval_precision": prior, "human_accepted": acc, "human_dismissed": dis})
        st, reason, by = "open", None, None
        for p in prev:
            if p["source_ref"] == f["source_ref"] and p["original"] == f["original"] and p["rule"] == f["rule"]:
                st, reason, by = "dismissed", p["reason"], p["decided_by"]
        c.insert("""INSERT INTO findings(submission_id,version_n,rule,category,severity,"start","end",original,explanation,source_ref,source_title,source_text,confidence,suggestion,origin,status,reason,decided_by,meta,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (sid, n, f["rule"], f["category"], f["severity"], f["start"], f["end"], f["original"], f["explanation"], f["source_ref"], f["source_title"], f["source_text"], conf, f["suggestion"],
                  f["origin"], st, reason, by, json.dumps(meta), db.now()))
    log(c, ws, sid, uid, "review", f"Version {n} reviewed: {len(found)} finding(s). {ai_status}")
    return len(found)

def make_submission(c, u, title, purpose, channel, text, colors, image=None, commit=True):
    sid = c.insert("INSERT INTO submissions(workspace_id,title,purpose,channel,author_id,status,created_at) VALUES(?,?,?,?,?,'Draft',?)", (u["workspace_id"], title, purpose, channel, u["id"], db.now()))
    sub = {"id": sid, "workspace_id": u["workspace_id"], "purpose": purpose, "channel": channel}
    log(c, u["workspace_id"], sid, u["id"], "created", title)
    run_version(c, sub, 1, text, colors, u["id"], "Initial submission", image)
    if commit:
        c.commit()
    return sid

def prep_image(b64, kind, alt):
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64.split(",")[-1], validate=False)
    except Exception:
        raise HTTPException(400, "The image could not be read")
    kind = kind if kind in ("design", "photo") else "design"
    try:
        data, mime, w, h = vision.normalize(raw, kind)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"bytes": data, "mime": mime, "alt": (alt or "").strip()[:300], "kind": kind}

# ---------------- auth ----------------
class Login(BaseModel):
    username: str = Field(max_length=60)
    password: str = Field(max_length=100)

def new_session(c, uid):
    tok = secrets.token_urlsafe(24)
    c.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (tok, uid, db.now()))
    return tok

@app.post("/api/login")
def login(b: Login, c=Depends(get_db)):
    u = c.one("SELECT u.*, w.name AS workspace FROM users u JOIN workspaces w ON w.id=u.workspace_id WHERE u.username=?", (b.username.strip().lower(),))
    if not u or not db.check_pw(b.password, u["pw"]):
        raise HTTPException(401, "Wrong username or password")
    tok = new_session(c, u["id"]); c.commit()
    return {"token": tok, "user": {k: u[k] for k in ("id", "username", "name", "role", "workspace")}}

class WorkspaceIn(BaseModel):
    workspace: str = Field(min_length=2, max_length=60)
    name: str = Field(min_length=2, max_length=60)
    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=100)

@app.post("/api/workspaces")
def create_workspace(b: WorkspaceIn, c=Depends(get_db)):
    if c.one("SELECT 1 AS x FROM users WHERE username=?", (b.username.lower(),)):
        raise HTTPException(400, "That username is taken")
    ws = c.insert("INSERT INTO workspaces(name,created_at) VALUES(?,?)", (b.workspace.strip(), db.now()))
    uid = c.insert("INSERT INTO users(workspace_id,username,name,role,pw) VALUES(?,?,?,'manager',?)", (ws, b.username.lower(), b.name.strip(), db.hash_pw(b.password)))
    db.seed_kb(c, ws, {"name": b.workspace.strip(), "palette": [], "terms": [], "facts": []}, with_content=False)
    log(c, ws, None, uid, "workspace", "created")
    tok = new_session(c, uid); c.commit()
    return {"token": tok, "user": {"id": uid, "username": b.username.lower(), "name": b.name.strip(), "role": "manager", "workspace": b.workspace.strip()}}

@app.get("/api/me")
def me(u=Depends(current_user)):
    return u

@app.get("/api/disclosure")
def disclosure():
    on = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {"ai_enabled": on, "model": os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5") if on else None, "redaction": os.environ.get("REDACT_BEFORE_AI", "1") != "0", "database": "PostgreSQL" if db.PG else "SQLite"}

@app.get("/api/health")
def health_check(c=Depends(get_db)):
    c.scalar("SELECT 1")
    return {"ok": True, "database": "PostgreSQL" if db.PG else "SQLite"}

class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=2, max_length=60)
    role: str
    password: str = Field(min_length=6, max_length=100)

@app.get("/api/users")
def list_users(u=Depends(current_user), c=Depends(get_db)):
    return c.rows("SELECT id,username,name,role FROM users WHERE workspace_id=? ORDER BY id", (u["workspace_id"],))

@app.post("/api/users")
def add_user(b: UserIn, u=Depends(current_user), c=Depends(get_db)):
    need_manager(u)
    if b.role not in ("creator", "reviewer", "manager"):
        raise HTTPException(400, "Role must be creator, reviewer or manager")
    if c.one("SELECT 1 AS x FROM users WHERE username=?", (b.username.lower(),)):
        raise HTTPException(400, "That username is taken")
    c.insert("INSERT INTO users(workspace_id,username,name,role,pw) VALUES(?,?,?,?,?)", (u["workspace_id"], b.username.lower(), b.name.strip(), b.role, db.hash_pw(b.password)))
    log(c, u["workspace_id"], None, u["id"], "user", f"added {b.username} as {b.role}"); c.commit()
    return {"ok": True}

# ---------------- knowledge base ----------------
@app.get("/api/kb")
def kb(u=Depends(current_user), c=Depends(get_db)):
    k = get_kb(c, u["workspace_id"])
    k["has_logo"] = bool(c.one("SELECT 1 AS x FROM assets WHERE workspace_id=? AND kind='logo'", (u["workspace_id"],)))
    return k

class TermIn(BaseModel):
    banned: str = Field(min_length=1, max_length=80)
    preferred: str = Field(min_length=1, max_length=80)

class FactIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    keywords: str = Field("", max_length=200)
    value: str = Field(min_length=1, max_length=100)
    source: str = Field("", max_length=150)
    kind: str = "fact"

class ClauseIn(BaseModel):
    category: str = Field("Messaging", max_length=40)
    title: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000)

class ImportIn(BaseModel):
    text: str = Field(max_length=40000)

class PaletteIn(BaseModel):
    colors: List[str] = Field(max_length=12)

class LogoIn(BaseModel):
    image_b64: str

@app.post("/api/kb/term")
def add_term(b: TermIn, u=Depends(current_user), c=Depends(get_db)):
    need_manager(u)
    c.insert("INSERT INTO terms(workspace_id,banned,preferred,clause_ref) VALUES(?,?,?,'TERM-1')", (u["workspace_id"], b.banned.strip(), b.preferred.strip()))
    log(c, u["workspace_id"], None, u["id"], "kb", f"term {b.banned} -> {b.preferred}"); c.commit()
    return {"ok": True}

@app.post("/api/kb/fact")
def add_fact(b: FactIn, u=Depends(current_user), c=Depends(get_db)):
    need_manager(u)
    kind = "claim" if b.kind == "claim" else "fact"
    n = c.scalar("SELECT COUNT(*) FROM facts WHERE workspace_id=? AND kind=?", (u["workspace_id"], kind)) + 1
    ref = ("CLAIM-A" if kind == "claim" else "FACT-") + str(n)
    while c.one("SELECT 1 AS x FROM facts WHERE workspace_id=? AND ref=?", (u["workspace_id"], ref)):
        n += 1; ref = ("CLAIM-A" if kind == "claim" else "FACT-") + str(n)
    kws = [k.strip() for k in b.keywords.split(",") if k.strip()]
    c.insert("INSERT INTO facts(workspace_id,ref,kind,label,keywords,value,source) VALUES(?,?,?,?,?,?,?)", (u["workspace_id"], ref, kind, b.label.strip(), json.dumps(kws), b.value.strip(), b.source.strip()))
    log(c, u["workspace_id"], None, u["id"], "kb", f"{kind} {b.label}"); c.commit()
    return {"ok": True, "ref": ref}

def next_gref(c, ws):
    n = c.scalar("SELECT COUNT(*) FROM clauses WHERE workspace_id=?", (ws,)) + 1
    while c.one("SELECT 1 AS x FROM clauses WHERE workspace_id=? AND ref=?", (ws, f"G-{n}")):
        n += 1
    return f"G-{n}"

@app.post("/api/kb/clause")
def add_clause(b: ClauseIn, u=Depends(current_user), c=Depends(get_db)):
    need_manager(u)
    c.insert("INSERT INTO clauses(workspace_id,ref,category,title,text,check_type,params,severity) VALUES(?,?,?,?,?,'text','{}','medium')", (u["workspace_id"], next_gref(c, u["workspace_id"]), b.category, b.title, b.text))
    c.commit()
    return {"ok": True}

@app.post("/api/kb/import")
def import_guidelines(b: ImportIn, u=Depends(current_user), c=Depends(get_db)):
    need_manager(u)
    parts = [p.strip() for p in re.split(r"\n\s*\n", b.text) if len(p.strip()) > 20]
    if not parts:
        raise HTTPException(400, "Nothing to import. Paste guideline paragraphs separated by blank lines")
    for p in parts[:60]:
        first = p.split("\n")[0]
        c.insert("INSERT INTO clauses(workspace_id,ref,category,title,text,check_type,params,severity) VALUES(?,?,?,?,?,'text','{}','medium')",
                 (u["workspace_id"], next_gref(c, u["workspace_id"]), "Imported", (first[:60] if "\n" in p and len(first) < 70 else p[:50] + "..."), p[:2000]))
    log(c, u["workspace_id"], None, u["id"], "kb", f"imported {min(len(parts), 60)} clauses"); c.commit()
    return {"imported": min(len(parts), 60)}

@app.put("/api/kb/palette")
def set_palette(b: PaletteIn, u=Depends(current_user), c=Depends(get_db)):
    need_manager(u)
    cols = []
    for h in b.colors:
        h = h.strip()
        if not re.fullmatch(r"#(?:[0-9a-fA-F]{6})", h):
            raise HTTPException(400, f"\u201c{h}\u201d is not a valid colour. Use six-digit hex such as #1F6F5C")
        cols.append(h.upper())
    cl = c.one("SELECT * FROM clauses WHERE workspace_id=? AND check_type='palette'", (u["workspace_id"],))
    ws_name = c.scalar("SELECT name FROM workspaces WHERE id=?", (u["workspace_id"],))
    new_text = "Only the approved palette may be used: " + (", ".join(cols) if cols else "(no palette set yet)") + "."
    c.execute("UPDATE clauses SET params=?, text=? WHERE id=?", (json.dumps({"colors": cols}), new_text, cl["id"]))
    c.insert("INSERT INTO clause_history(workspace_id,ref,old_text,new_text,old_params,new_params,reason,changed_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
             (u["workspace_id"], cl["ref"], cl["text"], new_text, cl["params"], json.dumps({"colors": cols}), "Palette edited", u["id"], db.now()))
    c.commit()
    return {"ok": True, "colors": cols}

@app.post("/api/kb/logo")
def set_logo(b: LogoIn, u=Depends(current_user), c=Depends(get_db)):
    need_manager(u)
    img = prep_image(b.image_b64, "design", "")
    c.execute("DELETE FROM assets WHERE workspace_id=? AND kind='logo'", (u["workspace_id"],))
    c.insert("INSERT INTO assets(workspace_id,kind,name,data,mime) VALUES(?,?,?,?,?)", (u["workspace_id"], "logo", "Brand logo", img["bytes"], img["mime"]))
    log(c, u["workspace_id"], None, u["id"], "kb", "logo replaced"); c.commit()
    return {"ok": True}

@app.get("/api/kb/logo")
def get_logo(u=Depends(current_user), c=Depends(get_db)):
    a = c.one("SELECT data,mime FROM assets WHERE workspace_id=? AND kind='logo' ORDER BY id DESC", (u["workspace_id"],))
    if not a:
        raise HTTPException(404, "No logo uploaded")
    return Response(a["data"], media_type=a["mime"])

@app.delete("/api/kb/{kind}/{key}")
def del_kb(kind: str, key: str, u=Depends(current_user), c=Depends(get_db)):
    need_manager(u); ws = u["workspace_id"]
    if kind == "term":
        c.execute("DELETE FROM terms WHERE id=? AND workspace_id=?", (key, ws))
    elif kind == "fact":
        c.execute("DELETE FROM facts WHERE id=? AND workspace_id=?", (key, ws))
    elif kind == "clause":
        c.execute("DELETE FROM clauses WHERE ref=? AND workspace_id=? AND check_type='text'", (key, ws))
    elif kind == "logo":
        c.execute("DELETE FROM assets WHERE workspace_id=? AND kind='logo'", (ws,))
    else:
        raise HTTPException(404, "Unknown item")
    c.commit()
    return {"ok": True}

class ProposalIn(BaseModel):
    ref: str
    exempt_channels: List[str] = []
    new_text: str = Field("", max_length=2000)
    reason: str = Field("", max_length=300)

@app.post("/api/proposals/apply")
def apply_proposal(b: ProposalIn, u=Depends(current_user), c=Depends(get_db)):
    """A manager accepts a Guideline Health proposal: the clause wording and its channel exceptions change, and the change is recorded."""
    need_manager(u)
    cl = c.one("SELECT * FROM clauses WHERE workspace_id=? AND ref=?", (u["workspace_id"], b.ref))
    if not cl or cl["check_type"] == "text":
        raise HTTPException(404, "Rule not found")
    bad = [x for x in b.exempt_channels if x not in CHANNELS]
    if bad:
        raise HTTPException(400, "Unknown channel")
    params = json.loads(cl["params"] or "{}"); old = json.dumps(params)
    ex = sorted(set(params.get("exempt_channels", [])) | set(b.exempt_channels))
    if ex:
        params["exempt_channels"] = ex
    text = b.new_text.strip() or cl["text"]
    c.execute("UPDATE clauses SET params=?, text=? WHERE id=?", (json.dumps(params), text, cl["id"]))
    c.insert("INSERT INTO clause_history(workspace_id,ref,old_text,new_text,old_params,new_params,reason,changed_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
             (u["workspace_id"], cl["ref"], cl["text"], text, old, json.dumps(params), b.reason or "Guideline Health proposal accepted", u["id"], db.now()))
    log(c, u["workspace_id"], None, u["id"], "guideline", f"{b.ref} updated: exceptions {ex}"); c.commit()
    return {"ok": True}

class ExtractOut(BaseModel):
    text: str

@app.post("/api/extract")
async def extract(file: UploadFile = File(...), u=Depends(current_user)):
    data = await file.read()
    if len(data) > 8_000_000:
        raise HTTPException(400, "File is too large (limit 8 MB)")
    name = (file.filename or "").lower()
    try:
        if name.endswith(".docx"):
            import docx
            text = "\n\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
        elif name.endswith(".pdf"):
            from pypdf import PdfReader
            text = "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
        elif name.endswith((".txt", ".md", ".csv")):
            text = data.decode("utf-8", errors="replace")
        else:
            raise HTTPException(400, "Unsupported file type. Use .txt, .docx or .pdf")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Could not read this file. Try .txt, .docx or a text-based .pdf")
    if not text.strip():
        raise HTTPException(400, "No text found in the file (scanned PDFs are not supported yet)")
    return {"text": text[:20000], "filename": file.filename, "truncated": len(text) > 20000}

class PrivacyIn(BaseModel):
    text: str = Field(max_length=20000)

@app.post("/api/privacy-preview")
def privacy_preview(b: PrivacyIn, u=Depends(current_user)):
    red, mapping = privacy.redact(b.text)
    return {"redacted": red, "found": [{"token": k, "kind": k.strip("[]").rsplit("_", 1)[0]} for k in mapping]}

@app.get("/api/samples/image/{kind}")
def sample_image(kind: str, u=Depends(current_user), c=Depends(get_db)):
    if kind not in ("good", "bad"):
        raise HTTPException(404)
    cl = c.one("SELECT params FROM clauses WHERE workspace_id=? AND check_type='palette'", (u["workspace_id"],))
    pal = json.loads(cl["params"]).get("colors") or db.DEFAULT_PALETTE
    if len(pal) < 5:
        pal = db.DEFAULT_PALETTE
    a = c.one("SELECT data FROM assets WHERE workspace_id=? AND kind='logo' ORDER BY id DESC", (u["workspace_id"],))
    logo = a["data"] if a else vision.make_logo(pal)
    return Response(vision.make_sample(kind, pal, logo), media_type="image/png")

# ---------------- submissions ----------------
class SubIn(BaseModel):
    title: str = Field(max_length=200)
    purpose: str = Field("", max_length=300)
    channel: str = "Website"
    text: str = Field("", max_length=20000)
    colors: str = Field("", max_length=200)
    image_b64: Optional[str] = Field(None, max_length=8_000_000)
    image_kind: str = "design"
    image_alt: str = Field("", max_length=300)

def can_view(u, sub):
    return u["workspace_id"] == sub["workspace_id"] and (u["role"] in ("manager", "reviewer") or sub["author_id"] == u["id"])

def load_sub(c, sid, u):
    sub = c.one("SELECT s.*, a.name AS author FROM submissions s JOIN users a ON a.id=s.author_id WHERE s.id=?", (sid,))
    if not sub or sub["workspace_id"] != u["workspace_id"]:
        raise HTTPException(404, "Not found")
    if not can_view(u, sub):
        raise HTTPException(403, "You cannot view this submission")
    return sub

def need_owner(u, sub):
    if sub["author_id"] != u["id"]:
        raise HTTPException(403, "Only the author can change this content")

def latest_n(c, sid):
    return c.scalar("SELECT MAX(n) FROM versions WHERE submission_id=?", (sid,))

@app.post("/api/submissions")
def create_sub(b: SubIn, u=Depends(current_user), c=Depends(get_db)):
    if not b.title.strip():
        raise HTTPException(400, "Give the content a title")
    img = prep_image(b.image_b64, b.image_kind, b.image_alt)
    if not b.text.strip() and not img:
        raise HTTPException(400, "Add some text or an image to review")
    ch = b.channel if b.channel in CHANNELS else "Website"
    return {"id": make_submission(c, u, b.title.strip(), b.purpose.strip(), ch, b.text, b.colors, img)}

@app.get("/api/submissions")
def list_subs(u=Depends(current_user), c=Depends(get_db)):
    q = """SELECT s.*, a.name AS author,
           (SELECT MAX(n) FROM versions WHERE submission_id=s.id) AS version,
           (SELECT COUNT(*) FROM findings f WHERE f.submission_id=s.id AND f.version_n=(SELECT MAX(n) FROM versions WHERE submission_id=s.id) AND f.status='open') AS open_findings,
           (SELECT COUNT(*) FROM findings f WHERE f.submission_id=s.id AND f.version_n=(SELECT MAX(n) FROM versions WHERE submission_id=s.id)) AS total_findings
           FROM submissions s JOIN users a ON a.id=s.author_id WHERE s.workspace_id=?"""
    args = [u["workspace_id"]]
    if u["role"] == "creator":
        q += " AND s.author_id=?"; args.append(u["id"])
    return c.rows(q + " ORDER BY s.id DESC", args)

@app.get("/api/submissions/{sid}")
def get_sub(sid: int, version: Optional[int] = Query(None), u=Depends(current_user), c=Depends(get_db)):
    sub = load_sub(c, sid, u)
    vs = c.rows("SELECT v.n,v.note,v.created_at,v.ai_status,u.name AS by FROM versions v JOIN users u ON u.id=v.created_by WHERE submission_id=? ORDER BY n", (sid,))
    latest = vs[-1]["n"]; n = version or latest
    v = c.one("SELECT * FROM versions WHERE submission_id=? AND n=?", (sid, n))
    if not v:
        raise HTTPException(404, "No such version")
    v["has_image"] = bool(v.pop("image", None)); v.pop("image_mime", None)
    fs = c.rows('SELECT * FROM findings WHERE submission_id=? AND version_n=? ORDER BY CASE WHEN "start"<0 THEN 1 ELSE 0 END, "start", id', (sid, n))
    for f in fs:
        try:
            f["meta"] = json.loads(f.get("meta") or "{}")
        except Exception:
            f["meta"] = {}
    return {"submission": sub, "version": v, "latest": latest, "versions": vs, "findings": fs,
            "comments": c.rows("SELECT c.*,u.name,u.role FROM comments c JOIN users u ON u.id=c.user_id WHERE submission_id=? ORDER BY c.id", (sid,)),
            "audit": c.rows("SELECT a.*,u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE submission_id=? ORDER BY a.id DESC", (sid,))}

@app.get("/api/submissions/{sid}/image")
def get_image(sid: int, version: Optional[int] = Query(None), u=Depends(current_user), c=Depends(get_db)):
    load_sub(c, sid, u)
    v = c.one("SELECT image,image_mime FROM versions WHERE submission_id=? AND n=?", (sid, version or latest_n(c, sid)))
    if not v or not v["image"]:
        raise HTTPException(404, "No image")
    return Response(v["image"], media_type=v["image_mime"] or "image/png")

class VersionIn(BaseModel):
    text: str = Field("", max_length=20000)
    colors: str = Field("", max_length=200)
    note: str = Field("Edited by author", max_length=200)
    image_b64: Optional[str] = Field(None, max_length=8_000_000)
    image_kind: Optional[str] = None
    image_alt: Optional[str] = Field(None, max_length=300)

def prev_image(c, sid, n):
    v = c.one("SELECT image,image_mime,image_alt,image_kind FROM versions WHERE submission_id=? AND n=?", (sid, n))
    return {"bytes": v["image"], "mime": v["image_mime"], "alt": v["image_alt"] or "", "kind": v["image_kind"] or "design"} if v and v["image"] else None

def reset_status(c, sub, u, why):
    if sub["status"] != "Draft":
        c.execute("UPDATE submissions SET status='Draft' WHERE id=?", (sub["id"],))
        log(c, sub["workspace_id"], sub["id"], u["id"], "status", f"{sub['status']} -> Draft ({why})")

@app.post("/api/submissions/{sid}/version")
def new_version(sid: int, b: VersionIn, u=Depends(current_user), c=Depends(get_db)):
    sub = load_sub(c, sid, u); need_owner(u, sub)
    n = latest_n(c, sid); old = prev_image(c, sid, n)
    img = prep_image(b.image_b64, b.image_kind or (old or {}).get("kind", "design"), b.image_alt if b.image_alt is not None else (old or {}).get("alt", "")) if b.image_b64 else old
    if img and not b.image_b64:
        if b.image_alt is not None:
            img = dict(img, alt=b.image_alt.strip()[:300])
        if b.image_kind in ("design", "photo"):
            img = dict(img, kind=b.image_kind)
    if not b.text.strip() and not img:
        raise HTTPException(400, "Add some text or an image to review")
    run_version(c, sub, n + 1, b.text, b.colors, u["id"], b.note or "Edited by author", img); reset_status(c, sub, u, "new version"); c.commit()
    return {"version": n + 1}

class Decide(BaseModel):
    decision: str
    edited_text: Optional[str] = Field(None, max_length=2000)
    reason: Optional[str] = Field(None, max_length=500)

@app.post("/api/findings/{fid}/decide")
def decide(fid: int, b: Decide, u=Depends(current_user), c=Depends(get_db)):
    f = c.one("SELECT * FROM findings WHERE id=?", (fid,))
    if not f:
        raise HTTPException(404, "Finding not found")
    sub = load_sub(c, f["submission_id"], u); need_owner(u, sub)
    if f["version_n"] != latest_n(c, sub["id"]):
        raise HTTPException(400, "Only findings of the latest version can be decided")
    if sub["status"] == "Approved":
        raise HTTPException(400, "Approved content is locked. Create a new version to change it")
    if sub["status"] == "In Review":
        raise HTTPException(400, "Content in review cannot be changed. Wait for the reviewer or create a new version")
    if b.decision == "accept":
        c.execute("UPDATE findings SET status='accepted',decision_text=NULL,reason=NULL,decided_by=?,decided_at=? WHERE id=?", (u["id"], db.now(), fid))
    elif b.decision == "edit":
        if b.edited_text is None:
            raise HTTPException(400, "Provide your replacement text")
        c.execute("UPDATE findings SET status='edited',decision_text=?,decided_by=?,decided_at=? WHERE id=?", (b.edited_text, u["id"], db.now(), fid))
    elif b.decision == "dismiss":
        if not (b.reason or "").strip():
            raise HTTPException(400, "Please give a short reason for dismissing")
        c.execute("UPDATE findings SET status='dismissed',reason=?,decided_by=?,decided_at=? WHERE id=?", (b.reason.strip(), u["id"], db.now(), fid))
    elif b.decision == "reopen":
        c.execute("UPDATE findings SET status='open',reason=NULL,decision_text=NULL WHERE id=?", (fid,))
    else:
        raise HTTPException(400, "Unknown decision")
    log(c, sub["workspace_id"], sub["id"], u["id"], "decision", f"{b.decision} finding #{fid} ({f['source_ref']})" + (f": {b.reason}" if b.reason else "")); c.commit()
    return {"ok": True}

@app.post("/api/submissions/{sid}/apply")
def apply_decisions(sid: int, u=Depends(current_user), c=Depends(get_db)):
    sub = load_sub(c, sid, u); need_owner(u, sub)
    if sub["status"] in ("Approved", "In Review"):
        raise HTTPException(400, "This content is locked while it is in review or approved")
    n = latest_n(c, sid); v = c.one("SELECT * FROM versions WHERE submission_id=? AND n=?", (sid, n))
    fs = c.rows("""SELECT * FROM findings WHERE submission_id=? AND version_n=? AND status IN ('accepted','edited') AND "start">=0 ORDER BY "start" DESC""", (sid, n))
    text, last, applied = v["text"], 10 ** 9, 0
    for f in fs:
        if f["end"] > last:
            continue
        rep = f["decision_text"] if f["status"] == "edited" else f["suggestion"]
        text = text[:f["start"]] + (rep or "") + text[f["end"]:]
        last, applied = f["start"], applied + 1
    if not applied:
        raise HTTPException(400, "No accepted or edited suggestions to apply. Accept or edit a finding that changes the text first")
    text = re.sub(r"[ \t]{2,}", " ", text); text = re.sub(r" ([.,!?])", r"\1", text)
    run_version(c, sub, n + 1, text, v["colors"] or "", u["id"], f"Applied {applied} reviewed suggestion(s)", prev_image(c, sid, n))
    reset_status(c, sub, u, "new version"); c.commit()
    return {"version": n + 1, "applied": applied}

class StatusIn(BaseModel):
    action: str
    comment: str = Field("", max_length=2000)

@app.post("/api/submissions/{sid}/status")
def set_status(sid: int, b: StatusIn, u=Depends(current_user), c=Depends(get_db)):
    sub = load_sub(c, sid, u); n = latest_n(c, sid)
    if b.action == "submit":
        need_owner(u, sub)
        if sub["status"] not in ("Draft", "Changes Requested"):
            raise HTTPException(400, "This content has already been submitted")
        op = c.scalar("SELECT COUNT(*) FROM findings WHERE submission_id=? AND version_n=? AND status='open'", (sid, n))
        if op:
            raise HTTPException(400, f"{op} finding(s) still need your decision (accept, edit or dismiss) before review")
        new = "In Review"
    elif b.action in ("approve", "request_changes"):
        if u["role"] not in ("manager", "reviewer"):
            raise HTTPException(403, "Only a manager or reviewer can do this")
        if sub["author_id"] == u["id"]:
            raise HTTPException(403, "You cannot approve your own content")
        if sub["status"] != "In Review":
            raise HTTPException(400, "Content must be In Review")
        if b.action == "request_changes" and not b.comment.strip():
            raise HTTPException(400, "Please explain what must change")
        new = "Approved" if b.action == "approve" else "Changes Requested"
    else:
        raise HTTPException(400, "Unknown action")
    c.execute("UPDATE submissions SET status=? WHERE id=?", (new, sid))
    log(c, sub["workspace_id"], sid, u["id"], "status", f"{sub['status']} -> {new}")
    if b.comment.strip():
        c.insert("INSERT INTO comments(submission_id,user_id,body,created_at) VALUES(?,?,?,?)", (sid, u["id"], b.comment.strip(), db.now()))
    c.commit()
    return {"status": new}

class CommentIn(BaseModel):
    body: str = Field(max_length=2000)
    finding_id: Optional[int] = None

@app.post("/api/submissions/{sid}/comments")
def add_comment(sid: int, b: CommentIn, u=Depends(current_user), c=Depends(get_db)):
    sub = load_sub(c, sid, u)
    if not b.body.strip():
        raise HTTPException(400, "Write a comment first")
    c.insert("INSERT INTO comments(submission_id,finding_id,user_id,body,created_at) VALUES(?,?,?,?,?)", (sid, b.finding_id, u["id"], b.body.strip(), db.now()))
    log(c, sub["workspace_id"], sid, u["id"], "comment", b.body.strip()[:80]); c.commit()
    return {"ok": True}

@app.get("/api/submissions/{sid}/diff")
def diff(sid: int, a: int, b: int, u=Depends(current_user), c=Depends(get_db)):
    load_sub(c, sid, u)
    va = c.one("SELECT text FROM versions WHERE submission_id=? AND n=?", (sid, a)); vb = c.one("SELECT text FROM versions WHERE submission_id=? AND n=?", (sid, b))
    if not va or not vb:
        raise HTTPException(404, "No such version")
    A, B = re.findall(r"\s+|\S+", va["text"]), re.findall(r"\s+|\S+", vb["text"])
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, A, B, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": "".join(A[i1:i2])})
        else:
            if i2 > i1: ops.append({"op": "delete", "text": "".join(A[i1:i2])})
            if j2 > j1: ops.append({"op": "insert", "text": "".join(B[j1:j2])})
    return {"ops": ops}

# ---------------- insights and quality ----------------
@app.get("/api/insights")
def insights(u=Depends(current_user), c=Depends(get_db)):
    if u["role"] == "creator":
        raise HTTPException(403, "Insights are for managers and reviewers")
    ws = u["workspace_id"]; d = health.analyze(c, ws)
    d["categories"] = c.rows("SELECT f.category AS category, COUNT(*) AS n FROM findings f JOIN submissions s ON s.id=f.submission_id WHERE s.workspace_id=? GROUP BY f.category ORDER BY n DESC", (ws,))
    d["authors"] = c.rows("SELECT a.name AS name, COUNT(*) AS n FROM findings f JOIN submissions s ON s.id=f.submission_id JOIN users a ON a.id=s.author_id WHERE s.workspace_id=? GROUP BY a.name ORDER BY n DESC", (ws,))
    d["statuses"] = c.rows("SELECT status, COUNT(*) AS n FROM submissions WHERE workspace_id=? GROUP BY status", (ws,))
    return d

@app.get("/api/quality")
def quality_report(u=Depends(current_user), c=Depends(get_db)):
    ev = quality.get_eval(); live = quality.live_stats(c, u["workspace_id"]); table = []
    for r in ev["combined_rules"]:
        acc, dis, tot = live.get(r["rule"], (0, 0, 0))
        cal, prior = quality.calibrate(r["rule"], 0.9, acc, dis)
        table.append(dict(r, live_total=tot, live_accepted=acc, live_dismissed=dis, calibrated=cal, observed_acceptance=round(acc / (acc + dis), 2) if acc + dis else None))
    return {"eval": ev, "table": table, "settings": disclosure()}

@app.post("/api/quality/run")
def quality_run(u=Depends(current_user)):
    if u["role"] == "creator":
        raise HTTPException(403, "Only managers and reviewers can re-run the evaluation")
    return {"eval": quality.get_eval(force=True)}

@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))

# ---------------- demo data ----------------
SAMPLES = [
 ("Instagram: Monsoon Launch", "Announce our monsoon snack box to followers", "Social media",
  "Hey guys!! Our new Monsoon Box is HERE \U0001F327\U0001F389 Get the best snacks for rainy evenings. Our customers love it and the Starter box is only \u20b9599 a month \U0001F60B Order from Bright Box today!", "text: #1A1A1A, background: #F5F1E8"),
 ("Email: Annual Plan Offer", "Persuade existing members to switch to the annual plan", "Email",
  "Hello,\n\nSwitch to our annual plan today and save 20% on every box. We guarantee you will love it, and every snack is made with 100% natural ingredients. Click [here](https://example.com/annual) to upgrade. Delivery is FREE on all orders. Questions? Call 9876543210.\n\nThe BrightBox team",
  "text: #9A9A9A, background: #FFFFFF"),
 ("Instagram: Weekend Giveaway", "Run a giveaway to grow followers", "Social media",
  "Win a free snack pack this weekend! \U0001F381 Follow us, tag two friends and you are in! Winners announced Monday \U0001F389", ""),
]

def seed_demo():
    c = db.connect()
    try:
        ws = c.scalar("SELECT id FROM workspaces ORDER BY id LIMIT 1")
        if not ws or c.scalar("SELECT COUNT(*) FROM submissions WHERE workspace_id=?", (ws,)):
            return
        riya = c.one("SELECT * FROM users WHERE username='riya'"); riya["workspace"] = "BrightBox"
        ids = [make_submission(c, riya, *s, commit=False) for s in SAMPLES]
        bad = vision.make_sample("bad", db.DEFAULT_PALETTE, c.one("SELECT data FROM assets WHERE workspace_id=? AND kind='logo'", (ws,))["data"])
        nb, mime, _, _ = vision.normalize(bad, "design")
        ids.append(make_submission(c, riya, "Poster: Monsoon Sale (sample design)", "Promote the monsoon sale with a poster", "Social media", "", "", {"bytes": nb, "mime": mime, "alt": "", "kind": "design"}, commit=False))
        for sid in (ids[0], ids[2]):  # authors dismissed the emoji rule on social posts
            for f in c.rows("SELECT id FROM findings WHERE submission_id=? AND rule='emoji'", (sid,)):
                c.execute("UPDATE findings SET status='dismissed',reason='Emoji are normal on social media',decided_by=?,decided_at=? WHERE id=?", (riya["id"], db.now(), f["id"]))
        for f in c.rows("SELECT id FROM findings WHERE submission_id=?", (ids[1],)):
            c.execute("UPDATE findings SET status='accepted',decided_by=?,decided_at=? WHERE id=?", (riya["id"], db.now(), f["id"]))
        c.execute("UPDATE submissions SET status='In Review' WHERE id=?", (ids[1],))
        log(c, ws, ids[1], riya["id"], "status", "Draft -> In Review")
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()
