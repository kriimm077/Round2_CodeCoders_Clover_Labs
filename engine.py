"""Review engine: deterministic rule checks + optional AI layer with citation verification.
Every finding carries the approved source (clause / fact) that justifies it."""
import re, json, os, urllib.request

def _span_sentences(text):
    return [(m.start(), m.end()) for m in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text) if m.group().strip()]

def _sentence_at(text, pos):
    for s, e in _span_sentences(text):
        if s <= pos < e:
            return s, e
    return 0, len(text)

def match_case(src, rep):
    if not rep:
        return rep
    if len(src) > 1 and src.isupper():
        return rep.upper()
    if src[:1].isupper():
        return rep[:1].upper() + rep[1:]
    return rep

def _hex6(h):
    h = h.lstrip("#").upper()
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "#" + h

def _lum(h):
    h = _hex6(h).lstrip("#")
    r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

def _dist(a, b):
    a, b = _hex6(a).lstrip("#"), _hex6(b).lstrip("#")
    return sum((int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) ** 2 for i in (0, 2, 4))

HEX_RE = re.compile(r"#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b")
CLAIM_RE = re.compile(r"(?<!\w)(world['\u2019]?s (?:best|leading|first|finest)|clinically proven|risk[- ]free|number one|#1|best|guarantee[ds]?|100%\s?|unbeatable|miracle|cures?)(?!\w)", re.I)
SOFTEN = {"best": "great", "#1": "trusted", "number one": "trusted", "guarantee": "aim to deliver", "guaranteed": "designed to deliver", "guarantees": "aims to deliver",
          "100%": "", "clinically proven": "carefully developed", "risk-free": "low-commitment", "risk free": "low-commitment", "unbeatable": "competitive",
          "miracle": "helpful", "cure": "help with", "cures": "helps with"}
NUM_RE = re.compile(r"[\u20b9$\u20ac]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?%|\d[\d,]*(?:\.\d+)?")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+")

def _kind(tok):
    return "cur" if tok[0] in "\u20b9$\u20ac" else ("pct" if tok.endswith("%") else "num")

def _norm(tok):
    return re.sub(r"[^\d.]", "", tok)

def run_rules(text, colors, kb):
    cl = {c["check_type"]: c for c in kb["clauses"] if c["check_type"] != "text"}
    P = lambda k: json.loads(cl[k]["params"] or "{}") if k in cl else {}
    out = []

    def add(rule, src, sev, s, e, expl, sugg, conf, cat=None):
        out.append(dict(rule=rule, category=cat or src.get("category", ""), severity=sev, start=s, end=e,
                        original=text[s:e] if s >= 0 else "", explanation=expl, source_ref=src["ref"], source_title=src["title"],
                        source_text=src["text"], confidence=conf, suggestion=sugg, origin="rule"))

    # 1. terminology
    if "term" in cl:
        c = cl["term"]
        for t in kb["terms"]:
            for m in re.finditer(r"(?<!\w)" + re.escape(t["banned"]) + r"(?!\w)", text, re.I):
                rep = match_case(m.group(), t["preferred"])
                add("term", c, c["severity"], m.start(), m.end(), f"\u201c{m.group()}\u201d is not approved BrightBox terminology. Use \u201c{rep}\u201d instead.", rep, 0.95)
    # 2. unsupported claims
    approved = [f["value"] for f in kb["facts"] if f["kind"] == "claim"]
    if "claim" in cl:
        c = cl["claim"]
        for m in CLAIM_RE.finditer(text):
            if any(a and a.lower() == text[i:i + len(a)].lower() and i <= m.start() and m.end() <= i + len(a)
                   for a in approved for i in [mm.start() for mm in re.finditer(re.escape(a), text, re.I)]):
                continue
            key = m.group().strip().lower()
            rep = match_case(m.group().strip(), SOFTEN.get(key, ""))
            if key == "100%":
                rep = ""
            add("claim", c, c["severity"], m.start(), m.start() + len(m.group().rstrip()) if key != "100%" else m.end(),
                f"\u201c{m.group().strip()}\u201d is a promotional claim with no approved evidence. Remove it or use an approved claim. The suggestion keeps the message but drops the unverifiable promise.", rep, 0.8)
    # 3. approved facts
    for f in kb["facts"]:
        if f["kind"] != "fact":
            continue
        kws = json.loads(f["keywords"] or "[]")
        fk = _kind(f["value"])
        for s0, e0 in _span_sentences(text):
            sent = text[s0:e0]
            if not kws or not all(k.lower() in sent.lower() for k in kws):
                continue
            for m in NUM_RE.finditer(sent):
                tok = m.group().strip()
                if _kind(tok) != fk:
                    continue
                if _norm(tok) != _norm(f["value"]):
                    src = {"ref": f["ref"], "title": f["label"], "text": f"Approved fact: {f['label']} = {f['value']}. Source: {f['source']}", "category": "Facts"}
                    add("fact", src, "high", s0 + m.start(), s0 + m.start() + len(m.group().rstrip()),
                        f"This states \u201c{tok}\u201d but the approved value for \u201c{f['label']}\u201d is {f['value']} (source: {f['source']}).", f["value"], 0.9)
                break
    # 4. tone
    if "exclaim" in cl:
        c = cl["exclaim"]; mx = P("exclaim").get("max", 1)
        for i, m in enumerate(re.finditer(r"!+(?!\[)", text)):
            if i >= mx or len(m.group()) > 1:
                add("exclaim", c, c["severity"], m.start(), m.end(), f"Too many exclamation marks. The voice guideline allows at most {mx}, and one at a time.", ".", 0.9)
    if "caps" in cl:
        c = cl["caps"]; allow = set(P("caps").get("allow", []))
        for m in re.finditer(r"\b[A-Z]{4,}\b", text):
            if m.group() not in allow:
                add("caps", c, c["severity"], m.start(), m.end(), f"\u201c{m.group()}\u201d is written in capitals, which reads as shouting.", m.group().capitalize(), 0.9)
    if "slang" in cl:
        c = cl["slang"]
        for w, rep in P("slang").get("map", {}).items():
            for m in re.finditer(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text, re.I):
                add("slang", c, c["severity"], m.start(), m.end(), f"\u201c{m.group()}\u201d is informal. Prefer plain, professional wording.", match_case(m.group(), rep), 0.85)
    if "emoji" in cl:
        c = cl["emoji"]
        for m in EMOJI_RE.finditer(text):
            add("emoji", c, c["severity"], m.start(), m.end(), "Emoji are not part of the brand voice guideline.", "", 0.9)
    # 5. accessibility
    if "alt" in cl:
        c = cl["alt"]
        for m in re.finditer(r"!\[\s*\]\(([^)]*)\)", text):
            add("alt", c, c["severity"], m.start(), m.end(), "This image has no alternative text, so screen-reader users get nothing. Describe what the image shows.", f"![Describe what this image shows]({m.group(1)})", 0.95)
        for m in re.finditer(r"<img\b(?![^>]*\balt\s*=\s*[\"'][^\"']+[\"'])[^>]*>", text, re.I):
            add("alt", c, c["severity"], m.start(), m.end(), "This <img> has no alt text. Describe what the image shows.", m.group().replace("<img", '<img alt="Describe what this image shows"', 1), 0.95)
    if "link" in cl:
        c = cl["link"]
        for m in re.finditer(r"\[(click here|here|read more|link|this)\]\(([^)]*)\)", text, re.I):
            add("link", c, c["severity"], m.start(), m.end(), f"Link text \u201c{m.group(1)}\u201d does not say where the link goes.", f"[Describe where this link goes]({m.group(2)})", 0.9)
    if "longsent" in cl:
        c = cl["longsent"]; mx = P("longsent").get("max", 30)
        for s0, e0 in _span_sentences(text):
            seg = text[s0:e0]
            n = len(seg.split())
            if n > mx:
                lead = len(seg) - len(seg.lstrip())
                add("longsent", c, c["severity"], s0 + lead, e0, f"This sentence has {n} words (limit {mx}). Split it into shorter sentences. This needs your own wording, so no automatic suggestion is given.", "", 0.6)
    # 6. visual: palette + contrast
    pal = P("palette").get("colors", [])
    if "palette" in cl and pal:
        c = cl["palette"]; seen = set()
        for m in HEX_RE.finditer(text):
            if _hex6(m.group()) not in [_hex6(p) for p in pal]:
                near = min(pal, key=lambda p: _dist(p, m.group()))
                add("palette", c, c["severity"], m.start(), m.end(), f"{m.group()} is not in the approved palette. The nearest approved colour is {near}.", near, 0.85)
        for h in HEX_RE.findall(colors or ""):
            hh = _hex6(h)
            if hh not in [_hex6(p) for p in pal] and hh not in seen:
                seen.add(hh)
                near = min(pal, key=lambda p: _dist(p, h))
                add("palette", c, c["severity"], -1, -1, f"Design colour {h} is not in the approved palette. Nearest approved colour: {near}. (Design setting, change it in your design file.)", near, 0.85)
    if "contrast" in cl:
        hx = HEX_RE.findall(colors or "")
        if len(hx) >= 2:
            c = cl["contrast"]; mn = P("contrast").get("min", 4.5)
            r = contrast(hx[0], hx[1])
            if r < mn:
                ok = [p for p in pal if contrast(p, hx[1]) >= mn]
                add("contrast", c, c["severity"], -1, -1, f"Text colour {hx[0]} on background {hx[1]} has contrast {r:.2f}:1, below the required {mn}:1." + (f" An approved colour that passes: {ok[0]}." if ok else ""), ok[0] if ok else "", 0.97)
    return out

def ai_review(text, purpose, channel, kb, existing):
    """Optional LLM layer. Returns (findings, status). Findings only survive if the cited source exists and the quote is really in the text."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return [], "AI layer not configured (rule-based review only)"
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
    refs = {c["ref"]: c for c in kb["clauses"]}
    ctx = {"guidelines": [{"ref": c["ref"], "title": c["title"], "text": c["text"]} for c in kb["clauses"]],
           "approved_terms": [{"avoid": t["banned"], "use": t["preferred"]} for t in kb["terms"]],
           "approved_facts_and_claims": [{"ref": f["ref"], "label": f["label"], "value": f["value"]} for f in kb["facts"]]}
    system = ("You are a careful brand-compliance reviewer. Use ONLY the guidelines, terms and facts provided. Never invent rules. "
              "Look for tone, messaging and claim problems that simple rules could miss. Return ONLY a JSON array (max 6 items), no prose. "
              "Each item: {\"source_ref\": <ref of the guideline or fact involved>, \"quote\": <exact substring of the content>, \"issue\": <one sentence>, "
              "\"suggestion\": <replacement for the quote that keeps the author's purpose>, \"severity\": \"low|medium|high\", \"confidence\": 0-1}. Return [] if nothing is wrong.")
    user = json.dumps({"purpose": purpose, "channel": channel, "approved_sources": ctx, "content": text})
    body = json.dumps({"model": model, "max_tokens": 1500, "system": system, "messages": [{"role": "user", "content": user}]}).encode()
    try:
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=40).read())
        raw = "".join(b.get("text", "") for b in data.get("content", []))
        raw = raw.strip().strip("`")
        raw = raw[raw.find("["): raw.rfind("]") + 1]
        items = json.loads(raw)
    except Exception as ex:
        return [], f"AI layer unavailable ({type(ex).__name__}); rule-based review used"
    allsrc = dict(refs)
    for f in kb["facts"]:
        allsrc[f["ref"]] = {"ref": f["ref"], "title": f["label"], "text": f"Approved {f['kind']}: {f['label']} = {f['value']}. Source: {f['source']}", "category": "Facts"}
    out, dropped = [], 0
    for it in items if isinstance(items, list) else []:
        try:
            src = allsrc.get(it["source_ref"]); q = it["quote"]; i = text.find(q)
            if not src or not q or i < 0:
                dropped += 1; continue
            if any(i < e["end"] and i + len(q) > e["start"] >= 0 for e in existing + out if e["start"] >= 0):
                continue
            sev = it.get("severity", "medium"); sev = sev if sev in ("low", "medium", "high") else "medium"
            out.append(dict(rule="ai", category=src.get("category", "Messaging"), severity=sev, start=i, end=i + len(q), original=q,
                            explanation=str(it["issue"]), source_ref=src["ref"], source_title=src["title"], source_text=src["text"],
                            confidence=min(0.75, float(it.get("confidence", 0.6))), suggestion=str(it.get("suggestion", "")), origin="ai"))
        except Exception:
            dropped += 1
    return out, f"AI layer used ({model}); {len(out)} verified finding(s), {dropped} discarded because the citation or quote could not be verified"

def review(text, colors, purpose, channel, kb):
    found = run_rules(text, colors, kb)
    ai, status = ai_review(text, purpose, channel, kb, found)
    allf = found + ai
    allf.sort(key=lambda f: (f["start"] if f["start"] >= 0 else 10 ** 9, f["end"]))
    return allf, status
