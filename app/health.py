"""Guideline Health 2.0: finds guidelines that are context-dependent, ambiguous, conflicting or unused, and proposes concrete rewrites a manager can apply."""
import json, re
from collections import Counter, defaultdict
from . import engine

STOP = set("the a an is are was be to of in on for and or it this that we they i you its as at by with not no so too very just".split())

def _keywords(reasons, n=3):
    c = Counter(w for r in reasons for w in re.findall(r"[a-z]{3,}", (r or "").lower()) if w not in STOP)
    return [w for w, _ in c.most_common(n)]

def analyze(c, ws):
    clauses = {r["ref"]: r for r in c.rows("SELECT * FROM clauses WHERE workspace_id=?", (ws,))}
    facts = c.rows("SELECT * FROM facts WHERE workspace_id=?", (ws,)); terms = c.rows("SELECT * FROM terms WHERE workspace_id=?", (ws,))
    fr = c.rows("""SELECT f.source_ref AS ref, f.source_title AS stitle, f.category AS scat, f.rule AS rule, f.status AS status, f.reason AS reason, s.channel AS channel, s.author_id AS author, s.id AS sid, substr(f.created_at,1,10) AS day
                   FROM findings f JOIN submissions s ON s.id=f.submission_id WHERE s.workspace_id=?""", (ws,))
    n_subs = c.scalar("SELECT COUNT(*) FROM submissions WHERE workspace_id=?", (ws,)) or 0
    per = defaultdict(lambda: {"stitle": "", "scat": "", "total": 0, "accepted": 0, "dismissed": 0, "open": 0, "ch": defaultdict(lambda: [0, 0]), "reasons": []})
    for f in fr:
        p = per[f["ref"]]; p["total"] += 1; p["stitle"] = f["stitle"]; p["scat"] = f["scat"]
        if f["status"] in ("accepted", "edited"):
            p["accepted"] += 1; p["ch"][f["channel"]][0] += 1
        elif f["status"] == "dismissed":
            p["dismissed"] += 1; p["ch"][f["channel"]][1] += 1; p["reasons"].append(f["reason"])
        else:
            p["open"] += 1
    rules, proposals = [], []
    for ref, p in sorted(per.items(), key=lambda kv: -kv[1]["total"]):
        decided = p["accepted"] + p["dismissed"]; rate = p["dismissed"] / decided if decided else 0
        ch = {k: {"accepted": v[0], "dismissed": v[1]} for k, v in p["ch"].items()}
        rules.append({"ref": ref, "title": (clauses.get(ref) or {}).get("title") or p["stitle"] or ref, "category": (clauses.get(ref) or {}).get("category") or p["scat"] or "", "total": p["total"], "accepted": p["accepted"],
                      "dismissed": p["dismissed"], "open": p["open"], "dismiss_rate": round(rate, 2), "channels": ch, "keywords": _keywords(p["reasons"]), "reasons": list(dict.fromkeys(r for r in p["reasons"] if r))[:3]})
        cl = clauses.get(ref)
        if not cl or cl["check_type"] == "text":
            continue
        exempt = set(json.loads(cl["params"] or "{}").get("exempt_channels") or [])
        live = {k: v for k, v in ch.items() if k not in exempt}          # channels where the rule still applies
        e_dis = sum(v["dismissed"] for v in live.values()); e_dec = e_dis + sum(v["accepted"] for v in live.values())
        e_rate = e_dis / e_dec if e_dec else 0
        if e_dec < 3 or e_rate < 0.4:
            continue
        hot = [k for k, v in live.items() if v["dismissed"] >= 2 and v["dismissed"] / (v["accepted"] + v["dismissed"]) >= 0.8]
        others = [k for k in live if k not in hot]
        calm = all(live[k]["dismissed"] / max(1, live[k]["accepted"] + live[k]["dismissed"]) <= 0.34 for k in others)
        if hot and calm:
            names = ", ".join(hot)
            proposals.append({"kind": "channel_exception", "ref": ref, "title": cl["title"], "channels": hot,
                              "evidence": f"Authors dismissed {sum(live[k]['dismissed'] for k in hot)} of {sum(live[k]['accepted'] + live[k]['dismissed'] for k in hot)} findings on {names} content" + (f", but accepted most findings on other channels ({', '.join(others)})." if others else "."),
                              "reasons": list(dict.fromkeys(r for r in p["reasons"] if r))[:3],
                              "old_text": cl["text"], "new_text": cl["text"].rstrip(". ") + f". Exception: this rule does not apply to {names} content.",
                              "explain": f"The rule is probably context-dependent. Applying this proposal stops {ref} from firing on {names} content, so authors stop seeing findings they keep dismissing."})
        elif e_rate >= 0.6:
            proposals.append({"kind": "reword", "ref": ref, "title": cl["title"], "channels": [],
                              "evidence": f"Authors dismissed {e_dis} of {e_dec} findings across channels ({round(e_rate * 100)}%)" + (f". Common words in their reasons: {', '.join(_keywords(p['reasons']))}." if p["reasons"] else "."),
                              "reasons": list(dict.fromkeys(r for r in p["reasons"] if r))[:3], "old_text": cl["text"], "new_text": None,
                              "explain": "There is no single channel pattern, so the wording itself is probably unclear or out of date. Review the rule with the authors; no automatic rewrite is proposed."})
    # static conflicts inside the knowledge base
    conflicts = []
    pal = []
    for cl in clauses.values():
        if cl["check_type"] == "palette":
            pal = json.loads(cl["params"] or "{}").get("colors", [])
    mn = 4.5
    for cl in clauses.values():
        if cl["check_type"] == "contrast":
            mn = json.loads(cl["params"] or "{}").get("min", 4.5)
    bad = [(a, b, round(engine.contrast(a, b), 2)) for a in pal for b in pal if a != b and engine.contrast(a, b) < mn and engine._lum(a) < engine._lum(b)]
    for a, b, r in bad[:6]:
        conflicts.append({"type": "palette_vs_contrast", "refs": ["VIS-1", "ACC-4"], "detail": f"Palette colours {a} (text) on {b} (background) have a contrast of only {r}:1, below the {mn}:1 required by ACC-4. The guidelines should say which colour pairs are allowed for text."})
    for c1 in [t for t in terms]:
        for c2 in terms:
            if c1["id"] != c2["id"] and c1["preferred"] == c2["banned"]:
                conflicts.append({"type": "term_chain", "refs": ["TERM-1"], "detail": f"\u201c{c1['banned']}\u201d must become \u201c{c1['preferred']}\u201d, but \u201c{c2['banned']}\u201d is itself banned. Authors cannot satisfy both."})
    fl = [f for f in facts if f["kind"] == "fact"]
    for i, a in enumerate(fl):
        for b in fl[i + 1:]:
            ka, kb = set(json.loads(a["keywords"] or "[]")), set(json.loads(b["keywords"] or "[]"))
            if ka and kb and (ka <= kb or kb <= ka) and a["value"] != b["value"]:
                conflicts.append({"type": "fact_overlap", "refs": [a["ref"], b["ref"]], "detail": f"Facts \u201c{a['label']}\u201d ({a['value']}) and \u201c{b['label']}\u201d ({b['value']}) share the same keywords, so one of them will always be flagged wrongly."})
    for t in terms:
        for f in facts:
            if f["kind"] == "claim" and re.search(r"(?<!\w)" + re.escape(t["banned"]) + r"(?!\w)", f["value"], re.I):
                conflicts.append({"type": "claim_vs_term", "refs": [f["ref"], "TERM-1"], "detail": f"The approved claim \u201c{f['value']}\u201d contains the banned term \u201c{t['banned']}\u201d."})
    unused = []
    if n_subs >= 3:
        seen = {r["ref"] for r in rules}
        unused = [{"ref": r, "title": cl["title"]} for r, cl in clauses.items() if cl["check_type"] != "text" and r not in seen]
    # trends and repeat offenders
    days = Counter(f["day"] for f in fr if f["day"]); by_ch = Counter(f["channel"] for f in fr)
    names = {u["id"]: u["name"] for u in c.rows("SELECT id,name FROM users WHERE workspace_id=?", (ws,))}
    rep = defaultdict(set)
    for f in fr:
        rep[(f["author"], f["ref"])].add(f["sid"])
    repeat = sorted([{"author": names.get(a, "?"), "ref": r, "submissions": len(s)} for (a, r), s in rep.items() if len(s) >= 2], key=lambda x: -x["submissions"])[:8]
    history = c.rows("""SELECT h.ref, h.old_text, h.new_text, h.reason, h.created_at, u.name AS by FROM clause_history h LEFT JOIN users u ON u.id=h.changed_by WHERE h.workspace_id=? ORDER BY h.id DESC LIMIT 10""", (ws,))
    return {"rules": rules, "proposals": proposals, "conflicts": conflicts, "unused": unused, "repeat": repeat, "history": history,
            "trend": [{"day": d, "n": n} for d, n in sorted(days.items())[-14:]], "by_channel": [{"channel": k, "n": v} for k, v in by_ch.most_common()]}
