"""Reliability layer: labelled evaluation set -> precision/recall per rule, and confidence calibration from eval results + live human decisions."""
import json, os, io, threading
from collections import Counter, defaultdict
from . import engine, db, vision

ROOT = os.path.dirname(os.path.dirname(__file__))
DATASET = os.path.join(ROOT, "eval", "dataset.json")
STRESS = os.path.join(ROOT, "eval", "stress.json")
_CACHE = {}

def _norm(s):
    return (s or "").strip().lower()

def _image_cases():
    from PIL import Image, ImageDraw
    pal = db.DEFAULT_PALETTE; logo_png = vision.make_logo(pal)
    good = vision.open_image(vision.make_sample("good", pal, logo_png)); bad = vision.open_image(vision.make_sample("bad", pal, logo_png))
    logo = Image.open(io.BytesIO(logo_png)).convert("RGB"); ALT = "A promotional graphic for the monsoon snack box"
    crowded = good.copy(); ImageDraw.Draw(crowded).rectangle((250, 80, 420, 230), fill=(26, 26, 26))
    blank = Image.new("RGB", (1080, 1080), (245, 241, 232))
    stretched = Image.new("RGB", (1080, 1080), (245, 241, 232)); stretched.paste(logo.resize((300, 120)), (100, 100))
    photo = Image.new("RGB", (400, 400), (120, 90, 60))
    tall = vision.open_image(vision.make_sample("good45", pal, logo_png))
    return [("img-01 on-brand design", good, "design", ALT, "Social media", []),
            ("img-02 off-brand low-res design", bad, "design", ALT, "Social media", ["imgquality", "imgquality", "imgpalette", "logo"]),
            ("img-03 missing alt text", good, "design", "", "Website", ["alt"]),
            ("img-04 crowded logo", crowded, "design", ALT, "Website", ["logo"]),
            ("img-05 logo missing", blank, "design", ALT, "Website", ["logo"]),
            ("img-06 low-res photo", photo, "photo", ALT, "Website", ["imgquality"]),
            ("img-07 stretched logo", stretched, "design", ALT, "Website", ["logo"]),
            ("img-08 square design on website", good, "design", ALT, "Website", []),
            ("img-09 4:5 design on social", tall, "design", ALT, "Social media", []),
            ("img-10 vague alt text", good, "design", "image", "Website", ["alt"])], logo

def _score(stats):
    rules = []
    for r, s in sorted(stats.items()):
        tp, fp, fn = s["tp"], s["fp"], s["fn"]
        p = tp / (tp + fp) if tp + fp else None; rc = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * rc / (p + rc) if p and rc else (0.0 if (p is not None and rc is not None) else None)
        rules.append({"rule": r, "tp": tp, "fp": fp, "fn": fn, "precision": None if p is None else round(p, 3), "recall": None if rc is None else round(rc, 3), "f1": None if f1 is None else round(f1, 3), "support": tp + fn})
    TP = sum(s["tp"] for s in stats.values()); FP = sum(s["fp"] for s in stats.values()); FN = sum(s["fn"] for s in stats.values())
    mp = TP / (TP + FP) if TP + FP else 0; mr = TP / (TP + FN) if TP + FN else 0
    return rules, {"tp": TP, "fp": FP, "fn": FN, "precision": round(mp, 3), "recall": round(mr, 3), "f1": round(2 * mp * mr / (mp + mr), 3) if mp + mr else 0}

def _run_text(path, kb, stats):
    fails = []; data = json.load(open(path, encoding="utf-8"))
    for case in data:
        preds = [(f["rule"], _norm(f["original"])) for f in engine.run_rules(case["text"], case.get("colors", ""), kb, case.get("channel", ""))]
        exp = [(r, _norm(t)) for r, t in case["expect"]]; used = [False] * len(preds); missed = []
        for r, t in exp:
            hit = next((i for i, (pr, pt) in enumerate(preds) if not used[i] and pr == r and (t == "*" or pt == t)), None)
            if hit is None:
                stats[r]["fn"] += 1; missed.append(f"{r}:{t}")
            else:
                used[hit] = True; stats[r]["tp"] += 1
        extra = []
        for i, (pr, pt) in enumerate(preds):
            if not used[i]:
                stats[pr]["fp"] += 1; extra.append(f"{pr}:{pt}")
        if missed or extra:
            fails.append({"id": case["id"], "text": case["text"][:80], "missed": missed, "extra": extra})
    return len(data), fails

def evaluate():
    """Main labelled set (text + generated images) and a separate adversarial 'stress' set. Both feed the calibration priors."""
    kb = db.default_kb(); main = defaultdict(Counter); stress = defaultdict(Counter)
    n_text, fails = _run_text(DATASET, kb, main)
    icases, logo = _image_cases(); cl = {c["check_type"]: c for c in kb["clauses"]}
    for name, img, kind, alt, ch, exp in icases:
        preds = [f["rule"] for f in vision.review_image(img, kind, alt, ch, dict(cl, logo_img=logo))]
        c_exp, c_pred = Counter(exp), Counter(preds)
        for r in set(c_exp) | set(c_pred):
            tp = min(c_exp[r], c_pred[r]); main[r]["tp"] += tp; main[r]["fn"] += c_exp[r] - tp; main[r]["fp"] += c_pred[r] - tp
        if c_exp != c_pred:
            fails.append({"id": name, "text": "(image)", "missed": list((c_exp - c_pred).elements()), "extra": list((c_pred - c_exp).elements())})
    n_stress, sfails = _run_text(STRESS, kb, stress)
    rules, overall = _score(main); srules, soverall = _score(stress)
    comb = defaultdict(Counter)
    for src in (main, stress):
        for r, s in src.items():
            comb[r].update(s)
    crules, coverall = _score(comb)
    return {"main": {"cases": n_text + len(icases), "text_cases": n_text, "image_cases": len(icases), "rules": rules, "overall": overall, "failures": fails},
            "stress": {"cases": n_stress, "rules": srules, "overall": soverall, "failures": sfails},
            "combined_rules": crules, "combined_overall": coverall}

_LOCK = threading.Lock()

def get_eval(force=False):
    with _LOCK:
        if force or "eval" not in _CACHE:
            _CACHE["eval"] = evaluate()
        return _CACHE["eval"]

def prior_precision(rule):
    for r in get_eval()["combined_rules"]:
        if r["rule"] == rule and r["precision"] is not None and r["tp"] + r["fp"] >= 3:
            return r["precision"]
    return None

K = 6  # strength of the prior, in pseudo-observations

def calibrate(rule, raw, accepted=0, dismissed=0):
    """Blend the evaluation precision (prior) with how often humans accept this rule's findings in this workspace."""
    prior = prior_precision(rule) if rule != "ai" else None
    p0 = prior if prior is not None else raw
    n = accepted + dismissed
    c = (p0 * K + accepted) / (K + n)
    return round(min(0.95, max(0.05, c)), 2), p0

def live_stats(c, ws):
    rows = c.rows("""SELECT f.rule AS rule, SUM(CASE WHEN f.status IN ('accepted','edited') THEN 1 ELSE 0 END) AS accepted, SUM(CASE WHEN f.status='dismissed' THEN 1 ELSE 0 END) AS dismissed,
                     COUNT(*) AS total FROM findings f JOIN submissions s ON s.id=f.submission_id WHERE s.workspace_id=? GROUP BY f.rule""", (ws,))
    return {r["rule"]: (int(r["accepted"] or 0), int(r["dismissed"] or 0), int(r["total"] or 0)) for r in rows}
