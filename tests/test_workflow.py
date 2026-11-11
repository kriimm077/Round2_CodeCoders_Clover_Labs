def test_end_to_end_workflow(client, H):
    c, riya, manav, sana = client, H["riya"], H["manav"], H["sana"]
    assert c.get("/").status_code == 200 and c.get("/api/health").json()["ok"]
    assert c.post("/api/login", json={"username": "riya", "password": "bad"}).status_code == 401
    assert c.get("/api/submissions").status_code == 401
    assert len(c.get("/api/submissions", headers=riya).json()) >= 4   # seeded demo content
    r = c.post("/api/submissions", headers=riya, json={"title": "T", "purpose": "promo", "channel": "Email", "colors": "text: #888888, background: #FFFFFF",
        "text": "Hey guys!! Our customers get the best deal. Starter box is \u20b9599. Click [here](x). ![](a.png)"})
    sid = r.json()["id"]
    d = c.get(f"/api/submissions/{sid}", headers=riya).json()
    refs = {f["source_ref"] for f in d["findings"]}
    assert {"TONE-3", "TONE-1", "TERM-1", "CLAIM-1", "FACT-1", "ACC-2", "ACC-1", "ACC-4"} <= refs
    assert all(f["source_ref"] and f["source_text"] and f["explanation"] and 0 < f["confidence"] < 1 for f in d["findings"])
    assert c.post(f"/api/submissions/{sid}/status", headers=riya, json={"action": "submit"}).status_code == 400   # undecided findings
    for f in d["findings"]:
        if f["suggestion"] and f["start"] >= 0:
            assert c.post(f"/api/findings/{f['id']}/decide", headers=riya, json={"decision": "accept"}).status_code == 200
        else:
            assert c.post(f"/api/findings/{f['id']}/decide", headers=riya, json={"decision": "dismiss"}).status_code == 400
            assert c.post(f"/api/findings/{f['id']}/decide", headers=riya, json={"decision": "dismiss", "reason": "design handled elsewhere"}).status_code == 200
    assert c.post(f"/api/findings/{d['findings'][0]['id']}/decide", headers=manav, json={"decision": "accept"}).status_code == 403
    assert c.post(f"/api/submissions/{sid}/apply", headers=riya).json()["version"] == 2
    d2 = c.get(f"/api/submissions/{sid}", headers=riya).json()
    assert "members" in d2["version"]["text"] and "customers" not in d2["version"]["text"] and "\u20b9499" in d2["version"]["text"]
    assert any(o["op"] == "insert" for o in c.get(f"/api/submissions/{sid}/diff?a=1&b=2", headers=riya).json()["ops"])
    for f in d2["findings"]:
        if f["status"] == "open":
            c.post(f"/api/findings/{f['id']}/decide", headers=riya, json={"decision": "dismiss", "reason": "ok"})
    assert c.post(f"/api/submissions/{sid}/status", headers=riya, json={"action": "submit"}).json()["status"] == "In Review"
    assert c.post(f"/api/submissions/{sid}/status", headers=riya, json={"action": "submit"}).status_code == 400       # same action twice
    assert c.post(f"/api/submissions/{sid}/status", headers=riya, json={"action": "approve"}).status_code == 403
    assert c.post(f"/api/submissions/{sid}/status", headers=manav, json={"action": "request_changes"}).status_code == 400
    assert c.post(f"/api/submissions/{sid}/status", headers=manav, json={"action": "request_changes", "comment": "Fix tone"}).json()["status"] == "Changes Requested"
    assert c.post(f"/api/submissions/{sid}/comments", headers=riya, json={"body": "Will do"}).status_code == 200
    c.post(f"/api/submissions/{sid}/version", headers=riya, json={"text": "Hello members. Our Starter box costs \u20b9499.", "note": "rewrite"})
    assert c.get(f"/api/submissions/{sid}", headers=riya).json()["submission"]["status"] == "Draft"
    assert c.post(f"/api/submissions/{sid}/status", headers=riya, json={"action": "submit"}).json()["status"] == "In Review"
    assert c.post(f"/api/submissions/{sid}/status", headers=sana, json={"action": "approve", "comment": "Good"}).json()["status"] == "Approved"
    assert c.post(f"/api/submissions/{sid}/status", headers=sana, json={"action": "approve"}).status_code == 400        # approved twice
    f0 = c.get(f"/api/submissions/{sid}", headers=riya).json()["findings"]
    assert c.post(f"/api/submissions/{sid}/apply", headers=riya).status_code == 400                                       # locked after approval

def test_knowledge_base_and_uploads(client, H):
    c, riya, manav = client, H["riya"], H["manav"]
    assert c.post("/api/kb/term", headers=riya, json={"banned": "x", "preferred": "y"}).status_code == 403
    assert c.post("/api/kb/term", headers=manav, json={"banned": "tasty", "preferred": "delicious"}).status_code == 200
    n = c.post("/api/submissions", headers=riya, json={"title": "K", "purpose": "p", "channel": "Website", "text": "A tasty snack."}).json()["id"]
    assert any(f["original"] == "tasty" for f in c.get(f"/api/submissions/{n}", headers=riya).json()["findings"])
    assert c.post("/api/extract", headers=riya, files={"file": ("a.txt", b"hello world")}).json()["text"] == "hello world"
    assert c.post("/api/extract", headers=riya, files={"file": ("a.exe", b"zzz")}).status_code == 400
    assert c.post("/api/kb/import", headers=manav, json={"text": "Voice rule one is long enough.\n\nSecond paragraph about the brand story."}).json()["imported"] == 2
    assert c.post("/api/kb/import", headers=manav, json={"text": "short"}).status_code == 400
    assert c.put("/api/kb/palette", headers=manav, json={"colors": ["#1F6F5C", "nothex"]}).status_code == 400
    assert c.get("/api/insights", headers=riya).status_code == 403
