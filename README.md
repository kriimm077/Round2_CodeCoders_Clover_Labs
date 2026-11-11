# BrandLens - explainable brand governance before you publish

**Team CLOVER LABS** (solo: Krima Patel) - CodeNexus India 2026, final submission (Round 3).

BrandLens reviews content **before it is published** against an organisation's approved guidelines, terminology, facts, claims and brand assets.
Every finding explains the problem, **cites the approved source** behind it and offers a correction that keeps the author's purpose. Nothing is ever changed automatically: a person accepts, edits or dismisses each suggestion, and a manager or reviewer approves the result.

## Try it in two minutes (no setup)
Open the live link and click a demo role on the login page. Password for every demo user is `demo123`.

| Username | Role | Workspace |
|---|---|---|
| riya | creator | BrightBox |
| manav | brand manager | BrightBox |
| sana | reviewer | BrightBox |
| nora | brand manager | Northwind Bank (separate data) |
| nikhil | creator | Northwind Bank |

1. Log in as **Creator (Riya)** > **New review** > **Load sample content** > **Run review**. Read the findings: each one shows why, the cited guideline, a confidence score and a suggestion.
2. Try **Use off-brand sample image** > **Run review** to see image checks (palette, logo, resolution, aspect ratio, alt text).
3. Accept, edit or dismiss findings, click **Apply my accepted changes** (creates a new version), then **Submit for review**.
4. Log out, log in as **Manager (Manav)**, open the item and **Approve** or **Request changes**.
5. Open **Insights** (Guideline health: proposed guideline changes you can apply) and **Quality** (measured accuracy per rule).
6. Log in as **Nora** to see that Northwind Bank cannot see BrightBox content.

## Features
**Review engine**
- Text from pasting or uploading .txt / .docx / .pdf; optional design colours (hex)
- Tone (exclamation marks, ALL CAPS, slang, emoji), terminology, unsupported promotional claims (with an approved-claims list), fact conflicts (prices, percentages, years), personal data in content, accessibility (alt text, link text, long sentences, colour contrast)
- **Image and visual review:** upload a PNG/JPG; checks dominant-colour palette compliance, brand-logo presence, size and clear space (FFT template matching against the stored approved logo), minimum resolution, social aspect ratios, alt text. Detected logo is outlined on the image.
- Explainable findings: severity, calibrated confidence, highlighted text, cited guideline or fact, suggested fix

**Human control and workflow**
- Accept / edit / dismiss (dismissals need a reason); "needs human judgement" label for low-confidence findings
- Roles (creator, manager, reviewer); Draft > In Review > Changes Requested > Approved; no self-approval; approved content locked
- Comments, versions with word-level diff, full audit log; dismissals carry over to new versions

**Governance insights (Guideline Health 2.0)**
- Most repeated issues, per-channel accept/dismiss rates, daily trend, repeat issues per author
- Detects **context-dependent rules** (e.g. emoji rule dismissed on Social media) and proposes new wording plus a channel exception that a manager can apply in one click; the change takes effect immediately and is recorded in the guideline history
- Detects **conflicts inside the guidelines** (palette colours that fail the contrast rule, term chains, overlapping facts, claims containing banned terms) and rules that never fire

**Reliability and trust**
- Labelled evaluation (76 main cases + 14 adversarial stress cases): precision / recall per rule, shown in the **Quality** page and via `python -m eval.run`
- Confidence calibration: shown confidence blends evaluation precision with how often people in the workspace accept or dismiss that rule's findings
- **Workspaces:** every table carries a workspace id; users only see their own workspace's guidelines, content, images and insights. Create your own workspace from the login page.
- **PostgreSQL** support (set `DATABASE_URL`), SQLite by default
- Optional AI layer (Claude API) with citation verification, and **redaction of personal data before anything is sent** (`REDACT_BEFORE_AI`, on by default); "Preview privacy redaction" shows what would be sent

## Run it locally
```
git clone https://github.com/kriimm077/Round2_CodeCoders_Clover_Labs.git
cd Round2_CodeCoders_Clover_Labs
python -m venv .venv && .venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://localhost:8000. A SQLite database is created in `data/` with the demo workspaces on first start.

Tests and evaluation:
```
python -m pytest tests -q          # 15 tests: workflow, images, workspaces, robustness, health, privacy/AI (mocked), evaluation
python -m eval.run                 # precision / recall per rule on the labelled sets
```
Run the tests against PostgreSQL by setting `DATABASE_URL` first.

## Configuration (all optional)
See `.env.example`. `DATABASE_URL` (PostgreSQL), `ANTHROPIC_API_KEY` + `CLAUDE_MODEL` (AI layer), `REDACT_BEFORE_AI`, `DB_PATH`.

## Deploy on Render
1. Push the repository to GitHub. Render > New > Web Service > connect it. Build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, environment variable `PYTHON_VERSION=3.11.9`.
2. For persistent data: Render > New > PostgreSQL (free), copy its **Internal Database URL**, add it to the web service as `DATABASE_URL`, redeploy. Without it the app uses SQLite, which resets whenever the free server restarts (demo data is recreated automatically).
3. The free web service sleeps when idle; the first request can take about a minute. Health check: `/api/health`.

## Architecture
Browser single-page app (vanilla JS) > FastAPI REST API > review engine + vision module > SQLite or PostgreSQL. See `docs/ARCHITECTURE.md`.

Code map: `app/main.py` (API, workflow, workspace scoping) - `app/engine.py` (text rules + optional AI layer with verifier) - `app/vision.py` (image checks, logo detection) - `app/health.py` (Guideline Health) - `app/quality.py` (evaluation + calibration) - `app/privacy.py` (PII detection/redaction) - `app/db.py` (storage, schema, seed) - `app/static/index.html` (UI) - `eval/` (labelled datasets) - `tests/`.

## Documentation
`docs/ARCHITECTURE.md` - `docs/TESTING.md` (what was tested, bugs found and fixed) - `docs/ROADMAP.md`

## Disclosure
- Optional external service: Anthropic Claude API, only if an administrator sets a key. Not enabled in the live demo. Personal data is redacted before sending, and AI findings are labelled and citation-verified.
- Libraries (open source): FastAPI, Uvicorn, Pydantic, python-multipart, python-docx, pypdf, Pillow, NumPy, psycopg2.
- Development: the code was drafted with the help of an AI assistant (Claude), then reviewed, tested and deployed by me . No templates or boilerplate repositories were used. The demo brands (BrightBox, Northwind Bank) and all sample content are fictional.

## Known limitations
- Text rules are English-oriented and pattern based. They miss paraphrases (e.g. "twenty percent"), cannot tell idioms from claims ("Best of luck"), and do not check comparisons with competitors. The stress set documents these cases.
- Imported free-text guidelines are stored as reference text (and AI context) and do not create automatic rules; automatic checks come from the built-in rule types plus terms, facts and claims.
- Logo detection needs a flat, opaque approved logo; heavily rotated or partly hidden logos are reported as "not detected" for a human to judge. Photos are not checked for brand colours.
- The AI layers (text and vision) are unit-tested with a mocked API only, not against a live key, and are off in the demo.
- Scanned (image-only) PDFs cannot be read. Authentication is demo-grade (no password reset, no email verification).
