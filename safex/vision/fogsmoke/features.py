"""
What separates smoke from fog, steam and plain haze.

The fire model (best.pt) answers "does this look like smoke". It was trained on
outdoor wildfire and building-fire photographs, so anything grey, soft-edged and
translucent scores well — fog on a car-park camera, steam off a ward kitchen
range, condensation on the lens. Those are the false alarms that get a fire
system switched off, so the question this module answers is the next one down:
*given that something hazy is here, is it a fire product or a water product?*

None of these features are learned image statistics. Each one is a physical
difference between the three, so the classifier trained on top of them stays
interpretable and does not depend on having photographed every hospital:

  fog / haze   scatters over the whole depth of the scene. It has no source and
               no boundary, it dims distant things more than near ones, and it
               is the same from second to second.
  steam        is a local buoyant plume like smoke, but it is condensed water:
               bright, close to white, weakly absorbing, and it dissipates as
               soon as it mixes with cooler air.
  smoke        is a local buoyant plume of soot and unburnt fuel: it *absorbs*
               rather than scatters, so it is darker than its surroundings, it
               is optically thicker in the middle than at the edge, and its
               boundary is turbulent rather than smoothly fading.

The single most useful measurement is contrast inside the region against
contrast outside it. A veil that covers the whole frame is weather. A veil with
somewhere to stand outside it is an event.
"""
from __future__ import annotations
import numpy as np
import cv2

# The order here IS the model's input order — appending is safe, reordering is
# not. `train.py` writes it into the model file so a mismatch is caught loudly
# instead of silently scoring nonsense.
FEATURE_NAMES = [
    "area_frac", "sat_mean", "val_mean", "val_std", "val_skew",
    "lap_var", "edge_density", "contrast_in", "contrast_out", "contrast_ratio",
    "dark_in", "dark_out", "dark_ratio", "trans_mean", "trans_std",
    "boundary_sharp", "depth_grad", "warm_ratio",
]
N_FEATURES = len(FEATURE_NAMES)


def _clip_box(box, w, h):
    """Box as pixel ints, clamped inside the frame, never empty."""
    x1 = max(0, min(int(box[0]), w - 2))
    y1 = max(0, min(int(box[1]), h - 2))
    x2 = max(x1 + 1, min(int(box[2]), w - 1))
    y2 = max(y1 + 1, min(int(box[3]), h - 1))
    return x1, y1, x2, y2


def _dark_channel(bgr, patch=9):
    """Min over colour channels, then a local min filter.

    On a clear image something in nearly every patch is dark (a shadow, a dark
    surface, a saturated colour), so the dark channel sits near zero. Airlight
    lifts it — which is what fog does everywhere and a plume does only where it
    actually is.
    """
    mn = np.min(bgr, axis=2)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (patch, patch))
    return cv2.erode(mn, k)


def _rms_contrast(gray_f):
    """RMS contrast, normalised by mean level — blind to overall brightness,
    which matters, because a dim room is not a foggy one."""
    if gray_f.size == 0:
        return 0.0
    m = float(gray_f.mean())
    if m < 1e-6:
        return 0.0
    return float(gray_f.std() / max(m, 1e-6))


def _local_contrast_map(gray, k=15):
    """Per-pixel local contrast: std inside a k-box, via E[x^2] - E[x]^2."""
    g = gray.astype(np.float32)
    mu = cv2.blur(g, (k, k))
    mu2 = cv2.blur(g * g, (k, k))
    return np.sqrt(np.maximum(mu2 - mu * mu, 0.0))


# Everything is measured at this working size, whatever came in. Two reasons,
# both of them bugs otherwise: the training images are 640x640 while the cameras
# run 480x360, and texture measures like Laplacian variance and edge density
# move with resolution — so a model trained at one scale would read the other
# scale wrong. Fixing the scale also keeps the global passes cheap enough to sit
# inside a detection budget.
WORK_LONG_SIDE = 320


def _to_work_scale(frame_bgr, box):
    """Resize the frame to a fixed working size and carry the box with it."""
    h, w = frame_bgr.shape[:2]
    long_side = max(h, w)
    if long_side == WORK_LONG_SIDE:
        return frame_bgr, box
    s = WORK_LONG_SIDE / float(long_side)
    nw, nh = max(2, int(round(w * s))), max(2, int(round(h * s)))
    small = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    return small, (box[0] * s, box[1] * s, box[2] * s, box[3] * s)


def extract(frame_bgr, box) -> np.ndarray:
    """Feature vector for one candidate region.

    `box` is (x1, y1, x2, y2) in pixels of the frame as passed in. Returns
    exactly N_FEATURES floats, in FEATURE_NAMES order.
    """
    frame_bgr, box = _to_work_scale(frame_bgr, box)
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = _clip_box(box, w, h)

    roi = frame_bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray_roi = gray[y1:y2, x1:x2]
    hsv_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

    area_frac = ((x2 - x1) * (y2 - y1)) / float(w * h)

    # --- colour: steam is bright and near-white, smoke is darker and tinted --
    sat_mean = float(hsv_roi[..., 1].mean()) / 255.0
    v = hsv_roi[..., 2].astype(np.float32)
    val_mean = float(v.mean()) / 255.0
    val_std = float(v.std()) / 255.0
    vm, vs = v.mean(), v.std() + 1e-6
    val_skew = float(np.clip((((v - vm) / vs) ** 3).mean(), -6, 6))

    # --- texture: any veil destroys detail, turbulent smoke keeps some -------
    lap = cv2.Laplacian(gray_roi, cv2.CV_32F)
    lap_var = float(np.log1p(lap.var()))
    edges = cv2.Canny(gray_roi, 40, 120)
    edge_density = float((edges > 0).mean())

    # --- the decisive one: is the veil local, or is it the whole scene? ------
    contrast_in = _rms_contrast(gray_roi.astype(np.float32))
    outside = np.ones((h, w), bool)
    outside[y1:y2, x1:x2] = False
    contrast_out = (_rms_contrast(gray[outside].astype(np.float32))
                    if outside.sum() > 64 else contrast_in)
    contrast_ratio = float(contrast_in / max(contrast_out, 1e-4))

    # --- airlight: lifted inside only (plume) vs lifted everywhere (fog) -----
    dc = _dark_channel(frame_bgr).astype(np.float32) / 255.0
    dark_in = float(dc[y1:y2, x1:x2].mean())
    dark_out = float(dc[outside].mean()) if outside.sum() > 64 else dark_in
    dark_ratio = float(dark_in / max(dark_out, 1e-4))

    # transmission from the dark-channel prior: t = 1 - w * dark / A
    A = float(np.percentile(dc, 99.5)) or 1.0
    trans = 1.0 - 0.95 * (dc / max(A, 1e-4))
    trans_roi = np.clip(trans[y1:y2, x1:x2], 0.0, 1.0)
    trans_mean = float(trans_roi.mean())
    trans_std = float(trans_roi.std())      # fog is uniform, a plume is not

    # --- boundary: a plume has an edge, fog fades into nothing ---------------
    pad = max(4, int(0.08 * max(x2 - x1, y2 - y1)))
    bx1, by1 = max(0, x1 - pad), max(0, y1 - pad)
    bx2, by2 = min(w, x2 + pad), min(h, y2 + pad)
    ring = gray[by1:by2, bx1:bx2].astype(np.float32).copy()
    iy1, ix1 = y1 - by1, x1 - bx1
    ring[iy1:iy1 + (y2 - y1), ix1:ix1 + (x2 - x1)] = np.nan
    ring_vals = ring[~np.isnan(ring)]
    boundary_sharp = float(abs(float(gray_roi.astype(np.float32).mean()) -
                               (float(ring_vals.mean()) if ring_vals.size else 0.0)) / 255.0)

    # --- depth: fog dims the far field more, so contrast rises down the frame
    lc = _local_contrast_map(gray)
    rows = lc.mean(axis=1)
    yy = np.arange(h, dtype=np.float32)
    depth_grad = float(np.corrcoef(yy, rows)[0, 1]) if rows.std() > 1e-6 else 0.0
    if not np.isfinite(depth_grad):
        depth_grad = 0.0

    # --- a hint of the fire itself: smoke often carries warm light -----------
    b = roi[..., 0].astype(np.float32)
    r = roi[..., 2].astype(np.float32)
    warm_ratio = float(np.clip((r.mean() + 1.0) / (b.mean() + 1.0), 0.0, 4.0))

    out = np.array([
        area_frac, sat_mean, val_mean, val_std, val_skew,
        lap_var, edge_density, contrast_in, contrast_out, contrast_ratio,
        dark_in, dark_out, dark_ratio, trans_mean, trans_std,
        boundary_sharp, depth_grad, warm_ratio,
    ], dtype=np.float32)
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
