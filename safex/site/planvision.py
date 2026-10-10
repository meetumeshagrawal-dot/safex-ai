"""Read an uploaded floor plan and propose room rectangles.

Be clear about what this is: turning an arbitrary floor-plan image into a clean
vector building is an unsolved problem, and pretending otherwise would produce
a map an evacuation router walks people through walls on. What *is* reliable is
finding the closed, roughly rectangular regions a plan is drawn with — so this
proposes those, and the operator accepts, renames, merges or redraws them.

The pipeline is deliberately plain:
    grayscale -> adaptive threshold -> close gaps (doorways) -> contours ->
    keep rectangle-ish regions of a sensible size -> drop nested duplicates
Everything comes back in normalised 0-1 plan coordinates, which is what the
site model stores.
"""
from __future__ import annotations
from typing import Dict, List

import cv2
import numpy as np

# A room has to be at least this fraction of the plan, and no bigger than this
# — smaller is furniture or a label, bigger is the building outline itself.
MIN_AREA_FRAC = 0.004
MAX_AREA_FRAC = 0.40
MIN_SIDE_FRAC = 0.03
MAX_SUGGESTIONS = 60


def _iou(a: List[float], b: List[float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def detect_room_rects(path: str, sensitivity: float = 1.0,
                      **_ignored) -> List[Dict]:
    """Propose rooms for a floor-plan image, as normalised rectangles."""
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("unreadable image")

    h, w = img.shape[:2]
    scale = 1200.0 / max(h, w)
    if scale < 1.0:                      # big plans: work smaller, it's faster
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]

    img = cv2.GaussianBlur(img, (3, 3), 0)
    # walls are dark lines on light paper; adaptive keeps working on photos of
    # a printed plan, where the lighting is never even
    walls = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                  cv2.THRESH_BINARY_INV, 25, 12)
    k = max(2, int(round(3 * max(0.3, min(3.0, sensitivity)))))
    # close doorways and hatching so a room reads as one enclosed region
    walls = cv2.morphologyEx(walls, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    rooms = cv2.bitwise_not(walls)
    rooms = cv2.morphologyEx(rooms, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))

    contours, _ = cv2.findContours(rooms, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    plan_area = float(w * h)
    found: List[Dict] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < plan_area * MIN_AREA_FRAC or area > plan_area * MAX_AREA_FRAC:
            continue
        x, y, bw_, bh_ = cv2.boundingRect(c)
        if bw_ < w * MIN_SIDE_FRAC or bh_ < h * MIN_SIDE_FRAC:
            continue
        # rectangle-ish: the contour should fill most of its bounding box
        fill = area / float(bw_ * bh_)
        if fill < 0.55:
            continue
        found.append({
            "rect": [round(x / w, 4), round(y / h, 4),
                     round(bw_ / w, 4), round(bh_ / h, 4)],
            "fill": round(fill, 2),
            "area": round(area / plan_area, 4),
        })

    # biggest first, then drop anything that mostly repeats a bigger one
    found.sort(key=lambda r: -r["area"])
    kept: List[Dict] = []
    for cand in found:
        if all(_iou(cand["rect"], k2["rect"]) < 0.35 for k2 in kept):
            kept.append(cand)
        if len(kept) >= MAX_SUGGESTIONS:
            break
    return kept
