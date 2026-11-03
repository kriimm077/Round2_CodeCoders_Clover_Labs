# BrandLens (Team CLOVER LABS) - CodeNexus India 2026, Round 2

BrandLens reviews content **before publication** against an organisation's approved guidelines, terminology, facts and claims.
Every finding explains the problem and **cites the exact approved source**. Suggestions are never applied automatically: a person accepts, edits or dismisses each one, and a manager or reviewer approves the final content.

Demo brand: *BrightBox* (fictional healthy-snack subscription). No real confidential data is used.

## Try it (no setup for judges)
Open the live link and click a demo role on the login page (Creator / Brand manager / Reviewer). Password for all demo users: `demo123`.
Two-minute path: log in as **Creator** > *New review* > *Load sample content* > *Run review* > accept, edit or dismiss findings > *Apply my accepted changes* > *Submit for review* > log out and log in as **Manager** > open the item > *Approve* or *Request changes* > *Insights*.

## Features
- Brand knowledge base: guidelines, approved terminology, approved facts and claims (manager can add or remove; guideline import from pasted text or .txt/.docx/.pdf)
- Review of pasted text or uploaded .txt / .docx / .pdf, plus optional design colours (hex) for visual checks
- Checks: tone (exclamation marks, shouting, slang, emoji), terminology, unsupported promotional claims, fact conflicts (prices, percentages, years), accessibility (alt text, link text, long sentences, colour contrast), visual (brand palette)
- Explainability: category, severity, confidence, highlighted text, the guideline or fact cited, and a suggested fix that keeps the message
- Human control: accept / edit / dismiss (with reason), "needs human judgement" label for low confidence, nothing auto-applied
- Workflow: roles (creator, manager, reviewer), Draft > In Review > Changes Requested > Approved, comments, version history with diff, audit log, no self-approval
- Insights: most repeated issues, per-author and per-category counts, guidelines that authors keep dismissing (possibly unclear)
- Optional AI layer (Claude API) for tone and messaging nuance: findings must cite a real source and quote real text, or they are discarded

## Run locally
```
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://localhost:8000
python -m pytest tests -q
```
Optional AI layer: set `ANTHROPIC_API_KEY` (and optionally `CLAUDE_MODEL`). Without a key the product runs fully on the rule engine.

## Deploy (Render, free)
Push this folder to GitHub, then Render > New Web Service > connect the repo. `render.yaml` is included (build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`). The free tier sleeps after inactivity, so the first request can take about a minute.
Data is stored in SQLite and re-seeded with demo data on a fresh start.

## Architecture
Browser (single-page app, vanilla JS) > FastAPI REST API > review engine (`app/engine.py`) > SQLite (`app/db.py`).
Pipeline: parse text > deterministic rule checks (each tied to a guideline or fact) > optional LLM review > citation verifier > findings stored with a snapshot of the source > human decisions > new version > re-review.

## Disclosure
- External service: Anthropic Claude API, only if the administrator sets a key. Only the submitted text and the brand guidelines are sent. The UI shows a disclosure banner.
- Libraries: FastAPI, Uvicorn, Pydantic, python-multipart, python-docx, pypdf (open source).
- Development: the code was drafted with the help of an AI assistant (Claude), then reviewed, tested and deployed by the participant. No templates or boilerplate repositories were used.

## Known limitations
Rule checks are English-oriented. Imported free-text guidelines are used as reference and AI context, not turned into automatic rules. Images are not analysed yet (Round 3). SQLite on free hosting is not durable. Authentication is demo-grade.
