"""
Train the fog/steam/smoke discriminator.

    python -m safex.vision.fogsmoke.train

Positives come from the fire dataset's own smoke boxes. Negatives are rendered
from the same dataset's fire-only and background images with synth.py, so the
two classes share cameras, lighting and compression — the classifier cannot win
by learning "dataset A looks like this".

Training uses the dataset's train and valid splits; every number reported is
measured on its test split, which is never built into the training matrix. Two
traps this setup exists to avoid, both of which produced flattering nonsense
first time round:

  * scoring on a random slice of one pile, when the same photographs are in
    both halves;
  * letting the negatives differ from the positives in *shape* rather than in
    optics — the fog renderers veil the whole frame, so if their boxes are left
    whole-frame the model learns "large region = fog", scores 97%, and is
    useless against a detector that only ever emits small boxes.

The confusion matrix is printed in full because the only number that really
matters for a fire system is how often it throws away real smoke.
"""
from __future__ import annotations
import glob
import os
import sys
import time

import cv2
import numpy as np

from .features import extract, FEATURE_NAMES, N_FEATURES
from . import synth

DATASET = os.path.join("fire_dataset", "Fire Detection.v1i.yolov8")
OUT_PATH = os.path.join("safex", "vision", "fogsmoke", "fogsmoke.joblib")

SMOKE_CLASS = 1          # data.yaml: ['fire', 'smoke']
FIRE_CLASS = 0
SEED = 20260823


def _label_boxes(label_path, w, h):
    """YOLO normalised (cls cx cy bw bh) -> [(cls, x1, y1, x2, y2)] in pixels."""
    out = []
    if not os.path.exists(label_path):
        return out
    for line in open(label_path, encoding="utf-8"):
        p = line.split()
        if len(p) < 5:
            continue
        c = int(float(p[0]))
        cx, cy, bw, bh = (float(v) for v in p[1:5])
        x1 = int((cx - bw / 2) * w)
        y1 = int((cy - bh / 2) * h)
        x2 = int((cx + bw / 2) * w)
        y2 = int((cy + bh / 2) * h)
        out.append((c, x1, y1, x2, y2))
    return out


def _pairs(split):
    """(image path, label path) for one dataset split."""
    imgs = sorted(glob.glob(os.path.join(DATASET, split, "images", "*")))
    pairs = []
    for ip in imgs:
        stem = os.path.splitext(os.path.basename(ip))[0]
        lp = os.path.join(DATASET, split, "labels", stem + ".txt")
        pairs.append((ip, lp))
    return pairs


def _smoke_box_shapes(splits):
    """The (width, height) of every real smoke box, as fractions of the frame.

    Negatives are cropped to boxes drawn from this same distribution. Without
    that, the fog renderers hand back a whole-frame box while smoke boxes are
    small, and the classifier learns "big region = fog" — which scores 97% here
    and is worthless in the plant, because YOLO never returns a whole-frame box.
    The geometry has to carry no signal so the optics have to do the work.
    """
    shapes = []
    for split in splits:
        for ip, lp in _pairs(split):
            if not os.path.exists(lp):
                continue
            for line in open(lp, encoding="utf-8"):
                p = line.split()
                if len(p) >= 5 and int(float(p[0])) == SMOKE_CLASS:
                    shapes.append((float(p[3]), float(p[4])))
    return shapes or [(0.3, 0.3)]


def _sample_box(shapes, rng, w, h):
    """A box with a real smoke box's shape, placed anywhere in the frame."""
    bw, bh = shapes[rng.integers(len(shapes))]
    pw, ph = max(16, int(bw * w)), max(16, int(bh * h))
    pw, ph = min(pw, w - 2), min(ph, h - 2)
    x1 = int(rng.integers(0, max(1, w - pw)))
    y1 = int(rng.integers(0, max(1, h - ph)))
    return x1, y1, x1 + pw, y1 + ph


def build(splits=("train", "valid", "test"), verbose=True):
    """Feature matrix X, labels y (1 = fire smoke, 0 = water/haze), and the
    source image each row came from, so the split can respect image identity."""
    rng = np.random.default_rng(SEED)
    X, y, src = [], [], []
    n_smoke = n_fog = n_steam = n_lens = 0
    shapes = _smoke_box_shapes(splits)

    for split in splits:
        for ip, lp in _pairs(split):
            img = cv2.imread(ip)
            if img is None:
                continue
            h, w = img.shape[:2]
            boxes = _label_boxes(lp, w, h)
            smoke_boxes = [b for b in boxes if b[0] == SMOKE_CLASS]

            # --- positives: real smoke, exactly as the detector would see it --
            for _, x1, y1, x2, y2 in smoke_boxes:
                if (x2 - x1) < 16 or (y2 - y1) < 16:
                    continue
                X.append(extract(img, (x1, y1, x2, y2)))
                y.append(1)
                src.append(ip)
                n_smoke += 1

            # --- negatives: render a confuser onto a frame with NO real smoke -
            # Rendering fog over an image that already contains smoke would put
            # both classes in one crop and teach the model nothing.
            if smoke_boxes:
                continue
            kind = rng.choice(["fog", "steam", "lens"], p=[0.4, 0.4, 0.2])
            want = _sample_box(shapes, rng, w, h)
            if kind == "steam":
                # draw the plume at a real smoke box's size and place
                out, box = synth.steam(img, rng, box=want)
            else:
                # fog and lens haze veil the whole frame; take a smoke-shaped
                # window of it, the way a detector would have boxed the haze
                out, _ = synth.RENDERERS[kind](img, rng)
                box = want
            if (box[2] - box[0]) < 16 or (box[3] - box[1]) < 16:
                continue
            X.append(extract(out, box))
            y.append(0)
            src.append(ip)
            n_fog += kind == "fog"
            n_steam += kind == "steam"
            n_lens += kind == "lens"

    if verbose:
        print(f"  smoke (positive) : {n_smoke}")
        print(f"  fog              : {n_fog}")
        print(f"  steam            : {n_steam}")
        print(f"  lens haze        : {n_lens}")
    return np.array(X, np.float32), np.array(y, np.int32), np.array(src)


def main():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import (accuracy_score, confusion_matrix,
                                 precision_recall_fscore_support, roc_auc_score)
    import joblib

    t0 = time.time()
    # The dataset's own test split is never built into the training matrix, so
    # the numbers below are measured on images the model has not seen in any
    # form — not a random slice of the same pile.
    print("building train set (dataset splits: train + valid)...")
    X, y, src = build(splits=("train", "valid"))
    print(f"  rows             : {len(X)}  ({time.time() - t0:.1f}s)")
    print("building held-out set (dataset split: test)...")
    Xte, yte, _ = build(splits=("test",))
    print(f"  rows             : {len(Xte)}")
    if len(X) < 50 or len(Xte) < 20:
        print("not enough data — is fire_dataset/ present?")
        return 1

    clf = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.08, max_depth=6,
        l2_regularization=1.0, random_state=SEED,
    )
    print("training...")
    clf.fit(X, y)

    prob = clf.predict_proba(Xte)[:, 1]
    pred = (prob >= 0.5).astype(int)
    acc = accuracy_score(yte, pred)
    auc = roc_auc_score(yte, prob)
    pr, rc, f1, _ = precision_recall_fscore_support(yte, pred, average="binary")
    cm = confusion_matrix(yte, pred)

    print("\nheld-out results (1 = fire smoke, 0 = fog/steam/lens)")
    print(f"  accuracy         : {acc:.3f}")
    print(f"  roc auc          : {auc:.3f}")
    print(f"  smoke precision  : {pr:.3f}")
    print(f"  smoke recall     : {rc:.3f}   <-- missed fires live here")
    print(f"  f1               : {f1:.3f}")
    print(f"  confusion [[tn fp][fn tp]] : {cm.tolist()}")

    # Permutation importance says which physics actually carried the decision.
    try:
        from sklearn.inspection import permutation_importance
        imp = permutation_importance(clf, Xte, yte, n_repeats=8,
                                     random_state=SEED, n_jobs=1)
        order = np.argsort(imp.importances_mean)[::-1][:8]
        print("\n  top features by permutation importance")
        for i in order:
            print(f"    {FEATURE_NAMES[i]:16s} {imp.importances_mean[i]:+.4f}")
    except Exception as e:                          # never fail the run for this
        print(f"  (importance skipped: {e})")

    joblib.dump({
        "model": clf,
        "feature_names": FEATURE_NAMES,
        "n_features": N_FEATURES,
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "metrics": {"accuracy": float(acc), "roc_auc": float(auc),
                    "precision": float(pr), "recall": float(rc), "f1": float(f1),
                    "confusion": cm.tolist(), "n_train": int(len(X)),
                    "n_test": int(len(Xte)),
                    "holdout": "dataset test split, unseen in training"},
    }, OUT_PATH)
    print(f"\nsaved -> {OUT_PATH}  ({os.path.getsize(OUT_PATH) / 1024:.0f} KB)")
    print(f"total {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
