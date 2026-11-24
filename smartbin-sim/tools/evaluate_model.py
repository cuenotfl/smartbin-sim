"""Accuracy gate for a trained model against the test set (MUSS-01).

Test set layout (photos of real items, versioned with the project):

    testset/
      PET/  *.jpg
      ALU/  *.jpg
      REST/ *.jpg

    python tools/evaluate_model.py --model model.onnx --testset testset --labels ALU PET REST

Exit code 1 if overall accuracy < --min (default 0.90), so CI can block a bad model.
Needs: pip install -r requirements-ml.txt
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smartbin.adapters.onnx_classifier import OnnxClassifier  # noqa: E402
from smartbin.core.model import Fraction  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--testset", required=True)
    ap.add_argument("--labels", nargs="+", default=["ALU", "PET", "REST"],
                    help="fractions in the order of the model outputs")
    ap.add_argument("--threshold", type=float, default=0.6, help="confidence threshold")
    ap.add_argument("--min", type=float, default=0.90)
    args = ap.parse_args(argv)

    clf = OnnxClassifier(args.model, labels=args.labels)
    total = correct = 0
    per_class = {}
    for fraction in Fraction:
        folder = os.path.join(args.testset, fraction.value)
        if not os.path.isdir(folder):
            continue
        ok = n = 0
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            with open(os.path.join(folder, name), "rb") as fh:
                r = clf.classify(fh.read())
            hit = r.fraction == fraction and r.confidence >= args.threshold
            ok += hit
            n += 1
            if not hit:
                print("miss: %s/%s -> %s (%.2f)" % (fraction.value, name,
                                                   r.fraction.value if r.fraction else "-", r.confidence))
        per_class[fraction.value] = (ok, n)
        correct += ok
        total += n

    if total == 0:
        print("no images found in %s" % args.testset)
        return 1
    for k, (ok, n) in per_class.items():
        print("%-5s %5.1f %%  (%d/%d)" % (k, 100.0 * ok / max(n, 1), ok, n))
    acc = correct / total
    print("TOTAL %5.1f %%  (%d/%d)  target >= %.0f %%" % (100 * acc, correct, total, 100 * args.min))
    return 0 if acc >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
