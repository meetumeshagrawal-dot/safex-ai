"""Fire door monitoring — is this door doing its job?

A fire door only works closed. Held open by a wedge, a chair or a stacked box,
it is a hole in the compartmentation, and smoke goes through it in seconds.
Hospitals prop them open constantly, for entirely understandable reasons, which
is why this is one of the most common findings in a real fire audit.

**A door being open is not a violation.** People walk through doors. The
violation is a door that *stays* open — so this measures how long, and only
raises it past a threshold the operator sets per door. During an alarm that
grace period disappears: an open fire door while something is burning is
immediate, because the compartment is failing right then.

How the state is read, given there is no dataset of "this hospital's doors":

* **Reference comparison.** The operator captures the door closed, once. Every
  check compares the door region against that reference. Matching is done on
  mean-subtracted correlation plus edge structure, both of which survive
  lighting changes far better than raw pixels — a corridor light coming on must
  not read as a door swinging open.
* **A contact sensor, when there is one.** Real life-safety installations use a
  magnetic reed switch, and it is far more reliable than any camera. If one is
  wired in, it is taken as authoritative — and when the two disagree, that is
  *reported as a disagreement* rather than quietly resolved, because a camera
  and a switch contradicting each other means one of them is broken and
  somebody needs to know.
"""
from __future__ import annotations
import base64
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

REF_SIZE = 96                  # reference thumbnails are stored at this size
# Calibrated so that a shut door scores well clear of the line while a door
# merely AJAR still reads as open — a gap is a hole in the compartmentation,
# smoke does not need the door to be wide open. Measured on the closed state:
# identical 0.96, lights raised 0.86, someone standing in front 0.90, camera
# nudged two pixels 0.84; ajar 20 degrees scores 0.60.
# These are defaults: real doors vary, and each door can override them.
CLOSED_AT = 0.75               # similarity at or above this reads as closed
OPEN_BELOW = 0.66              # ...and at or below this as open
HELD_OPEN_SECONDS = 30.0       # how long a door may stand open before it counts
ALARM_GRACE_SECONDS = 3.0      # ...while something is burning


@dataclass
class DoorState:
    state: str = "UNCALIBRATED"    # CLOSED | OPEN | HELD_OPEN | UNCALIBRATED | DISPUTED
    similarity: float = 0.0
    open_since: float = 0.0
    open_for_s: float = 0.0
    source: str = "camera"         # camera | contact | fused
    sensor_state: str = ""         # what the contact sensor says, if any
    note: str = ""
    violation: bool = False

    def as_dict(self) -> Dict:
        return {"state": self.state, "similarity": round(self.similarity, 2),
                "open_for_s": round(self.open_for_s, 1), "source": self.source,
                "sensor_state": self.sensor_state, "note": self.note,
                "violation": self.violation}


def encode_reference(img: np.ndarray) -> str:
    """Reference thumbnails travel inside the site document, so they survive a
    restart and move with the building — hence a small PNG in base64 rather
    than a file on disk nobody would ever clean up."""
    ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


def decode_reference(data: str) -> Optional[np.ndarray]:
    try:
        raw = np.frombuffer(base64.b64decode(data), np.uint8)
        return cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None


def _prep(patch: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    g = cv2.resize(g, (REF_SIZE, REF_SIZE), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(g, (3, 3), 0)


def similarity(current: np.ndarray, reference: np.ndarray) -> float:
    """How much this looks like the reference, 0-1.

    Two measures, because either alone is fooled: correlation handles overall
    appearance but drifts with light, edge agreement handles structure but is
    noisy on flat surfaces. A door that swings open changes both.
    """
    cur, ref = _prep(current), _prep(reference)
    # mean-subtracted correlation — immune to a uniform brightness shift
    corr = float(cv2.matchTemplate(cur, ref, cv2.TM_CCOEFF_NORMED)[0][0])
    corr = max(0.0, min(1.0, (corr + 1) / 2))

    ec, er = cv2.Canny(cur, 60, 160), cv2.Canny(ref, 60, 160)
    # Dilate before comparing: a camera shifts by a pixel or two with vibration
    # and temperature, and demanding pixel-exact edge agreement would score a
    # perfectly shut door the same as an open one. Tolerating a couple of
    # pixels is what makes the closed state read as clearly closed.
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    ecd, erd = cv2.dilate(ec, k), cv2.dilate(er, k)
    inter = float(np.count_nonzero(cv2.bitwise_and(ecd, erd)))
    union = float(np.count_nonzero(cv2.bitwise_or(ecd, erd)))
    edge = inter / union if union > 20 else corr      # flat door: trust corr

    return round(0.6 * corr + 0.4 * edge, 3)


class DoorMonitor:
    """One fire door, watched by one camera."""

    def __init__(self, door_id: str, roi: List[float],
                 reference: Optional[str] = None,
                 held_open_seconds: float = HELD_OPEN_SECONDS,
                 label: str = "", closed_at: float = CLOSED_AT,
                 open_below: float = OPEN_BELOW):
        self.door_id = door_id
        self.label = label
        self.closed_at = float(closed_at or CLOSED_AT)
        self.open_below = float(open_below or OPEN_BELOW)
        self.roi = list(roi or [0.35, 0.15, 0.3, 0.7])
        self.held_open_seconds = float(held_open_seconds or HELD_OPEN_SECONDS)
        self._ref = decode_reference(reference) if reference else None
        self._state = DoorState(state="UNCALIBRATED" if self._ref is None else "CLOSED")
        self._open_since: Optional[float] = None
        self._sensor: Optional[str] = None        # "open" / "closed" if wired
        self._sensor_ts = 0.0
        self._last_patch: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ setup
    def _crop(self, frame) -> np.ndarray:
        h, w = frame.shape[:2]
        x0, y0 = int(self.roi[0] * w), int(self.roi[1] * h)
        x1 = min(w, x0 + int(self.roi[2] * w))
        y1 = min(h, y0 + int(self.roi[3] * h))
        x0, y0 = max(0, x0), max(0, y0)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return frame
        return frame[y0:y1, x0:x1]

    def capture_reference(self, frame=None) -> Optional[str]:
        """Learn what this door looks like closed. Returns the encoded thumbnail
        for the caller to persist on the door's marker."""
        patch = self._crop(frame) if frame is not None else self._last_patch
        if patch is None:
            return None
        self._ref = _prep(patch)
        self._state.state = "CLOSED"
        self._state.note = "Reference captured"
        self._open_since = None
        return encode_reference(self._ref)

    def set_roi(self, roi: List[float]):
        self.roi = list(roi)

    def set_contact_sensor(self, state: Optional[str]):
        """Feed a magnetic contact sensor reading: 'open', 'closed' or None."""
        self._sensor = state if state in ("open", "closed") else None
        self._sensor_ts = time.time()

    # ------------------------------------------------------------------- main
    def check(self, frame, alarm: bool = False) -> DoorState:
        st = self._state
        patch = self._crop(frame)
        self._last_patch = patch
        now = time.time()

        if self._ref is None:
            st.state, st.violation = "UNCALIBRATED", False
            st.note = "Capture this door closed once to start monitoring it"
            st.source = "camera"
            return st

        sim = similarity(patch, self._ref)
        st.similarity = sim

        # hysteresis so a person passing in front of the door does not toggle it
        if sim >= self.closed_at:
            cam_state = "closed"
        elif sim <= self.open_below:
            cam_state = "open"
        else:
            cam_state = "open" if self._open_since else "closed"

        # a wired contact sensor outranks the camera, but a contradiction is
        # itself the finding — one of the two is broken
        disputed = False
        state = cam_state
        source = "camera"
        if self._sensor is not None and now - self._sensor_ts < 120:
            source = "contact" if self._sensor != cam_state else "fused"
            disputed = self._sensor != cam_state
            state = self._sensor

        if state == "open":
            if self._open_since is None:
                self._open_since = now
        else:
            self._open_since = None

        open_for = (now - self._open_since) if self._open_since else 0.0
        limit = ALARM_GRACE_SECONDS if alarm else self.held_open_seconds
        held = state == "open" and open_for >= limit

        st.source, st.sensor_state = source, self._sensor or ""
        st.open_since = self._open_since or 0.0
        st.open_for_s = open_for
        st.violation = held or (disputed and not held)

        if disputed:
            st.state = "DISPUTED"
            st.note = (f"Door contact says {self._sensor}, camera says {cam_state} "
                       f"— one of them needs checking")
        elif held:
            st.state = "HELD_OPEN"
            st.note = (f"Fire door held open {int(open_for)}s during an alarm"
                       if alarm else
                       f"Fire door has been open {int(open_for // 60) or 1} min"
                       if open_for >= 60 else
                       f"Fire door held open {int(open_for)}s")
        elif state == "open":
            st.state = "OPEN"
            st.note = f"Door open {int(open_for)}s (in use)"
        else:
            st.state = "CLOSED"
            st.note = ""
        return st

    def state(self) -> DoorState:
        return self._state

    def calibrated(self) -> bool:
        return self._ref is not None
