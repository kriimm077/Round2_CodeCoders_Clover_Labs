"""Image and visual-consistency review: palette, logo size and clear space (FFT template matching), resolution and aspect ratio, alt text."""
import io, json, os, base64, urllib.request
import numpy as np
from PIL import Image, ImageDraw, ImageFont

MAX_BYTES = 5_000_000

def hex_(rgb):
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb)

def rgb_(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def open_image(data):
    img = Image.open(io.BytesIO(data)); img.load()
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA"); bg = Image.new("RGBA", img.size, (255, 255, 255, 255)); bg.alpha_composite(img); img = bg
    return img.convert("RGB")

def normalize(data, kind="design"):
    """Validate and normalise an uploaded image. Returns (bytes, mime, width, height)."""
    if len(data) > MAX_BYTES:
        raise ValueError("Image is larger than 5 MB")
    try:
        img = open_image(data)
    except Exception:
        raise ValueError("This file is not a readable image (use PNG or JPG)")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    if kind == "photo":
        img.save(buf, "JPEG", quality=88); mime = "image/jpeg"
    else:
        img.save(buf, "PNG", optimize=True); mime = "image/png"
    return buf.getvalue(), mime, img.size[0], img.size[1]

def dominant(img, k=8):
    s = img.copy(); s.thumbnail((160, 160))
    q = s.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette(); cols = q.getcolors() or []
    total = sum(n for n, _ in cols) or 1
    out = [(tuple(pal[i * 3:i * 3 + 3]), n / total) for n, i in cols]
    return sorted(out, key=lambda x: -x[1])

def off_palette(img, pal, tol=60, min_share=0.08):
    """Colours covering >= min_share of the image that are farther than tol from every approved colour."""
    s = img.copy(); s.thumbnail((200, 200))
    arr = np.asarray(s, dtype=np.float64).reshape(-1, 3)
    P = np.array([rgb_(p) for p in pal], dtype=np.float64)
    d = np.sqrt(((arr[:, None, :] - P[None, :, :]) ** 2).sum(2))
    off = d.min(1) > tol
    if off.mean() < min_share:
        return []
    px = arr[off].astype(np.uint8).reshape(1, -1, 3)
    q = Image.fromarray(px).quantize(colors=3, method=Image.Quantize.MEDIANCUT)
    pal3 = q.getpalette(); out = []
    for n, i in sorted(q.getcolors(), reverse=True):
        share = n / arr.shape[0]
        if share >= min_share / 2:
            rgb = tuple(pal3[i * 3:i * 3 + 3]); near = min(pal, key=lambda p: _dist(rgb_(p), rgb))
            out.append((hex_(rgb), round(share * 100), near))
    return out

def _dist(a, b):
    return float(sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5)

def _ncc_best(I, T):
    H, W = I.shape; h, w = T.shape
    if h < 6 or w < 6 or h >= H or w >= W:
        return None
    t0 = T - T.mean(); tn = float(np.sqrt((t0 ** 2).sum()))
    if tn < 1e-6:
        return None
    fs = (H + h, W + w)
    corr = np.fft.irfft2(np.fft.rfft2(I, fs) * np.conj(np.fft.rfft2(t0, fs)), fs)[:H - h + 1, :W - w + 1]
    ii = np.pad(I, ((1, 0), (1, 0))).cumsum(0).cumsum(1); ii2 = np.pad(I * I, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    ws = lambda a: a[h:H + 1, w:W + 1] - a[0:H - h + 1, w:W + 1] - a[h:H + 1, 0:W - w + 1] + a[0:H - h + 1, 0:W - w + 1]
    n = h * w; var = ws(ii2) - ws(ii) ** 2 / n
    ncc = np.where(var > 1e-3 * n, corr / (np.sqrt(np.maximum(var, 1e-9)) * tn), 0.0)
    y, x = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
    return float(ncc[y, x]), int(x), int(y)

LOGO_SCALES = [round(0.03 * 1.07 ** i, 4) for i in range(0, 41) if 0.03 * 1.07 ** i <= 0.5]

def find_logo(img, logo, threshold=0.6):
    W0, H0 = img.size
    s = min(1.0, 420 / max(W0, H0))
    small = img.resize((max(8, int(W0 * s)), max(8, int(H0 * s))), Image.LANCZOS).convert("L")
    I = np.asarray(small, dtype=np.float64)
    lg = logo.convert("L"); best = None
    for wp in LOGO_SCALES:
        tw = int(small.size[0] * wp); th = int(round(tw * lg.size[1] / lg.size[0]))
        if tw < 8 or th < 8:
            continue
        r = _ncc_best(I, np.asarray(lg.resize((tw, th), Image.LANCZOS), dtype=np.float64))
        if r and (best is None or r[0] > best[0]):
            best = (r[0], r[1], r[2], tw, th)
    if not best or best[0] < threshold:
        return {"found": False, "score": round(best[0], 2) if best else 0.0}
    sc, x, y, tw, th = best
    return {"found": True, "score": round(sc, 2), "x": int(x / s), "y": int(y / s), "w": int(tw / s), "h": int(th / s)}

def clear_space_ok(img, box, ratio):
    W, H = img.size; x, y, w, h = box["x"], box["y"], box["w"], box["h"]; m = int(ratio * w)
    x0, y0, x1, y1 = x - m, y - m, x + w + m, y + h + m
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        return False, "the logo is closer to the image edge than the required clear space"
    g = np.asarray(img.convert("L"), dtype=np.float64)[y0:y1, x0:x1].copy()
    t = max(2, int(0.08 * w)); mask = np.ones(g.shape, bool); mask[max(0, m - t):m + h + t, max(0, m - t):m + w + t] = False  # tolerance for detection error
    ring = g[mask]; med = np.median(ring)
    if float((np.abs(ring - med) > 40).mean()) > 0.04:
        return False, "other graphics or text intrude into the clear space around the logo"
    return True, ""

def review_image(img, kind, alt, channel, params):
    """params: dict with clause dicts by check type (palette, logo, imgquality, alt) and optional logo_img. Returns findings."""
    out = []
    W, H = img.size
    def add(rule, clause, sev, expl, sugg, conf, meta=None):
        out.append(dict(rule=rule, category=clause["category"], severity=sev, start=-1, end=-1, original="", explanation=expl, source_ref=clause["ref"],
                        source_title=clause["title"], source_text=clause["text"], confidence=conf, suggestion=sugg, origin="rule", meta=meta or {}))
    P = lambda k: json.loads(params[k]["params"] or "{}") if k in params else {}
    if "alt" in params:
        a = (alt or "").strip()
        if len(a) < 8 or a.lower() in ("image", "photo", "picture", "img", "banner", "graphic"):
            add("alt", params["alt"], "high", "This image has no meaningful alternative text. Describe what it shows for screen-reader users." if not a else f"Alt text \u201c{a}\u201d is too vague. Describe what the image shows.", "Describe what this image shows", 0.95)
    if "imgquality" in params:
        q = P("imgquality"); c = params["imgquality"]
        if W < q.get("min_width", 600):
            add("imgquality", c, "medium", f"The image is {W} px wide; the minimum is {q.get('min_width', 600)} px, so it will look blurry.", f"Export at {q.get('min_width', 600)} px wide or larger", 0.95)
        if channel == "Social media" and q.get("social_ratios"):
            r = W / H
            if not any(abs(r - a) / a <= 0.03 for a in q["social_ratios"]):
                near = min(q["social_ratios"], key=lambda a: abs(r - a))
                add("imgquality", c, "medium", f"Aspect ratio is {r:.2f}:1; social images must be 1:1, 4:5 or 1.91:1 (nearest: {near}:1).", f"Resize the canvas to {near}:1", 0.9)
    if kind == "design":
        if "palette" in params:
            pal = P("palette").get("colors", [])
            if pal:
                bad = off_palette(img, pal)
                if bad:
                    add("imgpalette", params["palette"], "medium", "Large areas use colours outside the approved palette: " + "; ".join(f"{c} ({s}% of the image, nearest approved {n})" for c, s, n in bad) + ".",
                        "Recolour with the approved palette", 0.8, {"colors": [{"hex": c, "share": s, "near": n} for c, s, n in bad]})
        logo = params.get("logo_img")
        if "logo" in params and logo is not None:
            lp = P("logo"); res = find_logo(img, logo)
            c = params["logo"]
            if not res["found"]:
                add("logo", c, "medium", "The approved logo was not detected. It may be missing, recoloured, stretched or too small to recognise. Check the design.", "Place the approved logo unmodified", 0.65, {"score": res["score"]})
            else:
                box = {k: res[k] for k in ("x", "y", "w", "h")}; meta = {"bbox": {"x": box["x"] / W, "y": box["y"] / H, "w": box["w"] / W, "h": box["h"] / H}, "score": res["score"]}
                pct = box["w"] / W
                if pct < lp.get("min_width_pct", 0.08):
                    add("logo", c, "medium", f"The logo is {pct * 100:.0f}% of the image width; the minimum is {lp.get('min_width_pct', 0.08) * 100:.0f}%.", "Enlarge the logo", 0.85, meta)
                ok, why = clear_space_ok(img, box, lp.get("clear_space", 0.25))
                if not ok:
                    add("logo", c, "medium", f"Logo clear space is not respected: {why}.", "Move the logo or remove nearby elements", 0.8, meta)
    return out

# ---------- sample assets (used for the seeded logo and the 'try a sample image' buttons) ----------
def _font(size):
    return ImageFont.load_default(size=size)

def make_logo(palette):
    g, y = palette[0], palette[1]
    im = Image.new("RGB", (240, 240), g); d = ImageDraw.Draw(im)
    d.ellipse((30, 30, 210, 210), fill=y)
    d.rectangle((60, 60, 90, 180), fill=g); d.polygon([(90, 60), (150, 60), (170, 80), (170, 100), (150, 118), (90, 118)], fill=g)
    d.polygon([(90, 118), (155, 118), (180, 138), (180, 165), (155, 180), (90, 180)], fill=g)
    d.ellipse((100, 78, 140, 100), fill=y); d.ellipse((100, 138, 150, 160), fill=y)
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue()

def make_sample(kind, palette, logo_png):
    logo = Image.open(io.BytesIO(logo_png)).convert("RGB")
    cream, green, yel, ink = [rgb_(p) for p in (palette[4], palette[0], palette[1], palette[3])]
    if kind in ("good", "good45"):
        W = 1080 if kind == "good" else 864
        im = Image.new("RGB", (W, 1080), cream); d = ImageDraw.Draw(im)
        d.rectangle((0, 800, W, 1080), fill=green); d.ellipse((W - 380, 250, W - 80, 550), fill=yel)
        lg = logo.resize((170, 170)); im.paste(lg, (70, 70))
        d.text((70, 380), "Monsoon Box", fill=ink, font=_font(110)); d.text((70, 540), "Healthy snacks, delivered monthly", fill=ink, font=_font(44))
        d.text((70, 880), "Order at brightbox.example", fill=(255, 255, 255), font=_font(52))
    else:
        im = Image.new("RGB", (500, 300), (216, 27, 154)); d = ImageDraw.Draw(im)
        d.rectangle((0, 180, 500, 300), fill=(0, 220, 230))
        for i in range(0, 500, 14):
            d.line((i, 0, i + 60, 180), fill=(255, 235, 0), width=5)
        lg = logo.resize((22, 22)); im.paste(lg, (462, 264))
        d.text((20, 40), "MONSOON SALE", fill=(255, 255, 255), font=_font(44))
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue()

def ai_image_review(png_bytes, alt, clauses, channel):
    """Optional vision-model layer. Returns (findings, alt_suggestion, status). Only runs when ANTHROPIC_API_KEY is set."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return [], "", "Vision AI not configured"
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
    try:
        img = open_image(png_bytes); img.thumbnail((1024, 1024)); b = io.BytesIO(); img.save(b, "JPEG", quality=80)
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}},
                   {"type": "text", "text": json.dumps({"channel": channel, "author_alt_text": alt, "guidelines": [{"ref": c["ref"], "title": c["title"], "text": c["text"]} for c in clauses if c["category"] in ("Visual", "Accessibility", "Tone", "Messaging")]})}]
        system = ("You review brand images using ONLY the provided guidelines. Return ONLY JSON: {\"alt_text\": <a one-sentence description of the image for screen readers>, "
                  "\"issues\": [{\"source_ref\": <guideline ref>, \"issue\": <one sentence>, \"severity\": \"low|medium|high\", \"confidence\": 0-1}]} with at most 4 issues. Do not invent rules.")
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", json.dumps({"model": model, "max_tokens": 800, "system": system, "messages": [{"role": "user", "content": content}]}).encode(),
                                     {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        data = json.loads(urllib.request.urlopen(req, timeout=40).read())
        raw = "".join(x.get("text", "") for x in data.get("content", [])); raw = raw[raw.find("{"): raw.rfind("}") + 1]; res = json.loads(raw)
    except Exception as ex:
        return [], "", f"Vision AI unavailable ({type(ex).__name__})"
    refs = {c["ref"]: c for c in clauses}; out = []
    for it in res.get("issues", [])[:4]:
        c = refs.get(it.get("source_ref"))
        if not c:
            continue
        sev = it.get("severity") if it.get("severity") in ("low", "medium", "high") else "medium"
        out.append(dict(rule="ai", category=c["category"], severity=sev, start=-1, end=-1, original="", explanation=str(it.get("issue", "")), source_ref=c["ref"], source_title=c["title"],
                        source_text=c["text"], confidence=min(0.7, float(it.get("confidence", 0.6))), suggestion="", origin="ai", meta={}))
    return out, str(res.get("alt_text", ""))[:300], f"Vision AI used ({model}); {len(out)} verified finding(s)"
