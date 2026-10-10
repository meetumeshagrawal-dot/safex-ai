"""Camera ↔ floor-plan calibration.

Everything the platform knows from a camera is in *that camera's* pixels: a
person is at (312, 208), a corridor is "41% obstructed". Everything the
operator works with is on the *plan*: a room, a corridor, a width in metres.
Without a mapping between the two, the platform can only ever talk in
percentages and can never say where in the building something actually is.

This is that mapping. The operator clicks four points visible in the camera
image and the same four points on the floor plan — the corners of a corridor,
the corners of a room, floor tiles, anything flat and identifiable — and a
homography is fitted between the two planes. A homography is exactly the right
model here: both the floor and the plan are flat, and a camera looking at a
flat surface produces a projective transform of it. Nothing else is assumed
and nothing has to be measured.

What it buys, in order of how much it matters:

* **Real distances.** With the floor's scale recorded (how wide the plan is in
  metres), a corridor obstruction stops being "41% of the pixels" and becomes
  a clear width in metres, which is the number a fire code is written in.
* **Position.** A detection's foot point projects onto the plan, so people seen
  by CCTV appear where they actually are in the building.
* **Automatic regions of interest.** A door or an extinguisher drawn on the plan
  projects back into the camera image, so the region to watch is derived rather
  than drawn twice.

Quality is reported honestly: four points fit a homography exactly, so a
perfect residual means nothing on its own. The fit is scored by how sanely it
behaves — whether it stays invertible, keeps the image corners in front of the
camera, and reprojects the given points consistently — and anything doubtful is
flagged rather than silently used.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

MIN_POINTS = 4


class Homography:
    """A fitted image ↔ plan mapping for one camera."""

    def __init__(self, matrix: Sequence[Sequence[float]],
                 points: Optional[List[dict]] = None,
                 quality: float = 0.0, note: str = ""):
        self.H = np.array(matrix, dtype=np.float64).reshape(3, 3)
        self.points = points or []
        self.quality = quality
        self.note = note
        try:
            self.H_inv = np.linalg.inv(self.H)
        except np.linalg.LinAlgError:
            self.H_inv = None

    # ------------------------------------------------------------ projection
    def image_to_plan(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        """A point in normalised image coordinates → normalised plan coordinates."""
        return _apply(self.H, x, y)

    def plan_to_image(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        if self.H_inv is None:
            return None
        return _apply(self.H_inv, x, y)

    def foot_point(self, box: Dict, w: int, h: int) -> Optional[Tuple[float, float]]:
        """Where a detection touches the floor.

        The bottom-centre of the box, because that is where a person's feet or
        an object's base meet the ground — the only part of a bounding box that
        is actually on the plane the homography describes. Using the centre of
        the box would place a standing person somewhere inside the far wall.
        """
        fx = (box["x"] + box["w"] / 2) / float(max(1, w))
        fy = (box["y"] + box["h"]) / float(max(1, h))
        return self.image_to_plan(fx, fy)

    def plan_rect_to_image(self, rect: Sequence[float]) -> Optional[List[List[float]]]:
        """A plan rectangle → the quadrilateral it occupies in the image."""
        x, y, w, h = rect
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        out = []
        for cx, cy in corners:
            p = self.plan_to_image(cx, cy)
            if p is None:
                return None
            out.append([round(p[0], 4), round(p[1], 4)])
        return out

    def plan_bbox_in_image(self, rect: Sequence[float],
                           min_size: float = 0.08) -> Optional[List[float]]:
        """The axis-aligned image ROI covering a plan rectangle — this is what
        replaces an ROI the operator would otherwise have to draw by hand.

        Something far down a corridor projects to a very thin band, because
        that is genuinely how it appears. Refusing to return a region for it
        would be unhelpful — a door at the far end is still worth watching — so
        a too-small box is padded around its centre to something usable and the
        caller gets a region either way.
        """
        quad = self.plan_rect_to_image(rect)
        if not quad:
            return None
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if not (-0.25 <= cx <= 1.25 and -0.25 <= cy <= 1.25):
            return None                     # it is simply not in this view
        half_w = max((x1 - x0) / 2, min_size / 2)
        half_h = max((y1 - y0) / 2, min_size / 2)
        x0, x1 = max(0.0, cx - half_w), min(1.0, cx + half_w)
        y0, y1 = max(0.0, cy - half_h), min(1.0, cy + half_h)
        if x1 - x0 < 0.02 or y1 - y0 < 0.02:
            return None
        return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]

    def as_dict(self) -> dict:
        return {"matrix": [list(map(float, row)) for row in self.H],
                "points": self.points, "quality": round(self.quality, 3),
                "note": self.note}


def _apply(M: np.ndarray, x: float, y: float) -> Optional[Tuple[float, float]]:
    v = M @ np.array([x, y, 1.0])
    if abs(v[2]) < 1e-9:                 # the point maps to infinity
        return None
    return float(v[0] / v[2]), float(v[1] / v[2])


def _quad_is_sane(pts: List[Tuple[float, float]]) -> bool:
    """A convex, non-degenerate quadrilateral in a consistent winding order."""
    if len(pts) < 4:
        return False
    area = 0.0
    signs = []
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        x3, y3 = pts[(i + 2) % 4]
        area += x1 * y2 - x2 * y1
        cross = (x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2)
        signs.append(cross > 0)
    if abs(area / 2.0) < 1e-4:           # collinear or near-zero area
        return False
    return all(signs) or not any(signs)   # convex: every turn the same way


def fit(points: List[dict]) -> Dict:
    """Fit a homography from point pairs.

    `points`: [{"image": [x, y], "plan": [x, y]}, ...] in normalised (0-1)
    coordinates, four or more.
    """
    if len(points) < MIN_POINTS:
        return {"ok": False, "error": f"need at least {MIN_POINTS} point pairs"}
    try:
        src = np.array([[float(p["image"][0]), float(p["image"][1])]
                        for p in points], dtype=np.float32)
        dst = np.array([[float(p["plan"][0]), float(p["plan"][1])]
                        for p in points], dtype=np.float32)
    except (KeyError, TypeError, ValueError, IndexError):
        return {"ok": False, "error": "each point needs image [x,y] and plan [x,y]"}

    if not _quad_is_sane([tuple(p) for p in src[:4]]):
        return {"ok": False, "error": "the four image points must form a convex "
                                      "shape — click them around the area, not "
                                      "in a zigzag"}
    if not _quad_is_sane([tuple(p) for p in dst[:4]]):
        return {"ok": False, "error": "the four plan points must form a convex "
                                      "shape, in the same order as the image ones"}

    if len(points) == 4:
        H = cv2.getPerspectiveTransform(src, dst)
    else:
        H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 0.01)
    if H is None:
        return {"ok": False, "error": "could not fit a mapping to those points"}

    try:
        np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return {"ok": False, "error": "those points are degenerate (three of "
                                      "them are probably in a line)"}

    homo = Homography(H, points=points)

    # Reprojection error is a formality with exactly four points — it is zero by
    # construction — so the fit is judged on whether the *rest* of the image
    # behaves: the frame corners must land somewhere sane on the plan.
    err = _reprojection_error(homo, points)
    corners = [homo.image_to_plan(x, y) for x, y in
               ((0.02, 0.98), (0.98, 0.98), (0.98, 0.02), (0.02, 0.02))]
    if any(c is None for c in corners):
        return {"ok": False, "error": "that mapping sends part of the view to "
                                      "infinity — re-pick the points further apart"}
    spread = max(max(abs(c[0]), abs(c[1])) for c in corners)

    quality = 1.0
    note = ""
    if err > 0.02:
        quality -= min(0.5, err * 10)
        note = "point pairs disagree slightly"
    if spread > 6:
        quality -= 0.35
        note = ("the view stretches far beyond the plan — this is normal for a "
                "camera looking down a long corridor, but check the mapping")
    quality = max(0.0, round(quality, 3))
    homo.quality, homo.note = quality, note
    return {"ok": True, "homography": homo.as_dict(), "quality": quality,
            "reprojection_error": round(err, 4), "note": note}


def _reprojection_error(homo: Homography, points: List[dict]) -> float:
    errs = []
    for p in points:
        got = homo.image_to_plan(float(p["image"][0]), float(p["image"][1]))
        if got is None:
            return 1.0
        want = (float(p["plan"][0]), float(p["plan"][1]))
        errs.append(((got[0] - want[0]) ** 2 + (got[1] - want[1]) ** 2) ** 0.5)
    return float(sum(errs) / len(errs)) if errs else 1.0


def load(data: Optional[dict]) -> Optional[Homography]:
    if not data or not data.get("matrix"):
        return None
    try:
        return Homography(data["matrix"], data.get("points"),
                          data.get("quality", 0.0), data.get("note", ""))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Plan units → metres
# ---------------------------------------------------------------------------
def plan_metres(floor_width_m: float, plan_distance: float) -> Optional[float]:
    """Convert a distance in normalised plan units to metres.

    Only possible once somebody records how wide the floor plan actually is —
    which is a fact about the building, not something a camera can infer, so it
    is asked for rather than guessed.
    """
    if not floor_width_m or floor_width_m <= 0:
        return None
    return float(plan_distance) * float(floor_width_m)


def clear_width_m(homo: Homography, mask_free_fraction: float,
                  corridor_rect: Sequence[float], floor_width_m: float) -> Optional[float]:
    """Approximate remaining clear width of a corridor, in metres.

    The corridor's drawn width on the plan is a real dimension once the floor
    scale is known; the fraction of it still free comes from the obstruction
    analysis. Both parts are honest, and the result is explicitly approximate —
    it assumes the obstruction spans the corridor evenly, which is the
    conservative reading.
    """
    if not floor_width_m or floor_width_m <= 0:
        return None
    width_units = min(corridor_rect[2], corridor_rect[3])   # the short side
    return round(width_units * floor_width_m * max(0.0, mask_free_fraction), 2)
