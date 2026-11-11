"""Detection and redaction of personal data. Used (a) to flag personal data in content and (b) to strip it before any text is sent to an external AI service."""
import re

PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}\s\d{4}\s\d{4}(?!\d)")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE", re.compile(r"(?<![\w.])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\w)")),
]

def find_pii(text):
    """Return non-overlapping (start, end, kind, value) matches."""
    hits, taken = [], []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            if any(m.start() < e and m.end() > s for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), m.end(), kind, m.group()))
    return sorted(hits)

def redact(text):
    """Replace personal data with numbered placeholders. Returns (redacted_text, mapping)."""
    mapping, counts, out, pos = {}, {}, [], 0
    for s, e, kind, val in find_pii(text):
        counts[kind] = counts.get(kind, 0) + 1
        tok = f"[{kind}_{counts[kind]}]"
        mapping[tok] = val
        out.append(text[pos:s]); out.append(tok); pos = e
    out.append(text[pos:])
    return "".join(out), mapping

def restore(s, mapping):
    for tok, val in mapping.items():
        s = s.replace(tok, val)
    return s
