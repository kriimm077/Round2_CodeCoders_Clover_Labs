"""Storage layer. SQLite by default; PostgreSQL when DATABASE_URL is set. Every business table carries a workspace_id (tenant isolation)."""
import os, json, sqlite3, hashlib, secrets, datetime

DATABASE_URL = os.environ.get("DATABASE_URL", "")
PG = DATABASE_URL.startswith("postgres")
DB_PATH = os.environ.get("DB_PATH", "data/app.db")

def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _clean(r):
    if r is None:
        return None
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, memoryview):
            d[k] = bytes(v)
    return d

class Cur:
    def __init__(self, cur):
        self.cur = cur
    def fetchone(self):
        return _clean(self.cur.fetchone())
    def fetchall(self):
        return [_clean(r) for r in self.cur.fetchall()]

class Conn:
    def __init__(self):
        if PG:
            import psycopg2, psycopg2.extras
            self._ex = psycopg2.extras
            self.raw = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        else:
            os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
            self.raw = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
            self.raw.row_factory = sqlite3.Row
            self.raw.execute("PRAGMA journal_mode=WAL")

    def execute(self, sql, args=()):
        if PG:
            cur = self.raw.cursor(cursor_factory=self._ex.RealDictCursor)
            cur.execute(sql.replace("?", "%s"), tuple(args))
            return Cur(cur)
        return Cur(self.raw.execute(sql, tuple(args)))

    def insert(self, sql, args=()):
        if PG:
            cur = self.raw.cursor(cursor_factory=self._ex.RealDictCursor)
            cur.execute(sql.replace("?", "%s") + " RETURNING id", tuple(args))
            return cur.fetchone()["id"]
        return self.raw.execute(sql, tuple(args)).lastrowid

    def rows(self, sql, args=()):
        return self.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        return self.execute(sql, args).fetchone()

    def scalar(self, sql, args=()):
        r = self.execute(sql, args).fetchone()
        return list(r.values())[0] if r else None

    def commit(self):
        self.raw.commit()

    def rollback(self):
        try:
            self.raw.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass

def connect():
    return Conn()

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces(id {ID}, name TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS users(id {ID}, workspace_id INTEGER, username TEXT UNIQUE, name TEXT, role TEXT, pw TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS clauses(id {ID}, workspace_id INTEGER, ref TEXT, category TEXT, title TEXT, text TEXT, check_type TEXT, params TEXT, severity TEXT);
CREATE TABLE IF NOT EXISTS terms(id {ID}, workspace_id INTEGER, banned TEXT, preferred TEXT, clause_ref TEXT);
CREATE TABLE IF NOT EXISTS facts(id {ID}, workspace_id INTEGER, ref TEXT, kind TEXT, label TEXT, keywords TEXT, value TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS assets(id {ID}, workspace_id INTEGER, kind TEXT, name TEXT, data {BLOB}, mime TEXT);
CREATE TABLE IF NOT EXISTS submissions(id {ID}, workspace_id INTEGER, title TEXT, purpose TEXT, channel TEXT, author_id INTEGER, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS versions(id {ID}, submission_id INTEGER, n INTEGER, text TEXT, colors TEXT, note TEXT, ai_status TEXT, created_by INTEGER, created_at TEXT, image {BLOB}, image_mime TEXT, image_alt TEXT, image_kind TEXT);
CREATE TABLE IF NOT EXISTS findings(id {ID}, submission_id INTEGER, version_n INTEGER, rule TEXT, category TEXT, severity TEXT, "start" INTEGER, "end" INTEGER, original TEXT, explanation TEXT,
  source_ref TEXT, source_title TEXT, source_text TEXT, confidence REAL, suggestion TEXT, origin TEXT, status TEXT DEFAULT 'open', decision_text TEXT, reason TEXT, decided_by INTEGER, decided_at TEXT, meta TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS comments(id {ID}, submission_id INTEGER, finding_id INTEGER, user_id INTEGER, body TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit(id {ID}, workspace_id INTEGER, submission_id INTEGER, user_id INTEGER, action TEXT, detail TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS clause_history(id {ID}, workspace_id INTEGER, ref TEXT, old_text TEXT, new_text TEXT, old_params TEXT, new_params TEXT, reason TEXT, changed_by INTEGER, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_sub_ws ON submissions(workspace_id);
CREATE INDEX IF NOT EXISTS ix_find_sub ON findings(submission_id, version_n);
CREATE INDEX IF NOT EXISTS ix_ver_sub ON versions(submission_id, n);
"""

def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 50000).hex()

def check_pw(pw, stored):
    return hash_pw(pw, stored.split("$")[0]) == stored

DEFAULT_PALETTE = ["#1F6F5C", "#F4B942", "#FFFFFF", "#1A1A1A", "#F5F1E8"]

def clause_defs(brand, palette):
    pal = ", ".join(palette) if palette else "(no palette set yet - ask your manager to add the brand colours)"
    return [
     ("TONE-1", "Tone", "Friendly, calm voice", f"{brand} speaks in a warm, calm and professional voice. Use at most one exclamation mark per piece of content.", "exclaim", {"max": 1}, "low"),
     ("TONE-2", "Tone", "No shouting", "Never write whole words in ALL CAPS for emphasis. Acronyms such as FAQ or PDF are allowed.", "caps", {"allow": ["FAQ", "PDF", "USA", "GST", "UPI", "FSSAI", "RBI", "KYC", "EMI", "ATM", "DICGC", "PAN", "SMS", "CEO", "USB", "LED", "ISO", "UPI", "ID", "OTP", "AI", "API"]}, "low"),
     ("TONE-3", "Tone", "No slang or filler", "Avoid slang and casual filler words. Write in clear, plain language.", "slang", {"map": {"gonna": "going to", "wanna": "want to", "lol": "", "awesome": "great", "guys": "everyone", "stuff": "products", "super": "very"}}, "low"),
     ("TONE-4", "Tone", "Emoji use", f"Do not use emoji in {brand} content.", "emoji", {}, "low"),
     ("TERM-1", "Terminology", "Approved terminology", f"Always use the approved {brand} terms (see the terminology list). Product and company names must be written exactly as approved.", "term", {}, "medium"),
     ("CLAIM-1", "Claims", "No unsupported claims", "Do not use superlatives, absolute guarantees or health claims (best, #1, guaranteed, clinically proven, cure, risk-free). Only claims on the approved-claims list may be used.", "claim", {}, "high"),
     ("PRIV-1", "Privacy", "No personal contact details", "Do not publish personal email addresses, phone numbers or ID numbers in content. Use the official contact channels instead.", "pii", {"allow": []}, "medium"),
     ("ACC-1", "Accessibility", "Images need alt text", "Every image must have meaningful alternative text describing what it shows.", "alt", {}, "high"),
     ("ACC-2", "Accessibility", "Descriptive link text", "Link text must describe the destination. Avoid 'click here' and 'read more'.", "link", {}, "medium"),
     ("ACC-3", "Accessibility", "Short sentences", "Keep sentences to 30 words or fewer so the content is easy to read.", "longsent", {"max": 30}, "low"),
     ("ACC-4", "Accessibility", "Colour contrast", "Text and background colours must have a contrast ratio of at least 4.5:1 (WCAG AA).", "contrast", {"min": 4.5}, "high"),
     ("VIS-1", "Visual", "Brand colour palette", f"Only the approved palette may be used: {pal}.", "palette", {"colors": palette}, "medium"),
     ("VIS-2", "Visual", "Logo usage", f"The {brand} logo must appear on designs at no less than 8% of the image width and keep clear space around it equal to a quarter of the logo width. Do not stretch or recolour it.", "logo", {"min_width_pct": 0.08, "clear_space": 0.25}, "medium"),
     ("VIS-3", "Visual", "Image quality and format", "Images must be at least 600 px wide. Social media images must use a 1:1, 4:5 or 1.91:1 aspect ratio.", "imgquality", {"min_width": 600, "social_ratios": [1.0, 0.8, 1.91]}, "medium"),
     ("MSG-1", "Messaging", "Brand story", f"{brand} messaging should focus on honest pricing, quality and convenience, and should never criticise competitors.", "text", {}, "medium"),
    ]

BRIGHTBOX = dict(name="BrightBox", palette=DEFAULT_PALETTE,
    terms=[("customers", "members"), ("customer", "member"), ("Bright Box", "BrightBox"), ("Brightbox", "BrightBox"), ("snack pack", "snack box"), ("cheap", "affordable"), ("junk food", "snacks")],
    facts=[("FACT-1", "fact", "Starter box price", ["starter", "box"], "\u20b9499", "Price list, Oct 2026"),
           ("FACT-2", "fact", "Free delivery threshold", ["free", "delivery"], "\u20b9799", "Delivery policy v3"),
           ("FACT-3", "fact", "Annual plan saving", ["annual", "sav"], "15%", "Pricing sheet, Oct 2026"),
           ("FACT-4", "fact", "Year founded", ["founded"], "2021", "Company profile"),
           ("CLAIM-A1", "claim", "Approved claim: natural ingredients", [], "100% natural ingredients", "Lab certificate LC-2026-14"),
           ("CLAIM-A2", "claim", "Approved claim: no added sugar", [], "no added sugar", "Lab certificate LC-2026-15")])
NORTHWIND = dict(name="Northwind Bank", palette=["#0B3C5D", "#328CC1", "#FFFFFF", "#1A1A1A", "#F2F6FA"],
    terms=[("clients", "customers"), ("A/c", "account"), ("cheap loan", "low-cost loan")],
    facts=[("FACT-1", "fact", "Savings account interest rate", ["savings", "interest"], "4.5%", "Rate card, Nov 2026"),
           ("FACT-2", "fact", "Minimum balance", ["minimum", "balance"], "\u20b92,000", "Account terms v5"),
           ("CLAIM-A1", "claim", "Approved claim: deposit insurance", [], "insured up to \u20b95 lakh", "DICGC notice")])
USERS_BRIGHT = [("riya", "Riya Shah", "creator"), ("manav", "Manav Desai", "manager"), ("sana", "Sana Iyer", "reviewer")]
USERS_NORTH = [("nora", "Nora Mehta", "manager"), ("nikhil", "Nikhil Rao", "creator")]

def seed_kb(c, ws, spec, with_content=True):
    for ref, cat, t, tx, ct, p, sv in clause_defs(spec["name"], spec["palette"]):
        c.insert("INSERT INTO clauses(workspace_id,ref,category,title,text,check_type,params,severity) VALUES(?,?,?,?,?,?,?,?)", (ws, ref, cat, t, tx, ct, json.dumps(p), sv))
    if with_content:
        for b, p in spec["terms"]:
            c.insert("INSERT INTO terms(workspace_id,banned,preferred,clause_ref) VALUES(?,?,?,'TERM-1')", (ws, b, p))
        for ref, k, l, kw, v, s in spec["facts"]:
            c.insert("INSERT INTO facts(workspace_id,ref,kind,label,keywords,value,source) VALUES(?,?,?,?,?,?,?)", (ws, ref, k, l, json.dumps(kw), v, s))

def default_kb(spec=BRIGHTBOX):
    """In-memory knowledge base used by the evaluation harness (independent of any workspace edits)."""
    return {"clauses": [dict(ref=r, category=cat, title=t, text=tx, check_type=ct, params=json.dumps(p), severity=sv) for r, cat, t, tx, ct, p, sv in clause_defs(spec["name"], spec["palette"])],
            "terms": [dict(banned=b, preferred=p, clause_ref="TERM-1") for b, p in spec["terms"]],
            "facts": [dict(ref=r, kind=k, label=l, keywords=json.dumps(kw), value=v, source=s) for r, k, l, kw, v, s in spec["facts"]]}

def init():
    """Create tables; seed two demo workspaces on a fresh database. Returns True if seeded."""
    c = connect()
    try:
        for stmt in SCHEMA.format(ID="SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY", BLOB="BYTEA" if PG else "BLOB").split(";"):
            if stmt.strip():
                c.execute(stmt)
        c.commit()
        if c.scalar("SELECT COUNT(*) FROM workspaces"):
            return False
        from . import vision
        for spec, users in ((BRIGHTBOX, USERS_BRIGHT), (NORTHWIND, USERS_NORTH)):
            ws = c.insert("INSERT INTO workspaces(name,created_at) VALUES(?,?)", (spec["name"], now()))
            for u, n, r in users:
                c.insert("INSERT INTO users(workspace_id,username,name,role,pw) VALUES(?,?,?,?,?)", (ws, u, n, r, hash_pw("demo123")))
            seed_kb(c, ws, spec)
            logo = vision.make_logo(spec["palette"])
            c.insert("INSERT INTO assets(workspace_id,kind,name,data,mime) VALUES(?,?,?,?,?)", (ws, "logo", spec["name"] + " logo", logo, "image/png"))
        c.commit()
        return True
    finally:
        c.close()
