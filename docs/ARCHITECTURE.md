# Architecture

```
Browser (single-page app, vanilla JS)
   |  JSON over HTTPS, Bearer token
FastAPI (app/main.py)  -- auth, workspace scoping, workflow rules, audit log
   |-- engine.py   text rules + optional AI layer (redaction -> LLM -> citation verifier)
   |-- vision.py   palette, logo (FFT template matching), resolution/ratio, alt text, optional vision AI
   |-- privacy.py  PII detection and redaction
   |-- health.py   Guideline Health analysis and proposals
   |-- quality.py  evaluation harness and confidence calibration
   |-- db.py       SQLite or PostgreSQL behind one small connection wrapper
```

## Data flow for one review
1. The creator submits text and/or an image with a purpose and channel. Input is validated (length limits, image decoded and normalised, channel whitelist).
2. `engine.run_rules` runs deterministic checks. Each rule belongs to a guideline clause (or an approved fact/claim/term); rules a manager has exempted for the channel are skipped. Emails and URLs are never treated as prose.
3. `vision.review_image` (if an image is attached) checks palette, logo, resolution, aspect ratio and alt text against the workspace's guidelines and approved logo.
4. If an AI key is configured: personal data is redacted, the LLM returns structured findings, and `verify` keeps a finding only if its cited source exists and its quote appears in the content. Surviving findings are labelled AI and their confidence is capped.
5. `quality.calibrate` replaces each raw confidence by (evaluation precision x 6 + accepted) / (6 + accepted + dismissed), using the workspace's human decisions.
6. Findings are stored with a snapshot of the cited source text, so explanations stay correct if guidelines change later. Earlier dismissals of the same issue are carried over.
7. The author decides on every finding; "apply" builds a new version from accepted/edited suggestions (non-overlapping, applied from the end of the text); the new version is re-reviewed.
8. Submitting requires all findings decided. A manager or reviewer (not the author) approves or requests changes. Every step is written to the audit log.

## Tenant isolation
`workspaces` -> `users`, `clauses`, `terms`, `facts`, `assets`, `submissions`, `audit`, `clause_history` all carry `workspace_id`. Every endpoint resolves the user's workspace from the session and filters by it; findings, versions, comments and images are reached only through a submission that is checked against the workspace. Tests verify cross-workspace reads and writes return 404.

## Hardest problems and how they were solved
- **Trustworthy explanations:** findings are never free text from a model. Each carries the source reference and text, and AI output must pass the verifier.
- **Logo size and clear space without a CV library:** normalised cross-correlation computed with FFTs over a geometric series of logo scales (about 40), with integral images for local energy, on a downscaled image; clear space is measured on the full-resolution ring around the match.
- **Numbers in context:** a fact is compared only with numbers of the same kind (currency, percent, plain) in sentences containing all its keywords; the sentence splitter keeps "Rs. 599" and "No. 1" intact.
- **Guidelines that fight each other:** static analysis finds contradictions (e.g. a palette colour that can never meet the contrast rule) and dismissal patterns reveal rules that depend on the channel.
- **Same code on two databases:** a thin wrapper translates placeholders and returns ids on SQLite and PostgreSQL; the full test suite runs on both.
