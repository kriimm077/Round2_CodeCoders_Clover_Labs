import base64, io
from PIL import Image

def test_bad_input_does_not_break(client, H):
    c, riya = client, H["riya"]
    assert c.post("/api/submissions", headers=riya, json={"title": "", "text": "x"}).status_code == 400
    assert c.post("/api/submissions", headers=riya, json={"title": "Empty", "text": "   "}).status_code == 400
    assert c.post("/api/submissions", headers=riya, json={"title": "Long", "text": "a" * 20001}).status_code == 422
    assert c.post("/api/submissions", headers=riya, json={"title": "Odd", "channel": "Nonsense", "text": "Hello members."}).status_code == 200
    assert c.post("/api/submissions", headers=riya, json={"title": "Img", "text": "Hi", "image_b64": "!!notbase64!!"}).status_code == 400
    assert c.post("/api/submissions", headers=riya, json={"title": "Img", "text": "Hi", "image_b64": base64.b64encode(b"not an image").decode()}).status_code == 400
    assert c.post("/api/submissions", headers=riya, json="garbage").status_code == 422
    assert c.get("/api/submissions/999999", headers=riya).status_code == 404
    assert c.post("/api/findings/999999/decide", headers=riya, json={"decision": "accept"}).status_code == 404
    assert c.get("/api/submissions/abc", headers=riya).status_code == 422
    assert c.post("/api/login", json={"username": "x" * 500, "password": "y"}).status_code == 422
    assert c.get("/api/submissions", headers={"Authorization": "Bearer nonsense"}).status_code == 401

def test_unicode_and_markup_are_safe(client, H):
    c, riya = client, H["riya"]
    t = '<script>alert(1)</script> \u0939\u093f\u0928\u094d\u0926\u0940 text with \u201cquotes\u201d and emoji \U0001F600'
    sid = c.post("/api/submissions", headers=riya, json={"title": "<b>x</b>", "text": t}).json()["id"]
    d = c.get(f"/api/submissions/{sid}", headers=riya).json()
    assert d["version"]["text"] == t and any(f["rule"] == "emoji" for f in d["findings"])

def test_same_action_twice(client, H):
    c, riya = client, H["riya"]
    sid = c.post("/api/submissions", headers=riya, json={"title": "Twice", "text": "Our customers are great."}).json()["id"]
    f = c.get(f"/api/submissions/{sid}", headers=riya).json()["findings"][0]
    for _ in range(2):
        assert c.post(f"/api/findings/{f['id']}/decide", headers=riya, json={"decision": "accept"}).status_code == 200
    assert c.post(f"/api/submissions/{sid}/apply", headers=riya).status_code == 200
    assert c.post(f"/api/submissions/{sid}/apply", headers=riya).status_code == 400   # nothing left to apply
    assert c.post(f"/api/submissions/{sid}/version", headers=riya, json={"text": ""}).status_code == 400

def test_refresh_safe_read_endpoints(client, H):
    c, manav = client, H["manav"]
    for p in ("/api/submissions", "/api/kb", "/api/insights", "/api/quality", "/api/users", "/api/me"):
        a, b = c.get(p, headers=manav), c.get(p, headers=manav)
        assert a.status_code == b.status_code == 200

def test_demo_seed_is_idempotent_and_self_healing(client):
    from app import main, db
    c = db.connect()
    try:
        n = c.scalar("SELECT COUNT(*) FROM submissions WHERE workspace_id=1")
    finally:
        c.close()
    main.seed_demo(); main.seed_demo()          # running again must not duplicate demo content
    c = db.connect()
    try:
        assert c.scalar("SELECT COUNT(*) FROM submissions WHERE workspace_id=1") == n
    finally:
        c.close()
