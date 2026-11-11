def test_workspace_isolation(client, H):
    c, riya, nora, nikhil = client, H["riya"], H["nora"], H["nikhil"]
    bright = c.get("/api/submissions", headers=riya).json()
    assert len(bright) >= 3 and c.get("/api/submissions", headers=nora).json() == []
    sid = bright[0]["id"]
    assert c.get(f"/api/submissions/{sid}", headers=nora).status_code == 404          # other workspace
    assert c.get(f"/api/submissions/{sid}/image", headers=nora).status_code == 404
    kb_n = c.get("/api/kb", headers=nora).json(); kb_b = c.get("/api/kb", headers=riya).json()
    assert {t["banned"] for t in kb_n["terms"]}.isdisjoint({t["banned"] for t in kb_b["terms"]})
    # a term added in one workspace never affects the other
    c.post("/api/kb/term", headers=nora, json={"banned": "zebra", "preferred": "horse"})
    s1 = c.post("/api/submissions", headers=nikhil, json={"title": "N", "text": "A zebra."}).json()["id"]
    s2 = c.post("/api/submissions", headers=riya, json={"title": "B", "text": "A zebra."}).json()["id"]
    assert any(f["original"] == "zebra" for f in c.get(f"/api/submissions/{s1}", headers=nikhil).json()["findings"])
    assert not any(f["original"] == "zebra" for f in c.get(f"/api/submissions/{s2}", headers=riya).json()["findings"])
    ins = c.get("/api/insights", headers=nora).json()
    assert all(r["ref"] != "TONE-4" or r["total"] < 3 for r in ins["rules"])
    fid = c.get(f"/api/submissions/{sid}", headers=riya).json()["findings"][0]["id"]
    assert c.post(f"/api/findings/{fid}/decide", headers=nora, json={"decision": "accept"}).status_code == 404   # cannot touch another workspace's findings
    assert c.post(f"/api/submissions/{sid}/comments", headers=nora, json={"body": "hi"}).status_code == 404

def test_create_own_workspace(client):
    r = client.post("/api/workspaces", json={"workspace": "Acme Foods", "name": "Asha K", "username": "asha_acme", "password": "secret1"})
    assert r.status_code == 200
    h = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get("/api/submissions", headers=h).json() == []
    kb = client.get("/api/kb", headers=h).json()
    assert kb["terms"] == [] and any(c["check_type"] == "emoji" for c in kb["clauses"]) and not kb["has_logo"]
    assert client.post("/api/workspaces", json={"workspace": "Other", "name": "Zed Z", "username": "asha_acme", "password": "secret1"}).status_code == 400
    assert client.post("/api/workspaces", json={"workspace": "X", "name": "Zed Z", "username": "zz", "password": "1"}).status_code == 422
    assert client.put("/api/kb/palette", headers=h, json={"colors": ["#112233", "#ffffff"]}).status_code == 200
    assert client.post("/api/users", headers=h, json={"username": "bob_acme", "name": "Bob B", "role": "creator", "password": "secret1"}).status_code == 200
    sid = client.post("/api/submissions", headers=h, json={"title": "t", "text": "Use #FF00AA", "channel": "Website"}).json()["id"]
    assert any(f["rule"] == "palette" for f in client.get(f"/api/submissions/{sid}", headers=h).json()["findings"])
