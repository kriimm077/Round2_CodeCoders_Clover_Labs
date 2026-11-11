def test_guideline_health_proposal_changes_behaviour(client, H):
    c, riya, manav = client, H["riya"], H["manav"]
    ins = c.get("/api/insights", headers=manav).json()
    prop = next(p for p in ins["proposals"] if p["ref"] == "TONE-4")
    assert prop["kind"] == "channel_exception" and prop["channels"] == ["Social media"] and "Exception" in prop["new_text"]
    assert any(x["type"] == "palette_vs_contrast" for x in ins["conflicts"])
    def emoji_count(ch):
        s = c.post("/api/submissions", headers=riya, json={"title": "e", "channel": ch, "text": "Fun times \U0001F389"}).json()["id"]
        return sum(f["rule"] == "emoji" for f in c.get(f"/api/submissions/{s}", headers=riya).json()["findings"])
    assert emoji_count("Social media") == 1
    assert c.post("/api/proposals/apply", headers=riya, json={"ref": "TONE-4", "exempt_channels": ["Social media"]}).status_code == 403
    assert c.post("/api/proposals/apply", headers=manav, json={"ref": "TONE-4", "exempt_channels": ["Moon"]}).status_code == 400
    assert c.post("/api/proposals/apply", headers=manav, json={"ref": "TONE-4", "exempt_channels": prop["channels"], "new_text": prop["new_text"]}).status_code == 200
    assert emoji_count("Social media") == 0 and emoji_count("Email") == 1
    hist = c.get("/api/insights", headers=manav).json()["history"]
    assert hist and hist[0]["ref"] == "TONE-4"
    assert not any(p["ref"] == "TONE-4" for p in c.get("/api/insights", headers=manav).json()["proposals"])

def test_evaluation_and_calibration(client, H):
    from app import quality
    ev = quality.evaluate()
    assert ev["main"]["overall"]["precision"] >= 0.95 and ev["main"]["overall"]["recall"] >= 0.95 and ev["main"]["cases"] >= 70
    assert ev["stress"]["overall"]["precision"] < 1.0           # the stress set must keep exposing known limits
    base = quality.calibrate("slang", 0.9, 0, 0)[0]
    assert quality.calibrate("slang", 0.9, 0, 10)[0] < base - 0.2 and quality.calibrate("slang", 0.9, 10, 0)[0] >= base   # dismissals lower, acceptance keeps confidence
    q = client.get("/api/quality", headers=H["manav"]).json()
    assert q["table"] and all(0 < r["calibrated"] <= 0.95 for r in q["table"])
