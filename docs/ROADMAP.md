# Product roadmap

**Who it is for:** small and mid-size organisations (agencies, startups, student and community bodies, regional marketing teams) where several people and AI tools publish content and one or two people approve it.

| Order | Next step | Why | What it takes |
|---|---|---|---|
| 1 | Production-grade authentication (email login, password reset, SSO), invitations, per-workspace admin | The demo login is not safe for real brand material | Auth provider integration; about 1 week |
| 2 | Turn imported guideline text into rules: a guided "rule builder" (banned phrase, required phrase, number must match, channel scope) plus an LLM-assisted suggestion step that the manager must approve | Today imported text is only reference | Rule schema + UI; reuse the verifier; 1-2 weeks |
| 3 | Larger, independent evaluation set from real customer content, tracked per release | The current set is small and written by the developer | Collect and label 300+ pieces with 2 reviewers |
| 4 | Language support: Hindi and Gujarati terminology, tone and claim checks; right-to-left scripts | Many Indian brands publish in several languages | Language-specific rules and test sets |
| 5 | Integrations: Google Docs / Drive, Figma, WordPress, Slack approvals | Review where people already work | OAuth connectors; one integration at a time |
| 6 | Richer visual review: logo recolouring and rotation, text-over-image contrast, video thumbnails and subtitles | Visual mistakes are the hardest to catch by eye | Extend `vision.py`; labelled image set |
| 7 | Policy packs and audit export (PDF / CSV), retention settings, self-hosted or private-model option | Regulated and confidential brands | Export service, configuration, deployment guide |

Success measures for each step: fewer corrections after publication, shorter review time per item, rule precision above 95% on the independent set, and a falling share of findings that authors dismiss.
