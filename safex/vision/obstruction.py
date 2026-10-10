"""Egress obstruction analysis — is this corridor actually usable?

A corridor is blocked in more ways than a detector has classes for, so this
uses two independent readings and takes the worse of them:

1. **Known objects.** The person model already runs on every camera, and most
   real hospital violations are things it can name: a bed or stretcher parked
   in the corridor, chairs, visitors' luggage, a potted plant, equipment left
   out. Those boxes are measured directly.

2. **Anything else that shouldn't be there.** Cardboard cartons, linen
   trolleys, an oxygen cylinder, a ceiling panel brought down by a fire —
   none of these are COCO classes, and a system that only trusted the
   classifier would call that corridor clear. So each camera also learns what
   its corridor looks like empty, and anything large that appears and *stays*
   counts as an obstruction even when nothing can name it.

Both readings are gated on **persistence**: a nurse walking through is not an
obstruction, a trolley left for two minutes is. That gate is what makes this a
compliance signal instead of a stream of noise.

What this deliberately does not claim: it cannot measure clear width in metres
from an uncalibrated camera. It reports the fraction of the monitored floor
area that is lost, and converts that to an approximate width only when the
operator has told us how wide the corridor actually is.
"""
from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# COCO classes that constitute an egress obstruction when found in a corridor.
# `person` is handled separately — people are traffic until they stop moving.
OBSTRUCTION_CLASSES = {
    "bed": "bed / stretcher", "chair": "chair", "bench": "bench",
    "couch": "seating", "suitcase": "luggage", "backpack": "bag",
    "handbag": "bag", "umbrella": "umbrella", "potted plant": "planter",
    "dining table": "table", "tv": "equipment", "laptop": "equipment",
    "refrigerator": "appliance", "sink": "fixture", "toilet": "fixture",
    "bottle": "cylinder / bottle", "book": "stores",
}

# Severity thresholds on the fraction of the monitored floor area that is lost.
NARROWED_AT = 0.18
OBSTRUCTED_AT = 0.35
BLOCKED_AT = 0.60

# How long something must stay put before it is called a violation, and how
# long a cleared corridor takes to be believed again.
PERSIST_SECONDS = 8.0
CLEAR_SECONDS = 3.0

# Background learning: how quickly the "empty corridor" reference adapts. Slow
# enough that a parked trolley never becomes part of the background within a
# shift, fast enough to absorb lighting changes.
BG_ALPHA = 0.002
BG_WARMUP_FRAMES = 40
MIN_BLOB_FRAC = 0.012          # ignore specks; a real obstruction is sizeable
SCENE_CHANGE_AT = 0.85         # this much of the view differing is not an object
CHANGE_THRESHOLD = 22          # grey levels a pixel must shift to count
CHANGE_THRESHOLD_HELD = 14     # ...once something IS there, to stop flapping


@dataclass
class ObstructionState:
    severity: str = "CLEAR"           # CLEAR | NARROWED | OBSTRUCTED | BLOCKED
    fraction: float = 0.0             # 0-1 of monitored floor area lost
    objects: List[Dict] = field(default_factory=list)
    people_standing: int = 0
    unknown_fraction: float = 0.0     # part not explained by a named object
    since: float = 0.0                # when the current violation started
    duration_s: float = 0.0
    confirmed: bool = False           # survived the persistence gate
    reference_ready: bool = False
    note: str = ""

    def as_dict(self) -> Dict:
        return {"severity": self.severity, "fraction": round(self.fraction, 3),
                "objects": self.objects[:6], "people_standing": self.people_standing,
                "unknown_fraction": round(self.unknown_fraction, 3),
                "duration_s": round(self.duration_s, 1),
                "confirmed": self.confirmed,
                "reference_ready": self.reference_ready, "note": self.note}


class ObstructionAnalyser:
    """One per camera. Owns that camera's view of its corridor."""

    def __init__(self, roi: Optional[List[float]] = None):
        # Region of interest in image coordinates. The default watches the
        # lower part of the frame, which is where the floor is in a corridor
        # camera; the operator can tighten it per camera.
        self.roi = list(roi or [0.0, 0.35, 1.0, 0.65])
        # A per-pixel background model rather than a fixed grey threshold: a
        # dim ward at night and a bright day-lit corridor have wildly different
        # noise, and a cardboard carton can sit only twenty grey levels off the
        # floor. MOG2 learns how much each pixel normally varies, so "different
        # from normal" means the same thing in both rooms.
        self._mog = cv2.createBackgroundSubtractorMOG2(
            history=400, varThreshold=14, detectShadows=True)
        self._bg_frames = 0
        self._state = ObstructionState()
        self._violating_since: Optional[float] = None
        self._clear_since: Optional[float] = None
        self._track = deque(maxlen=30)      # recent person centroids for dwell

    # ------------------------------------------------------------------ utils
    def _roi_slice(self, frame) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
        h, w = frame.shape[:2]
        x0 = int(self.roi[0] * w); y0 = int(self.roi[1] * h)
        x1 = min(w, x0 + int(self.roi[2] * w)); y1 = min(h, y0 + int(self.roi[3] * h))
        x0, y0 = max(0, x0), max(0, y0)
        if x1 - x0 < 8 or y1 - y0 < 8:      # degenerate ROI — use the frame
            return frame, (0, 0, w, h)
        return frame[y0:y1, x0:x1], (x0, y0, x1 - x0, y1 - y0)

    def _box_overlap_frac(self, box: Dict, roi_px) -> float:
        """How much of the monitored area a detection box covers."""
        rx, ry, rw, rh = roi_px
        bx, by, bw, bh = box["x"], box["y"], box["w"], box["h"]
        ix = max(0, min(bx + bw, rx + rw) - max(bx, rx))
        iy = max(0, min(by + bh, ry + rh) - max(by, ry))
        return (ix * iy) / float(max(1, rw * rh))

    # -------------------------------------------------------------- reference
    def _foreground(self, roi_gray: np.ndarray) -> np.ndarray:
        """Everything that is not part of this corridor's normal appearance.

        The learning rate is the important part. While the corridor reads
        clear the model keeps adapting, so lighting changes and a slowly
        moving sun are absorbed. The moment something IS there the model is
        **frozen** — otherwise a trolley left in a fire exit would quietly
        become "normal" within minutes and the violation would disappear,
        which is the exact failure this feature exists to prevent.
        """
        if self._bg_frames < BG_WARMUP_FRAMES:
            rate = -1                       # let it settle quickly at first
        elif self._state.confirmed:
            rate = 0.0                      # a real violation: stop learning
        else:
            # Still learning while a change is merely *pending*. Freezing the
            # moment anything differs would lock in a lighting change and turn
            # it into a permanent false violation; at this rate a genuine
            # object cannot be absorbed inside the persistence window, but the
            # ward lights coming on can.
            rate = 0.0008
        mask = self._mog.apply(roi_gray, learningRate=rate)
        self._bg_frames += 1
        # 127 is MOG2's shadow label — a shadow on the floor is not an object
        return cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)[1]

    # ------------------------------------------------------------------ main
    def analyse(self, frame, boxes: List[Dict], smoke: bool = False,
                fire: bool = False) -> ObstructionState:
        st = self._state
        roi, roi_px = self._roi_slice(frame)
        gray = cv2.GaussianBlur(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), (5, 5), 0)

        # ---- 1. named objects sitting in the corridor ----
        named: List[Dict] = []
        named_frac = 0.0
        for b in boxes:
            label = b.get("label", "")
            if label in OBSTRUCTION_CLASSES:
                frac = self._box_overlap_frac(b, roi_px)
                if frac > 0.005:
                    named_frac += frac
                    named.append({"label": OBSTRUCTION_CLASSES[label],
                                  "raw": label, "conf": round(b.get("conf", 0), 2),
                                  "area": round(frac, 3)})
        named_frac = min(1.0, named_frac)

        # ---- 2. people who have stopped moving ----
        people = [b for b in boxes if b.get("label") == "person"]
        centroids = [(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2) for b in people]
        self._track.append(centroids)
        standing = self._count_standing(people, roi_px)

        # ---- 3. anything else that appeared and stayed ----
        mask = self._foreground(gray)
        unknown_frac = 0.0
        if self._bg_frames >= BG_WARMUP_FRAMES:
            # CLOSE before OPEN: a real object near the limit comes through
            # speckled, so consolidate it first and only then remove specks.
            # Doing it the other way round erases the object itself.
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                    cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                    cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
            # discount the area already explained by people and named objects,
            # so the same trolley isn't counted twice
            explained = self._explained_mask(mask.shape, boxes, roi_px)
            mask = cv2.bitwise_and(mask, cv2.bitwise_not(explained))
            total = float(mask.shape[0] * mask.shape[1])

            # An obstruction is *localised*. When almost the entire view differs
            # from normal, the scene changed — the ward lights came on, the
            # camera was nudged, the exposure jumped — and calling that a
            # blocked corridor would be both wrong and unfixable by the person
            # sent to clear it. Relearn instead, and say so.
            if cv2.countNonZero(mask) / total > SCENE_CHANGE_AT:
                self._fresh_model()
                st.note = "Camera view changed — relearning this corridor"
                st.severity, st.fraction, st.confirmed = "CLEAR", 0.0, False
                st.objects, st.unknown_fraction, st.duration_s = named, 0.0, 0.0
                st.reference_ready = False
                return st
            n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
            for i in range(1, n):
                blob = stats[i, cv2.CC_STAT_AREA] / total
                if blob >= MIN_BLOB_FRAC:
                    unknown_frac += blob
            unknown_frac = min(1.0, unknown_frac)

        fraction = min(1.0, named_frac + unknown_frac + standing * 0.05)

        # ---- 4. grade it ----
        severity, note = self._grade(fraction, smoke, fire, standing, named)
        now = time.time()
        violating = severity != "CLEAR"

        if violating:
            self._clear_since = None
            if self._violating_since is None:
                self._violating_since = now
        else:
            self._violating_since = None
            if self._clear_since is None:
                self._clear_since = now

        duration = (now - self._violating_since) if self._violating_since else 0.0
        # smoke or fire in the egress route is not a "wait and see" condition
        confirmed = violating and (duration >= PERSIST_SECONDS or smoke or fire)

        st.severity = severity if confirmed else ("CLEAR" if not violating else "PENDING")
        st.fraction = fraction
        st.objects = named
        st.people_standing = standing
        st.unknown_fraction = unknown_frac
        st.duration_s = duration
        st.confirmed = confirmed
        st.reference_ready = self._bg_frames >= BG_WARMUP_FRAMES
        st.note = note
        st.since = self._violating_since or 0.0
        return st

    # ------------------------------------------------------------- internals
    def _count_standing(self, people: List[Dict], roi_px) -> int:
        """People inside the corridor who are not moving through it."""
        if len(self._track) < 12:
            return 0
        standing = 0
        old = self._track[0]
        for b in people:
            if self._box_overlap_frac(b, roi_px) < 0.004:
                continue
            cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
            # was somebody in roughly this spot several seconds ago?
            if any(abs(cx - ox) < b["w"] * 0.6 and abs(cy - oy) < b["h"] * 0.6
                   for ox, oy in old):
                standing += 1
        return standing

    @staticmethod
    def _explained_mask(shape, boxes, roi_px) -> np.ndarray:
        """Pixels already accounted for by a detection, in ROI coordinates."""
        rx, ry, rw, rh = roi_px
        m = np.zeros(shape, np.uint8)
        for b in boxes:
            x0 = int(max(0, b["x"] - rx)); y0 = int(max(0, b["y"] - ry))
            x1 = int(min(rw, b["x"] + b["w"] - rx)); y1 = int(min(rh, b["y"] + b["h"] - ry))
            if x1 > x0 and y1 > y0:
                m[y0:y1, x0:x1] = 255
        return m

    @staticmethod
    def _grade(fraction: float, smoke: bool, fire: bool, standing: int,
               named: List[Dict]) -> Tuple[str, str]:
        if fire:
            return "BLOCKED", "Flame detected in the egress route"
        if smoke:
            return "BLOCKED", "Egress route is smoke-logged — unusable"
        if fraction >= BLOCKED_AT:
            return "BLOCKED", "Egress route is impassable"
        if fraction >= OBSTRUCTED_AT:
            what = named[0]["label"] if named else "an unidentified object"
            return "OBSTRUCTED", f"Egress width lost to {what}"
        if fraction >= NARROWED_AT:
            if standing >= 3 and not named:
                return "NARROWED", f"{standing} people standing in the egress route"
            what = named[0]["label"] if named else "an unidentified object"
            return "NARROWED", f"Egress route narrowed by {what}"
        return "CLEAR", ""

    # ------------------------------------------------------------------ admin
    def _fresh_model(self):
        self._mog = cv2.createBackgroundSubtractorMOG2(
            history=400, varThreshold=14, detectShadows=True)
        self._bg_frames = 0
        self._violating_since = None

    def set_roi(self, roi: List[float]) -> None:
        self.roi = list(roi)
        self._fresh_model()                        # reference must be relearnt

    def reset_reference(self) -> None:
        """Operator says the corridor is clear right now — learn it as normal."""
        self._fresh_model()

    def state(self) -> ObstructionState:
        return self._state


def clear_width_note(fraction: float, corridor_m: Optional[float]) -> str:
    """Turn a lost-area fraction into words a compliance report can use.

    A camera cannot measure metres on its own, so the metre figure only appears
    when the operator has recorded the corridor's real width — and it is stated
    as approximate, because it is.
    """
    pct = int(round(fraction * 100))
    if not corridor_m:
        return f"~{pct}% of the monitored floor area is obstructed"
    remaining = max(0.0, corridor_m * (1 - fraction))
    return (f"~{pct}% obstructed — roughly {remaining:.1f} m of "
            f"{corridor_m:.1f} m clear width remaining")
