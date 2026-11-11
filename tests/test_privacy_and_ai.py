import json, os
from app import engine, privacy, db

def test_redaction_roundtrip():
    t = "Call 98765 43210 or mail riya@example.com, PAN ABCDE1234F, card 4111 1111 1111 1111."
    red, m = privacy.redact(t)
    assert "9876" not in red and "@" not in red and "ABCDE" not in red and "4111" not in red
    assert privacy.restore(red, m) == t

def test_ai_layer_verifies_citations_and_redacts(monkeypatch):
    os.environ["ANTHROPIC_API_KEY"] = "test"; sent = {}
    text = "Mail riya@example.com for our greatest ever offer. Hello there."
    def fake(req):
        sent["body"] = req.data.decode()
        items = [{"source_ref": "TONE-1", "quote": "greatest ever offer", "issue": "Hype", "suggestion": "great offer", "severity": "low", "confidence": 0.9},
                 {"source_ref": "NOPE-9", "quote": "Hello there", "issue": "x", "suggestion": "", "severity": "low", "confidence": 0.9},       # unknown source -> dropped
                 {"source_ref": "TONE-1", "quote": "text that is not in the content", "issue": "x", "suggestion": "", "severity": "low", "confidence": 0.9},   # quote not found -> dropped
                 {"source_ref": "PRIV-1", "quote": "[EMAIL_1]", "issue": "Personal email", "suggestion": "our support team", "severity": "medium", "confidence": 0.9}]
        return {"content": [{"type": "text", "text": json.dumps(items)}]}
    monkeypatch.setattr(engine, "_http_post", fake)
    try:
        found, status = engine.ai_review(text, "promo", "Email", db.default_kb(), [])
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    assert "riya@example.com" not in sent["body"] and "[EMAIL_1]" in sent["body"]      # personal data never left
    quotes = sorted(f["original"] for f in found)
    assert quotes == ["greatest ever offer", "riya@example.com"] and all(f["origin"] == "ai" for f in found) and "2 verified" in status and "2 discarded" in status
    assert all(f["confidence"] <= 0.75 for f in found)
