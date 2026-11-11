"""Run the labelled evaluation from the command line:  python -m eval.run"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import quality

def show(title, part):
    print(f"\n{title}: {part['cases']} cases | precision {part['overall']['precision']:.3f} recall {part['overall']['recall']:.3f} f1 {part['overall']['f1']:.3f}")
    print(f"{'rule':12} {'P':>6} {'R':>6} {'F1':>6} {'TP':>4} {'FP':>4} {'FN':>4}")
    for r in part["rules"]:
        f = lambda v: "  -  " if v is None else f"{v:.3f}"
        print(f"{r['rule']:12} {f(r['precision']):>6} {f(r['recall']):>6} {f(r['f1']):>6} {r['tp']:>4} {r['fp']:>4} {r['fn']:>4}")
    for x in part["failures"]:
        print("  miss/false alarm:", x["id"], "missed", x["missed"], "extra", x["extra"])

if __name__ == "__main__":
    ev = quality.evaluate()
    show("MAIN SET", ev["main"]); show("STRESS SET", ev["stress"])
