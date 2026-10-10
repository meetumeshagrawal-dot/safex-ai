"""
Making the negatives, because nobody ships a hospital fog dataset.

The fire dataset has fire and smoke and nothing else, so the thing we actually
need to teach — "this haze is water, not combustion" — has no examples in it.
Rather than pretend otherwise, the negatives are rendered from the dataset's own
clear images using the same optical model that describes real fog:

    I(x) = J(x) * t(x) + A * (1 - t(x)),      t(x) = exp(-beta * d(x))

J is the clear image, A is the airlight (the colour the scene tends towards as
it disappears), and t is transmission, falling off with depth d. This is the
standard atmospheric-scattering model, the same one Foggy Cityscapes is built
with, and it is *why* fog looks like fog: everything drifts towards one colour
at a rate set by how far away it is.

Three renderers, one per confuser:

  fog     depth-dependent and global. Indoors we have no depth map, so depth is
          approximated by image row — the far field of a corridor camera is up
          the frame. Crude, but it reproduces the property that matters: the
          veil is everywhere and it is stronger further away.
  steam   a local plume with no depth term. Bright, near-white, high
          transmission at the edges, mixing out quickly.
  lens    condensation or a dirty dome: a soft global blur plus a white lift,
          with no depth term at all.

What this is and is not: these are physically-motivated renderings, not
photographs of real hospital steam. They teach the classifier the geometry and
the optics — global versus local, absorbing versus scattering — which is the
part that transfers. They cannot teach it what a specific autoclave looks like.
The evaluation in train.py is reported on held-out renders, so read those
numbers as "has it learned the physics", not "will it be right in your kitchen".
"""
from __future__ import annotations
import numpy as np
import cv2


def _airlight(rng):
    """Fog is not pure white — it takes the colour of the light in the room."""
    base = rng.uniform(0.72, 0.97)
    tint = rng.uniform(-0.05, 0.05, size=3)
    return np.clip(base + tint, 0.4, 1.0).astype(np.float32)


def fog(img_bgr, rng, beta=None):
    """Global, depth-dependent veil. Returns (image, box) — the box is the whole
    frame, because that is precisely what makes it fog."""
    h, w = img_bgr.shape[:2]
    J = img_bgr.astype(np.float32) / 255.0
    beta = float(rng.uniform(0.9, 2.6)) if beta is None else beta

    # Depth proxy: up the frame is further away. Softened with a low-frequency
    # random field so the veil is not a clean gradient — real fog is patchy.
    yy = np.linspace(1.0, 0.0, h, dtype=np.float32)[:, None]
    depth = np.repeat(yy, w, axis=1)
    blob = rng.random((max(2, h // 64), max(2, w // 64))).astype(np.float32)
    blob = cv2.resize(blob, (w, h), interpolation=cv2.INTER_CUBIC)
    depth = np.clip(depth * 0.75 + blob * 0.45, 0.0, 1.6)

    t = np.exp(-beta * depth)[..., None]
    A = _airlight(rng).reshape(1, 1, 3)
    out = J * t + A * (1.0 - t)
    return (np.clip(out, 0, 1) * 255).astype(np.uint8), (0, 0, w, h)


def steam(img_bgr, rng, box=None):
    """A local, bright, weakly-absorbing plume — a kettle, an autoclave, a
    shower door opening. Same geometry as smoke, opposite optics.

    `box` lets the caller dictate where and how big the plume is, so training
    can draw steam at exactly the sizes real smoke boxes come in and the
    classifier gets no free geometric hint.
    """
    h, w = img_bgr.shape[:2]
    J = img_bgr.astype(np.float32) / 255.0

    if box is not None:
        bx1, by1, bx2, by2 = box
        pw, ph = max(8, bx2 - bx1), max(8, by2 - by1)
        cx, cy = (bx1 + bx2) // 2, (by1 + by2) // 2
    else:
        pw = int(rng.uniform(0.12, 0.38) * w)
        ph = int(rng.uniform(0.18, 0.5) * h)
        cx = int(rng.uniform(pw / 2, w - pw / 2))
        cy = int(rng.uniform(ph / 2, h - ph / 2))

    # soft-edged blob: a Gaussian footprint roughened by low-frequency noise,
    # so the plume has structure without a hard edge
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    g = np.exp(-(((xx - cx) / (pw * 0.55)) ** 2 + ((yy - cy) / (ph * 0.55)) ** 2))
    noise = rng.random((max(2, h // 32), max(2, w // 32))).astype(np.float32)
    noise = cv2.resize(noise, (w, h), interpolation=cv2.INTER_CUBIC)
    alpha = np.clip(g * (0.55 + 0.45 * noise), 0, 1) * float(rng.uniform(0.55, 0.92))

    # steam is bright and desaturated: it scatters, it barely absorbs
    A = np.clip(_airlight(rng) + 0.06, 0, 1).reshape(1, 1, 3)
    out = J * (1 - alpha[..., None]) + A * alpha[..., None]

    x1, y1 = max(0, cx - pw // 2), max(0, cy - ph // 2)
    x2, y2 = min(w, cx + pw // 2), min(h, cy + ph // 2)
    return (np.clip(out, 0, 1) * 255).astype(np.uint8), (x1, y1, x2, y2)


def lens_haze(img_bgr, rng):
    """Condensation or grime on the dome: a global blur with a white lift and
    no depth term at all. Reads as smoke to a detector; is a cleaning job."""
    h, w = img_bgr.shape[:2]
    k = int(rng.uniform(9, 27)) | 1
    blurred = cv2.GaussianBlur(img_bgr, (k, k), 0).astype(np.float32) / 255.0
    a = float(rng.uniform(0.25, 0.6))
    A = _airlight(rng).reshape(1, 1, 3)
    out = blurred * (1 - a) + A * a
    return (np.clip(out, 0, 1) * 255).astype(np.uint8), (0, 0, w, h)


RENDERERS = {"fog": fog, "steam": steam, "lens": lens_haze}
