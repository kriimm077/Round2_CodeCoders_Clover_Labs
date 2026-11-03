"""SQLite storage + seed data for the fictional brand 'BrightBox'."""
import sqlite3, os, json, hashlib, secrets

DB_PATH = os.environ.get("DB_PATH", "data/app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, name TEXT, role TEXT, pw TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER);
CREATE TABLE IF NOT EXISTS clauses(ref TEXT PRIMARY KEY, category TEXT, title TEXT, text TEXT, check_type TEXT, params TEXT, severity TEXT);
CREATE TABLE IF NOT EXISTS terms(id INTEGER PRIMARY KEY, banned TEXT, preferred TEXT, clause_ref TEXT);
CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, ref TEXT, kind TEXT, label TEXT, keywords TEXT, value TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY, title TEXT, purpose TEXT, channel TEXT, author_id INTEGER, status TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY, submission_id INTEGER, n INTEGER, text TEXT, colors TEXT, note TEXT, ai_status TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS findings(id INTEGER PRIMARY KEY, submission_id INTEGER, version_n INTEGER, rule TEXT, category TEXT, severity TEXT,
  start INTEGER, end INTEGER, original TEXT, explanation TEXT, source_ref TEXT, source_title TEXT, source_text TEXT, confidence REAL,
  suggestion TEXT, origin TEXT, status TEXT DEFAULT 'open', decision_text TEXT, reason TEXT, decided_by INTEGER, decided_at TEXT);
CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY, submission_id INTEGER, finding_id INTEGER, user_id INTEGER, body TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, submission_id INTEGER, user_id INTEGER, action TEXT, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

def connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 50000).hex()

def check_pw(pw, stored):
    salt = stored.split("$")[0]
    return hash_pw(pw, salt) == stored

CLAUSES = [
 ("TONE-1", "Tone", "Friendly, calm voice", "BrightBox speaks in a warm, calm and professional voice. Use at most one exclamation mark per piece of content.", "exclaim", {"max": 1}, "low"),
 ("TONE-2", "Tone", "No shouting", "Never write whole words in ALL CAPS for emphasis. Acronyms such as FAQ or PDF are allowed.", "caps", {"allow": ["FAQ", "PDF", "USA", "GST", "UPI", "FSSAI"]}, "low"),
 ("TONE-3", "Tone", "No slang or filler", "Avoid slang and casual filler words. Write in clear, plain language.", "slang", {"map": {"gonna": "going to", "wanna": "want to", "lol": "", "awesome": "great", "guys": "everyone", "stuff": "products", "super": "very"}}, "low"),
 ("TONE-4", "Tone", "Emoji use", "Do not use emoji in brand content.", "emoji", {}, "low"),
 ("TERM-1", "Terminology", "Approved terminology", "Always use the approved BrightBox terms (see the terminology list). Product and company names must be written exactly as approved.", "term", {}, "medium"),
 ("CLAIM-1", "Claims", "No unsupported claims", "Do not use superlatives, absolute guarantees or health claims (best, #1, guaranteed, clinically proven, cure, risk-free). Only claims on the approved-claims list may be used.", "claim", {}, "high"),
 ("ACC-1", "Accessibility", "Images need alt text", "Every image must have meaningful alternative text describing what it shows.", "alt", {}, "high"),
 ("ACC-2", "Accessibility", "Descriptive link text", "Link text must describe the destination. Avoid 'click here' and 'read more'.", "link", {}, "medium"),
 ("ACC-3", "Accessibility", "Short sentences", "Keep sentences to 30 words or fewer so the content is easy to read.", "longsent", {"max": 30}, "low"),
 ("ACC-4", "Accessibility", "Colour contrast", "Text and background colours must have a contrast ratio of at least 4.5:1 (WCAG AA).", "contrast", {"min": 4.5}, "high"),
 ("VIS-1", "Visual", "Brand colour palette", "Only the approved palette may be used: green #1F6F5C, yellow #F4B942, white #FFFFFF, charcoal #1A1A1A, cream #F5F1E8.", "palette", {"colors": ["#1F6F5C", "#F4B942", "#FFFFFF", "#1A1A1A", "#F5F1E8"]}, "medium"),
 ("MSG-1", "Messaging", "Brand story", "BrightBox is a monthly subscription box of healthy Indian snacks. Messaging should focus on convenience, quality ingredients and honest pricing, and should never criticise competitors.", "text", {}, "medium"),
]
TERMS = [("customers", "members"), ("customer", "member"), ("Bright Box", "BrightBox"), ("Brightbox", "BrightBox"), ("snack pack", "snack box"), ("cheap", "affordable"), ("junk food", "snacks")]
FACTS = [
 ("FACT-1", "fact", "Starter box price", ["starter", "box"], "\u20b9499", "Price list, Oct 2026"),
 ("FACT-2", "fact", "Free delivery threshold", ["free", "delivery"], "\u20b9799", "Delivery policy v3"),
 ("FACT-3", "fact", "Annual plan saving", ["annual", "sav"], "15%", "Pricing sheet, Oct 2026"),
 ("FACT-4", "fact", "Year founded", ["founded"], "2021", "Company profile"),
 ("CLAIM-A1", "claim", "Approved claim: natural ingredients", [], "100% natural ingredients", "Lab certificate LC-2026-14"),
 ("CLAIM-A2", "claim", "Approved claim: no added sugar", [], "no added sugar", "Lab certificate LC-2026-15"),
]
USERS = [("riya", "Riya Shah", "creator"), ("manav", "Manav Desai", "manager"), ("sana", "Sana Iyer", "reviewer")]

def init():
    c = connect()
    c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM users").fetchone():
        for u, n, r in USERS:
            c.execute("INSERT INTO users(username,name,role,pw) VALUES(?,?,?,?)", (u, n, r, hash_pw("demo123")))
        for ref, cat, t, tx, ct, p, sv in CLAUSES:
            c.execute("INSERT INTO clauses VALUES(?,?,?,?,?,?,?)", (ref, cat, t, tx, ct, json.dumps(p), sv))
        for b, p in TERMS:
            c.execute("INSERT INTO terms(banned,preferred,clause_ref) VALUES(?,?,'TERM-1')", (b, p))
        for ref, k, l, kw, v, s in FACTS:
            c.execute("INSERT INTO facts(ref,kind,label,keywords,value,source) VALUES(?,?,?,?,?,?)", (ref, k, l, json.dumps(kw), v, s))
        c.commit()
        fresh = True
    else:
        fresh = False
    c.close()
    return fresh
