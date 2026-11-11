import base64
def b64(png): return "data:image/png;base64," + base64.b64encode(png).decode()

def test_image_review_end_to_end(client, H):
    c, riya = client, H["riya"]
    bad = c.get("/api/samples/image/bad", headers=riya).content; good = c.get("/api/samples/image/good", headers=riya).content
    assert bad[:4] == b"\x89PNG" and good[:4] == b"\x89PNG"
    s = c.post("/api/submissions", headers=riya, json={"title": "Bad poster", "channel": "Social media", "text": "", "image_b64": b64(bad), "image_kind": "design", "image_alt": ""}).json()["id"]
    d = c.get(f"/api/submissions/{s}", headers=riya).json(); rules = sorted(f["rule"] for f in d["findings"])
    assert rules == ["alt", "imgpalette", "imgquality", "imgquality", "logo"], rules
    assert all(f["source_ref"] for f in d["findings"]) and d["version"]["has_image"]
    assert c.get(f"/api/submissions/{s}/image", headers=riya).headers["content-type"] == "image/png"
    g = c.post("/api/submissions", headers=riya, json={"title": "Good poster", "channel": "Social media", "text": "", "image_b64": b64(good), "image_alt": "A promotional poster for the monsoon snack box"}).json()["id"]
    assert c.get(f"/api/submissions/{g}", headers=riya).json()["findings"] == []
    # editing text keeps the image and re-reviews
    v = c.post(f"/api/submissions/{g}/version", headers=riya, json={"text": "Our customers love it."}).json()["version"]
    d2 = c.get(f"/api/submissions/{g}", headers=riya).json()
    assert v == 2 and d2["version"]["has_image"] and any(f["rule"] == "term" for f in d2["findings"])
    # photos skip palette and logo checks; low resolution still caught
    from PIL import Image; import io
    buf = io.BytesIO(); Image.new("RGB", (300, 300), (200, 30, 120)).save(buf, "PNG")
    p = c.post("/api/submissions", headers=riya, json={"title": "Photo", "channel": "Website", "text": "", "image_b64": b64(buf.getvalue()), "image_kind": "photo", "image_alt": "A magenta test photograph"}).json()["id"]
    assert [f["rule"] for f in c.get(f"/api/submissions/{p}", headers=riya).json()["findings"]] == ["imgquality"]

def test_logo_detection_unit():
    import io
    from PIL import Image
    from app import vision, db
    logo_png = vision.make_logo(db.DEFAULT_PALETTE); logo = Image.open(io.BytesIO(logo_png)).convert("RGB")
    canvas = Image.new("RGB", (900, 600), (245, 241, 232)); canvas.paste(logo.resize((120, 120)), (500, 300))
    r = vision.find_logo(canvas, logo)
    assert r["found"] and abs(r["x"] - 500) < 15 and abs(r["y"] - 300) < 15 and 100 < r["w"] < 140
    assert not vision.find_logo(Image.new("RGB", (900, 600), (245, 241, 232)), logo)["found"]
