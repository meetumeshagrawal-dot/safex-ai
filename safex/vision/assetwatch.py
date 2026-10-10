"""Fire-safety asset monitoring — extinguishers, exit signs, hydrants, call points.

The brief asks for the *presence and accessibility* of these, and those are two
genuinely different questions:

* **Presence** — is it still there? Extinguishers get borrowed for a job and
  never come back, get taken for servicing and not returned, or get moved when
  a ward is repainted. An empty bracket is invisible until the day it matters.
* **Accessibility** — can somebody reach it in three seconds while a bin is on
  fire? An extinguisher behind a stack of boxes is as good as absent, and this
  is by far the more common finding in a real inspection.

There is no COCO class for a fire extinguisher and no dataset of this hospital's
signage, so presence is read the same way the fire doors are: the operator
captures the asset once, in place, and every check compares against that
reference. It is unglamorous and it works on any building without training
anything — and unlike a detector, it cannot hallucinate an extinguisher that
was never there.

Exit signs get one extra check. A sign that is present, unobstructed and **dark**
is a failed sign, and an unlit exit sign is a classic audit finding — so their
brightness is compared with the reference too.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import cv2
import numpy as np

from .doorwatch import decode_reference, encode_reference, _prep, similarity

# How much the view of the asset may change before it is called missing, and how
# much of its approach may be covered before it is called blocked.
PRESENT_AT = 0.70          # similarity at or above this: still there
MISSING_BELOW = 0.50       # ...at or below this, something has changed
BLOCKED_COVER = 0.35       # fraction of the approach zone obstructed
UNLIT_RATIO = 0.55         # brightness relative to the reference
CONFIRM_SECONDS = 20.0     # a trolley wheeled past is not a finding

ASSET_LABELS = {"extinguisher": "Fire extinguisher", "exit_sign": "Exit sign",
                "hydrant": "Hydrant / hose reel", "alarm_point": "Manual call point"}

ASSET_ACTIONS = {
    "BLOCKED": "Clear the access to it — an asset that cannot be reached in "
               "seconds is not available.",
    "MISSING": "Check whether it has been removed, borrowed or relocated, and "
               "restore it to its bracket.",
    "UNLIT": "The sign is not illuminated. Check its lamp and its supply.",
}


@dataclass
class AssetState:
    state: str = "UNCALIBRATED"     # OK | BLOCKED | MISSING | UNLIT | UNCALIBRATED
    similarity: float = 0.0
    cover: float = 0.0              # fraction of the approach that is obstructed
    brightness_ratio: float = 1.0
    since: float = 0.0
    duration_s: float = 0.0
    violation: bool = False
    note: str = ""

    def as_dict(self) -> Dict:
        return {"state": self.state, "similarity": round(self.similarity, 2),
                "cover": round(self.cover, 3),
                "brightness_ratio": round(self.brightness_ratio, 2),
                "duration_s": round(self.duration_s, 1),
                "violation": self.violation, "note": self.note}


class AssetMonitor:
    """One fire-safety asset, watched by one camera."""

    def __init__(self, asset_id: str, kind: str, roi: List[float],
                 reference: Optional[str] = None, label: str = "",
                 confirm_seconds: float = CONFIRM_SECONDS):
        self.asset_id = asset_id
        self.kind = kind
        self.label = label or ASSET_LABELS.get(kind, kind)
        self.roi = list(roi or [0.45, 0.4, 0.1, 0.2])
        self.confirm_seconds = float(confirm_seconds or CONFIRM_SECONDS)
        self._ref = decode_reference(reference) if reference else None
        self._ref_brightness = float(self._ref.mean()) if self._ref is not None else 0.0
        self._state = AssetState(state="UNCALIBRATED" if self._ref is None else "OK")
        self._bad_since: Optional[float] = None
        self._last_patch = None

    # ------------------------------------------------------------------ setup
    def _crop(self, frame, pad: float = 0.0):
        """The asset itself, or — with padding — the space in front of it that
        somebody has to walk into to reach it."""
        h, w = frame.shape[:2]
        x, y, rw, rh = self.roi
        if pad:
            x, y = x - rw * pad, y - rh * pad
            rw, rh = rw * (1 + 2 * pad), rh * (1 + 2 * pad)
        x0, y0 = max(0, int(x * w)), max(0, int(y * h))
        x1, y1 = min(w, int((x + rw) * w)), min(h, int((y + rh) * h))
        if x1 - x0 < 6 or y1 - y0 < 6:
            return frame
        return frame[y0:y1, x0:x1]

    def capture_reference(self, frame) -> Optional[str]:
        patch = self._crop(frame)
        if patch is None:
            return None
        self._ref = _prep(patch)
        self._ref_brightness = float(self._ref.mean())
        self._state.state, self._state.violation = "OK", False
        self._state.note = "Reference captured"
        self._bad_since = None
        return encode_reference(self._ref)

    def set_roi(self, roi: List[float]):
        self.roi = list(roi)

    # ------------------------------------------------------------------- main
    def check(self, frame, boxes: List[Dict] = None) -> AssetState:
        st = self._state
        if self._ref is None:
            st.state, st.violation = "UNCALIBRATED", False
            st.note = "Capture this asset once, in place, to start monitoring it"
            return st

        patch = self._crop(frame)
        self._last_patch = patch
        sim = similarity(patch, self._ref)
        st.similarity = sim

        # brightness, for a sign that should be lit
        cur_b = float(_prep(patch).mean())
        st.brightness_ratio = (cur_b / self._ref_brightness
                               if self._ref_brightness > 1 else 1.0)

        # is anything parked in front of it? A detection box overlapping the
        # approach zone is the clearest evidence, and it names the culprit.
        cover, culprit = self._approach_cover(frame, boxes or [])
        st.cover = cover

        state, note = self._grade(sim, cover, st.brightness_ratio, culprit)
        now = time.time()
        if state == "OK":
            self._bad_since = None
        elif self._bad_since is None:
            self._bad_since = now
        duration = (now - self._bad_since) if self._bad_since else 0.0
        st.duration_s = duration
        st.since = self._bad_since or 0.0

        confirmed = state != "OK" and duration >= self.confirm_seconds
        st.state = state if confirmed else ("OK" if state == "OK" else "PENDING")
        st.violation = confirmed
        st.note = note if state != "OK" else ""
        return st

    def _approach_cover(self, frame, boxes: List[Dict]):
        """How much of the space in front of the asset is taken up."""
        h, w = frame.shape[:2]
        x, y, rw, rh = self.roi
        pad = 0.6
        ax0, ay0 = (x - rw * pad) * w, (y - rh * pad) * h
        ax1, ay1 = (x + rw * (1 + pad)) * w, (y + rh * (1 + pad)) * h
        area = max(1.0, (ax1 - ax0) * (ay1 - ay0))
        cover, culprit = 0.0, ""
        for b in boxes:
            bx0, by0 = b["x"], b["y"]
            bx1, by1 = bx0 + b["w"], by0 + b["h"]
            iw = max(0, min(ax1, bx1) - max(ax0, bx0))
            ih = max(0, min(ay1, by1) - max(ay0, by0))
            frac = (iw * ih) / area
            if frac > 0.02 and b.get("label") != "person":
                cover += frac
                if not culprit:
                    culprit = b.get("label", "an object")
        return min(1.0, cover), culprit

    def _grade(self, sim: float, cover: float, brightness: float, culprit: str):
        if cover >= BLOCKED_COVER:
            what = f" by {culprit}" if culprit else ""
            return "BLOCKED", f"{self.label} access is obstructed{what}"
        if sim <= MISSING_BELOW:
            return "MISSING", f"{self.label} is not where it should be"
        if self.kind == "exit_sign" and brightness < UNLIT_RATIO:
            return "UNLIT", f"{self.label} is not illuminated"
        if sim < PRESENT_AT:
            # between the two lines: something changed but not enough to call it
            return "OK", ""
        return "OK", ""

    def state(self) -> AssetState:
        return self._state

    def calibrated(self) -> bool:
        return self._ref is not None
