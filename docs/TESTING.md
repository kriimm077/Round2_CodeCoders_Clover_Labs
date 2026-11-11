# Testing

## What was tested and how
| Level | What | How |
|---|---|---|
| Unit | rule engine, sentence splitting, number matching, PII detection and redaction, logo detection, calibration | pytest |
| Integration | full API workflow, roles and permissions, versions and diff, knowledge-base edits, uploads, images, Guideline Health proposal applied and changing behaviour | pytest with the FastAPI test client |
| Isolation | a second workspace cannot read or change another's submissions, images, findings, comments or knowledge base | pytest |
| Robustness | empty, oversized and malformed input, bad images, unknown ids, expired token, same action twice, refresh-safe read endpoints, Unicode and HTML in content, idempotent demo seeding | pytest |
| AI layer | redaction before sending, citation verification, quote restoration (with a mocked API, because no live key was used) | pytest |
| Evaluation | precision and recall per rule on labelled content | `python -m eval.run` |
| Database | the whole suite on SQLite and on a real PostgreSQL 16 server | pytest with `DATABASE_URL` |
| Interface | login, refresh, new review, image upload, double click, apply, submit blocked, role switch, insights proposal applied, quality page, logo shown, workspace isolation | Playwright browser script, no console errors except the intentional 400 for a blocked submission |

Result: **15 automated tests pass on SQLite and on PostgreSQL.**

## Evaluation results
Main set: 76 cases (66 text, 10 generated images), 63 expected findings. Precision 100%, recall 100% after the fixes below.
Stress set: 14 hard cases written to expose limits. First run: precision 62.5%, recall 45.5%. After fixing the cheap gaps: precision 75%, recall 82% (9 correct, 3 false alarms, 2 misses). The remaining failures are kept on purpose: the idiom "Best of luck" is flagged as a claim, "twenty percent" in words is not matched to the fact, the acronym UNICEF is flagged as shouting, a second price in the same sentence is compared with the wrong fact, and a comparison with a competitor is not detected.
These sets were written by the developer, so they measure consistency and known limits, not accuracy on unseen real content.

## Bugs found by testing and fixed
1. A correctly written brand name ("BrightBox") was flagged because term matching ignored case. Fixed: the approved spelling is never flagged.
2. Brand terms inside emails and URLs were flagged. Fixed: emails and URLs are excluded from prose checks.
3. A year followed by a comma ("2019,") was read as a different number. Fixed: number pattern.
4. Three-letter shouting ("NOW") was missed. Fixed: rule now covers 3+ letters, with an acronym allow-list.
5. "Rs. 599" was split into two sentences so the price was never compared. Fixed: sentence splitter, plus "Rs." and "INR" currency forms.
6. "No. 1", "greatest" and spaced phone numbers ("+91 98765 43210") were missed. Fixed.
7. The "!" of Markdown image syntax was counted as an exclamation mark. Fixed.
8. Palette check wrongly flagged on-brand images because of colour quantisation. Fixed: per-pixel distance to the approved palette.
9. Logo clear-space check flagged correct designs because the detected box was a few pixels off. Fixed: tolerance.
10. If the server stopped while creating demo data, the demo content was never created. Fixed: seeding is all-or-nothing and self-healing; covered by a test.
11. Guideline Health kept proposing a change that had already been applied. Fixed: exempt channels are excluded from the statistics.
12. A false "term conflict" between "BrightBox" and "Brightbox" in Guideline Health. Fixed.

## Known to be imperfect
Stress-set misses above; AI layers not tested against a live service; free hosting restarts can reset SQLite data (use PostgreSQL); logo detection needs a flat, opaque approved logo.
