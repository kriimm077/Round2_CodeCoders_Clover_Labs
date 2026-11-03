import json, re, io, difflib, os
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import db, engine

app = FastAPI(title="BrandLens", description="Explainable brand-governance review with human control")
HERE = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

def rows(c, sql, a=()):
    return [dict(r) for r in c.execute(sql, a).fetchall()]

def one(c, sql, a=()):
    r = c.execute(sql, a).fetchone()
    return dict(r) if r else None

def get_kb(c):
    return {"clauses": rows(c, "SELECT * FROM clauses"), "terms": rows(c, "SELECT * FROM terms"), "facts": rows(c, "SELECT * FROM facts")}

def log(c, sid, uid, action, detail=""):
    c.execute("INSERT INTO audit(submission_id,user_id,action,detail) VALUES(?,?,?,?)", (sid, uid, action, detail))

def auth(authorization):
    token = (authorization or "").replace("Bearer ", "").strip()
    c = db.connect()
    u = one(c, "SELECT u.id,u.username,u.name,u.role FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,))
    c.close()
    if not u:
        raise HTTPException(401, "Please log in")
    return u

def run_version(c, sid, n, text, colors, uid, note):
    sub = one(c, "SELECT * FROM submissions WHERE id=?", (sid,))
    found, ai_status = engine.review(text, colors, sub["purpose"], sub["channel"], get_kb(c))
    c.execute("INSERT INTO versions(submission_id,n,text,colors,note,ai_status,created_by) VALUES(?,?,?,?,?,?,?)", (sid, n, text, colors, note, ai_status, uid))
    prev = rows(c, "SELECT * FROM findings WHERE submission_id=? AND version_n=? AND status='dismissed'", (sid, n - 1)) if n > 1 else []
    for f in found:
        st, reason, by = "open", None, None
        for p in prev:  # carry over earlier human dismissals of the same issue
            if p["source_ref"] == f["source_ref"] and p["original"] == f["original"]:
                st, reason, by = "dismissed", p["reason"], p["decided_by"]
        c.execute("""INSERT INTO findings(submission_id,version_n,rule,category,severity,start,end,original,explanation,source_ref,source_title,source_text,confidence,suggestion,origin,status,reason,decided_by)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (sid, n, f["rule"], f["category"], f["severity"], f["start"], f["end"], f["original"], f["explanation"], f["source_ref"], f["source_title"],
                   f["source_text"], f["confidence"], f["suggestion"], f["origin"], st, reason, by))
    log(c, sid, uid, "review", f"Version {n} reviewed: {len(found)} finding(s). {ai_status}")
    return len(found)

def make_submission(c, uid, title, purpose, channel, text, colors):
    cur = c.execute("INSERT INTO submissions(title,purpose,channel,author_id,status) VALUES(?,?,?,?, 'Draft')", (title, purpose, channel, uid))
    sid = cur.lastrowid
    log(c, sid, uid, "created", title)
    run_version(c, sid, 1, text, colors, uid, "Initial submission")
    c.commit()
    return sid

def can_view(u, sub):
    return u["role"] in ("manager", "reviewer") or sub["author_id"] == u["id"]

class Login(BaseModel):
    username: str
    password: str

@app.post("/api/login")
def login(b: Login):
    import secrets
    c = db.connect()
    u = one(c, "SELECT * FROM users WHERE username=?", (b.username.strip().lower(),))
    if not u or not db.check_pw(b.password, u["pw"]):
        raise HTTPException(401, "Wrong username or password")
    tok = secrets.token_urlsafe(24)
    c.execute("INSERT INTO sessions VALUES(?,?)", (tok, u["id"])); c.commit(); c.close()
    return {"token": tok, "user": {k: u[k] for k in ("id", "username", "name", "role")}}

@app.get("/api/me")
def me(authorization: Optional[str] = Header(None)):
    return auth(authorization)

@app.get("/api/disclosure")
def disclosure():
    on = bool(os.environ.get("ANTHROPIC_API_KEY"))
    return {"ai_enabled": on, "model": os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5") if on else None}

@app.get("/api/health")
def health():
    return {"ok": True}

# ---------- knowledge base ----------
@app.get("/api/kb")
def kb(authorization: Optional[str] = Header(None)):
    auth(authorization)
    c = db.connect(); k = get_kb(c); c.close()
    return k

class TermIn(BaseModel):
    banned: str
    preferred: str

class FactIn(BaseModel):
    label: str
    keywords: str = ""
    value: str
    source: str = ""
    kind: str = "fact"

class ClauseIn(BaseModel):
    category: str = "Messaging"
    title: str
    text: str

class ImportIn(BaseModel):
    text: str

def need_manager(u):
    if u["role"] != "manager":
        raise HTTPException(403, "Only a brand manager can change the knowledge base")

@app.post("/api/kb/term")
def add_term(b: TermIn, authorization: Optional[str] = Header(None)):
    u = auth(authorization); need_manager(u)
    if not b.banned.strip() or not b.preferred.strip():
        raise HTTPException(400, "Both fields are required")
    c = db.connect(); c.execute("INSERT INTO terms(banned,preferred,clause_ref) VALUES(?,?,'TERM-1')", (b.banned.strip(), b.preferred.strip())); log(c, None, u["id"], "kb", f"term {b.banned}->{b.preferred}"); c.commit(); c.close()
    return {"ok": True}

@app.post("/api/kb/fact")
def add_fact(b: FactIn, authorization: Optional[str] = Header(None)):
    u = auth(authorization); need_manager(u)
    c = db.connect()
    kind = "claim" if b.kind == "claim" else "fact"
    n = c.execute("SELECT COUNT(*) FROM facts WHERE kind=?", (kind,)).fetchone()[0] + 1
    ref = ("CLAIM-A" if kind == "claim" else "FACT-") + str(n + (10 if kind == "fact" else 0))
    kws = [k.strip() for k in b.keywords.split(",") if k.strip()]
    c.execute("INSERT INTO facts(ref,kind,label,keywords,value,source) VALUES(?,?,?,?,?,?)", (ref, kind, b.label, json.dumps(kws), b.value, b.source))
    log(c, None, u["id"], "kb", f"{kind} {b.label}"); c.commit(); c.close()
    return {"ok": True}

@app.post("/api/kb/clause")
def add_clause(b: ClauseIn, authorization: Optional[str] = Header(None)):
    u = auth(authorization); need_manager(u)
    c = db.connect()
    n = c.execute("SELECT COUNT(*) FROM clauses").fetchone()[0] + 1
    c.execute("INSERT INTO clauses VALUES(?,?,?,?, 'text','{}','medium')", (f"G-{n}", b.category, b.title, b.text))
    c.commit(); c.close()
    return {"ok": True}

@app.post("/api/kb/import")
def import_guidelines(b: ImportIn, authorization: Optional[str] = Header(None)):
    u = auth(authorization); need_manager(u)
    parts = [p.strip() for p in re.split(r"\n\s*\n", b.text) if len(p.strip()) > 20]
    c = db.connect(); n0 = c.execute("SELECT COUNT(*) FROM clauses").fetchone()[0]
    for i, p in enumerate(parts):
        title = p.split("\n")[0][:60] if len(p.split("\n")[0]) < 70 and "\n" in p else p[:50] + "..."
        c.execute("INSERT INTO clauses VALUES(?,?,?,?, 'text','{}','medium')", (f"G-{n0 + i + 1}", "Imported", title, p))
    log(c, None, u["id"], "kb", f"imported {len(parts)} clauses"); c.commit(); c.close()
    return {"imported": len(parts)}

@app.delete("/api/kb/{kind}/{key}")
def del_kb(kind: str, key: str, authorization: Optional[str] = Header(None)):
    u = auth(authorization); need_manager(u)
    c = db.connect()
    if kind == "term":
        c.execute("DELETE FROM terms WHERE id=?", (key,))
    elif kind == "fact":
        c.execute("DELETE FROM facts WHERE id=?", (key,))
    elif kind == "clause":
        c.execute("DELETE FROM clauses WHERE ref=? AND check_type='text'", (key,))
    else:
        raise HTTPException(404)
    c.commit(); c.close()
    return {"ok": True}

@app.post("/api/extract")
async def extract(file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    auth(authorization)
    data = await file.read()
    name = (file.filename or "").lower()
    try:
        if name.endswith(".docx"):
            import docx
            text = "\n\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
        elif name.endswith(".pdf"):
            from pypdf import PdfReader
            text = "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
        else:
            text = data.decode("utf-8", errors="replace")
    except Exception:
        raise HTTPException(400, "Could not read this file. Try .txt, .docx or a text-based .pdf")
    if not text.strip():
        raise HTTPException(400, "No text found in the file (scanned PDFs are not supported yet)")
    return {"text": text[:20000], "filename": file.filename}

# ---------- submissions ----------
class SubIn(BaseModel):
    title: str
    purpose: str
    channel: str = "Website"
    text: str
    colors: str = ""

@app.post("/api/submissions")
def create_sub(b: SubIn, authorization: Optional[str] = Header(None)):
    u = auth(authorization)
    if not b.title.strip() or not b.text.strip():
        raise HTTPException(400, "Title and content are required")
    c = db.connect(); sid = make_submission(c, u["id"], b.title.strip(), b.purpose.strip(), b.channel, b.text, b.colors); c.close()
    return {"id": sid}

@app.get("/api/submissions")
def list_subs(authorization: Optional[str] = Header(None)):
    u = auth(authorization)
    c = db.connect()
    q = """SELECT s.*, a.name AS author,
           (SELECT MAX(n) FROM versions WHERE submission_id=s.id) AS version,
           (SELECT COUNT(*) FROM findings f WHERE f.submission_id=s.id AND f.version_n=(SELECT MAX(n) FROM versions WHERE submission_id=s.id) AND f.status='open') AS open_findings,
           (SELECT COUNT(*) FROM findings f WHERE f.submission_id=s.id AND f.version_n=(SELECT MAX(n) FROM versions WHERE submission_id=s.id)) AS total_findings
           FROM submissions s JOIN users a ON a.id=s.author_id"""
    r = rows(c, q + (" WHERE s.author_id=?" if u["role"] == "creator" else "") + " ORDER BY s.id DESC", (u["id"],) if u["role"] == "creator" else ())
    c.close()
    return r

def load_sub(c, sid, u):
    sub = one(c, "SELECT s.*, a.name AS author FROM submissions s JOIN users a ON a.id=s.author_id WHERE s.id=?", (sid,))
    if not sub:
        raise HTTPException(404, "Not found")
    if not can_view(u, sub):
        raise HTTPException(403, "You cannot view this submission")
    return sub

@app.get("/api/submissions/{sid}")
def get_sub(sid: int, version: Optional[int] = Query(None), authorization: Optional[str] = Header(None)):
    u = auth(authorization); c = db.connect()
    sub = load_sub(c, sid, u)
    vs = rows(c, "SELECT v.n,v.note,v.created_at,v.ai_status,u.name AS by FROM versions v JOIN users u ON u.id=v.created_by WHERE submission_id=? ORDER BY n", (sid,))
    latest = vs[-1]["n"]; n = version or latest
    v = one(c, "SELECT * FROM versions WHERE submission_id=? AND n=?", (sid, n))
    if not v:
        raise HTTPException(404, "No such version")
    out = {"submission": sub, "version": v, "latest": latest, "versions": vs,
           "findings": rows(c, "SELECT * FROM findings WHERE submission_id=? AND version_n=? ORDER BY CASE WHEN start<0 THEN 1 ELSE 0 END, start", (sid, n)),
           "comments": rows(c, "SELECT c.*,u.name,u.role FROM comments c JOIN users u ON u.id=c.user_id WHERE submission_id=? ORDER BY c.id", (sid,)),
           "audit": rows(c, "SELECT a.*,u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE submission_id=? ORDER BY a.id DESC", (sid,))}
    c.close()
    return out

class VersionIn(BaseModel):
    text: str
    colors: str = ""
    note: str = "Edited by author"

def need_owner(u, sub):
    if sub["author_id"] != u["id"]:
        raise HTTPException(403, "Only the author can change this content")

def reset_status(c, sub, u, why):
    if sub["status"] != "Draft":
        c.execute("UPDATE submissions SET status='Draft' WHERE id=?", (sub["id"],))
        log(c, sub["id"], u["id"], "status", f"{sub['status']} -> Draft ({why})")

@app.post("/api/submissions/{sid}/version")
def new_version(sid: int, b: VersionIn, authorization: Optional[str] = Header(None)):
    u = auth(authorization); c = db.connect(); sub = load_sub(c, sid, u); need_owner(u, sub)
    n = c.execute("SELECT MAX(n) FROM versions WHERE submission_id=?", (sid,)).fetchone()[0] + 1
    run_version(c, sid, n, b.text, b.colors, u["id"], b.note); reset_status(c, sub, u, "new version"); c.commit(); c.close()
    return {"version": n}

class Decide(BaseModel):
    decision: str
    edited_text: Optional[str] = None
    reason: Optional[str] = None

@app.post("/api/findings/{fid}/decide")
def decide(fid: int, b: Decide, authorization: Optional[str] = Header(None)):
    u = auth(authorization); c = db.connect()
    f = one(c, "SELECT * FROM findings WHERE id=?", (fid,))
    if not f:
        raise HTTPException(404)
    sub = load_sub(c, f["submission_id"], u); need_owner(u, sub)
    latest = c.execute("SELECT MAX(n) FROM versions WHERE submission_id=?", (sub["id"],)).fetchone()[0]
    if f["version_n"] != latest:
        raise HTTPException(400, "Only findings of the latest version can be decided")
    if sub["status"] == "Approved":
        raise HTTPException(400, "Approved content is locked. Create a new version to change it")
    if b.decision == "accept":
        c.execute("UPDATE findings SET status='accepted',decision_text=NULL,reason=NULL,decided_by=?,decided_at=CURRENT_TIMESTAMP WHERE id=?", (u["id"], fid))
    elif b.decision == "edit":
        if b.edited_text is None:
            raise HTTPException(400, "Provide your replacement text")
        c.execute("UPDATE findings SET status='edited',decision_text=?,decided_by=?,decided_at=CURRENT_TIMESTAMP WHERE id=?", (b.edited_text, u["id"], fid))
    elif b.decision == "dismiss":
        if not (b.reason or "").strip():
            raise HTTPException(400, "Please give a short reason for dismissing")
        c.execute("UPDATE findings SET status='dismissed',reason=?,decided_by=?,decided_at=CURRENT_TIMESTAMP WHERE id=?", (b.reason.strip(), u["id"], fid))
    elif b.decision == "reopen":
        c.execute("UPDATE findings SET status='open',reason=NULL,decision_text=NULL WHERE id=?", (fid,))
    else:
        raise HTTPException(400, "Unknown decision")
    log(c, sub["id"], u["id"], "decision", f"{b.decision} finding #{fid} ({f['source_ref']})" + (f": {b.reason}" if b.reason else ""))
    c.commit(); c.close()
    return {"ok": True}

@app.post("/api/submissions/{sid}/apply")
def apply_decisions(sid: int, authorization: Optional[str] = Header(None)):
    u = auth(authorization); c = db.connect(); sub = load_sub(c, sid, u); need_owner(u, sub)
    n = c.execute("SELECT MAX(n) FROM versions WHERE submission_id=?", (sid,)).fetchone()[0]
    v = one(c, "SELECT * FROM versions WHERE submission_id=? AND n=?", (sid, n))
    fs = rows(c, "SELECT * FROM findings WHERE submission_id=? AND version_n=? AND status IN ('accepted','edited') AND start>=0 ORDER BY start DESC", (sid, n))
    text, last, applied = v["text"], 10 ** 9, 0
    for f in fs:
        if f["end"] > last:
            continue
        rep = f["decision_text"] if f["status"] == "edited" else f["suggestion"]
        text = text[:f["start"]] + rep + text[f["end"]:]
        last, applied = f["start"], applied + 1
    if not applied:
        raise HTTPException(400, "No accepted or edited suggestions to apply")
    text = re.sub(r"[ \t]{2,}", " ", text); text = re.sub(r" ([.,!?])", r"\1", text)
    run_version(c, sid, n + 1, text, v["colors"], u["id"], f"Applied {applied} reviewed suggestion(s)")
    reset_status(c, sub, u, "new version"); c.commit(); c.close()
    return {"version": n + 1, "applied": applied}

class StatusIn(BaseModel):
    action: str
    comment: str = ""

@app.post("/api/submissions/{sid}/status")
def set_status(sid: int, b: StatusIn, authorization: Optional[str] = Header(None)):
    u = auth(authorization); c = db.connect(); sub = load_sub(c, sid, u)
    n = c.execute("SELECT MAX(n) FROM versions WHERE submission_id=?", (sid,)).fetchone()[0]
    if b.action == "submit":
        need_owner(u, sub)
        if sub["status"] not in ("Draft", "Changes Requested"):
            raise HTTPException(400, "Already submitted")
        op = c.execute("SELECT COUNT(*) FROM findings WHERE submission_id=? AND version_n=? AND status='open'", (sid, n)).fetchone()[0]
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
    log(c, sid, u["id"], "status", f"{sub['status']} -> {new}")
    if b.comment.strip():
        c.execute("INSERT INTO comments(submission_id,user_id,body) VALUES(?,?,?)", (sid, u["id"], b.comment.strip()))
    c.commit(); c.close()
    return {"status": new}

class CommentIn(BaseModel):
    body: str
    finding_id: Optional[int] = None

@app.post("/api/submissions/{sid}/comments")
def add_comment(sid: int, b: CommentIn, authorization: Optional[str] = Header(None)):
    u = auth(authorization); c = db.connect(); load_sub(c, sid, u)
    if not b.body.strip():
        raise HTTPException(400, "Write a comment first")
    c.execute("INSERT INTO comments(submission_id,finding_id,user_id,body) VALUES(?,?,?,?)", (sid, b.finding_id, u["id"], b.body.strip()))
    log(c, sid, u["id"], "comment", b.body.strip()[:80]); c.commit(); c.close()
    return {"ok": True}

@app.get("/api/submissions/{sid}/diff")
def diff(sid: int, a: int, b: int, authorization: Optional[str] = Header(None)):
    u = auth(authorization); c = db.connect(); load_sub(c, sid, u)
    va = one(c, "SELECT text FROM versions WHERE submission_id=? AND n=?", (sid, a)); vb = one(c, "SELECT text FROM versions WHERE submission_id=? AND n=?", (sid, b)); c.close()
    if not va or not vb:
        raise HTTPException(404)
    A, B = re.findall(r"\s+|\S+", va["text"]), re.findall(r"\s+|\S+", vb["text"])
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, A, B, autojunk=False).get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": "".join(A[i1:i2])})
        else:
            if i2 > i1: ops.append({"op": "delete", "text": "".join(A[i1:i2])})
            if j2 > j1: ops.append({"op": "insert", "text": "".join(B[j1:j2])})
    return {"ops": ops}

@app.get("/api/insights")
def insights(authorization: Optional[str] = Header(None)):
    u = auth(authorization)
    if u["role"] == "creator":
        raise HTTPException(403, "Insights are for managers and reviewers")
    c = db.connect()
    per = rows(c, """SELECT source_ref AS ref, MAX(source_title) AS title, MAX(category) AS category, COUNT(*) AS total,
                     SUM(status IN ('accepted','edited')) AS accepted, SUM(status='dismissed') AS dismissed, SUM(status='open') AS open
                     FROM findings GROUP BY source_ref ORDER BY total DESC""")
    for p in per:
        decided = (p["accepted"] or 0) + (p["dismissed"] or 0)
        p["dismiss_rate"] = round((p["dismissed"] or 0) / decided, 2) if decided else 0
        p["unclear"] = bool(p["total"] >= 3 and decided >= 2 and p["dismiss_rate"] >= 0.5)
        reasons = [r["reason"] for r in rows(c, "SELECT DISTINCT reason FROM findings WHERE source_ref=? AND status='dismissed' AND reason IS NOT NULL LIMIT 3", (p["ref"],))]
        p["reasons"] = reasons
    cats = rows(c, "SELECT category, COUNT(*) AS n FROM findings GROUP BY category ORDER BY n DESC")
    authors = rows(c, """SELECT a.name, COUNT(*) AS n FROM findings f JOIN submissions s ON s.id=f.submission_id JOIN users a ON a.id=s.author_id GROUP BY a.name ORDER BY n DESC""")
    stat = rows(c, "SELECT status, COUNT(*) AS n FROM submissions GROUP BY status")
    c.close()
    return {"rules": per, "categories": cats, "authors": authors, "statuses": stat}

@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))

SAMPLES = [
 ("Instagram: Monsoon Launch", "Announce our monsoon snack box to followers", "Social media",
  "Hey guys!! Our new Monsoon Box is HERE \U0001F327\U0001F389 Get the best snacks for rainy evenings. Our customers love it and the Starter box is only \u20b9599 a month \U0001F60B Order from Bright Box today!", "text: #1A1A1A, background: #F5F1E8"),
 ("Email: Annual Plan Offer", "Persuade existing members to switch to the annual plan", "Email",
  "Hello,\n\nSwitch to our annual plan today and save 20% on every box. We guarantee you will love it, and every snack is made with 100% natural ingredients. Click [here](https://example.com/annual) to upgrade. Delivery is FREE on all orders.\n\nThe BrightBox team",
  "text: #9A9A9A, background: #FFFFFF"),
 ("Instagram: Weekend Giveaway", "Run a giveaway to grow followers", "Social media",
  "Win a free snack pack this weekend! \U0001F381 Follow us, tag two friends and you are in! Winners announced Monday \U0001F389", ""),
]

@app.on_event("startup")
def startup():
    if db.init():
        c = db.connect()
        uid = one(c, "SELECT id FROM users WHERE username='riya'")["id"]; mid = one(c, "SELECT id FROM users WHERE username='manav'")["id"]
        ids = [make_submission(c, uid, *s) for s in SAMPLES]
        for sid in (ids[0], ids[2]):  # authors dismissed the emoji rule on social posts -> shows up as an unclear guideline
            for f in rows(c, "SELECT id FROM findings WHERE submission_id=? AND rule='emoji'", (sid,)):
                c.execute("UPDATE findings SET status='dismissed',reason='Emoji are normal on social media',decided_by=? WHERE id=?", (uid, f["id"]))
        for f in rows(c, "SELECT id,suggestion FROM findings WHERE submission_id=?", (ids[1],)):
            c.execute("UPDATE findings SET status='accepted',decided_by=? WHERE id=?", (uid, f["id"]))
        c.execute("UPDATE submissions SET status='In Review' WHERE id=?", (ids[1],))
        log(c, ids[1], uid, "status", "Draft -> In Review")
        c.commit(); c.close()
