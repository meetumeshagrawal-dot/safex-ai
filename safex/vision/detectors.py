"""
Camera worker — reuses the project's original detection logic.

This wraps the same YOLOv8 models and heuristics from the original
`fire_detection.py` and `people_detection.py`, so the proven detection code is
preserved rather than rewritten. Everything is imported lazily by cv_bridge.
"""
from __future__ import annotations
import os

import cv2
from ultralytics import YOLO

# resolve model paths relative to project root (models sit at repo root)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FIRE_MODEL = os.path.join(_ROOT, "best.pt")
_PEOPLE_MODEL = os.path.join(_ROOT, "yolov8n.pt")


def _cfg_sources():
    try:
        from config import CAMERA_SOURCES  # original repo config
        return CAMERA_SOURCES
    except Exception:
        return [0]


class CameraWorker:
    def __init__(self):
        self.fire_model = YOLO(_FIRE_MODEL)
        self.people_model = YOLO(_PEOPLE_MODEL)
        self.caps = []
        for src in _cfg_sources():
            cap = cv2.VideoCapture(src)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.caps.append(cap)

    def step(self) -> dict:
        out = {}
        for cam_id, cap in enumerate(self.caps):
            ok, frame = cap.read()
            if not ok or frame is None:
                out[cam_id] = {"flame": 0.0, "people": 0}
                continue
            flame = self._detect_flame(frame)
            people = self._detect_people(frame)
            out[cam_id] = {"flame": flame, "people": people}
        return out

    def _detect_flame(self, frame) -> float:
        best = 0.0
        for r in self.fire_model(frame, verbose=False, stream=True):
            for box in r.boxes:
                conf = float(box.conf[0])
                if conf < 0.25:
                    continue
                label = self.fire_model.names[int(box.cls[0])].lower()
                if label in ("fire", "smoke"):
                    best = max(best, conf)
        return round(best, 3)

    def _detect_people(self, frame) -> int:
        count = 0
        for r in self.people_model(frame, imgsz=320, conf=0.3, verbose=False):
            for box in r.boxes:
                if int(box.cls[0]) != 0:
                    continue
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                if (y2 - y1) < 40 or (x2 - x1) < 20:
                    continue
                count += 1
        return count

    def release(self):
        for cap in self.caps:
            try:
                cap.release()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Per-frame Detector — used by CameraStream (which owns the capture).
#
# Keeps the project's three detection methods, tuned for LATENCY so a flame is
# called on the first frame it appears in:
#   • fire   : best.pt YOLO at a reduced input size (fast, flame recall is not
#              sensitive to resolution — a fire is a big, bright blob)
#   • smoke  : best.pt's own smoke class, plus a classical grey-drift check
#              that now requires the region to be grey, smooth AND moving, so
#              an ordinary textured room no longer reads as smoke
#   • people : yolov8n.pt YOLO, run every other pass and cached in between —
#              headcount does not need to update 20 times a second, flame does
# Returns overlay boxes + an aggregate summary the engine can consume.
# ---------------------------------------------------------------------------
import time as _time
from collections import deque as _deque

import cv2 as _cv2
import numpy as _np

from ..settings import (FIRE_CONF_TRIGGER, FOG_STRICT_BELOW, FOG_VETO_BELOW,
                        PERSON_OVERLAP_VETO,
                        PERSON_CONF, SKIN_MAX_IN_BOX, SMOKE_CONF_TRIGGER,
                        SMOKE_PERSIST_HITS, SMOKE_PERSIST_WINDOW,
                        YOLO_IMGSZ_FIRE, YOLO_IMGSZ_PEOPLE)
from .fogsmoke import classifier as _fogsmoke


class DetectContext:
    """Per-camera scratch state.

    The Detector itself is a shared singleton (loading the models twice would
    double the memory and the warm-up), so anything that is *about one camera*
    — its previous frame, its cached headcount, its smoke persistence history —
    has to live here. Sharing this state between cameras would mean camera B
    inheriting camera A's motion history and person boxes.
    """

    def __init__(self):
        self.prev_gray = None
        self.people: list = []
        self.smoke_hits = _deque(maxlen=32)      # timestamps of accepted smoke


def _skin_fraction(ycrcb, box) -> float:
    """Fraction of skin-tone pixels inside a box.

    Standard YCrCb skin gate. A face, a hand or a bare arm scores high; smoke —
    which is grey and colourless by definition — scores near zero.
    """
    x, y, w, h = int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
    H, W = ycrcb.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    patch = ycrcb[y0:y1, x0:x1]
    cr, cb = patch[:, :, 1], patch[:, :, 2]
    skin = (cr > 133) & (cr < 173) & (cb > 77) & (cb < 127)
    return float(skin.mean())


def _overlap_with_people(box, people) -> float:
    """How much of `box` sits inside any detected person (0-1)."""
    bx0, by0 = box["x"], box["y"]
    bx1, by1 = bx0 + box["w"], by0 + box["h"]
    area = max(1.0, float(box["w"] * box["h"]))
    best = 0.0
    for p in people:
        px0, py0 = p["x"], p["y"]
        px1, py1 = px0 + p["w"], py0 + p["h"]
        iw = max(0, min(bx1, px1) - max(bx0, px0))
        ih = max(0, min(by1, py1) - max(by0, py0))
        best = max(best, (iw * ih) / area)
    return best


class Detector:
    def __init__(self):
        self.fire_model = YOLO(_FIRE_MODEL)
        self.people_model = YOLO(_PEOPLE_MODEL)
        # Some torch / ultralytics version combos crash inside the automatic
        # layer-fusing step with: "'Conv' object has no attribute 'bn'".
        # Fusing is only a speed optimisation (identical detections without it),
        # so we make it a safe no-op to guarantee the models run everywhere.
        def _no_fuse(model_ref):
            def fuse(*a, **k):
                return model_ref
            return fuse
        for m in (self.fire_model, self.people_model):
            try:
                m.model.fuse = _no_fuse(m.model)
            except Exception:
                pass
        self._fallback_ctx = DetectContext()     # used if a caller passes none
        self.warm = False

    def warmup(self):
        """Run one inference on a blank frame so the first REAL frame doesn't
        pay the lazy-initialisation cost (which can be several seconds)."""
        if self.warm:
            return
        blank = _np.zeros((360, 480, 3), _np.uint8)
        try:
            self.fire_model.predict(blank, imgsz=YOLO_IMGSZ_FIRE, verbose=False)
            self.people_model.predict(blank, imgsz=YOLO_IMGSZ_PEOPLE, verbose=False)
        except Exception:
            pass
        self.warm = True

    # --- fire + smoke (best.pt) ---
    def _fire(self, frame):
        boxes, fire_conf, smoke_conf = [], 0.0, 0.0
        results = self.fire_model.predict(frame, imgsz=YOLO_IMGSZ_FIRE,
                                          conf=FIRE_CONF_TRIGGER, verbose=False)
        for r in results:
            for box in r.boxes:
                conf = float(box.conf[0])
                label = self.fire_model.names[int(box.cls[0])].lower()
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                b = {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1, "conf": conf}
                if label in ("fire", "flame"):
                    fire_conf = max(fire_conf, conf)
                    boxes.append({**b, "label": "fire"})
                elif label == "smoke":
                    smoke_conf = max(smoke_conf, conf)
                    boxes.append({**b, "label": "smoke"})
        return boxes, fire_conf, smoke_conf

    # --- classical grey-drift measure: CORROBORATION ONLY ---
    def _smoke_texture_hint(self, frame, ctx) -> float:
        """A 0-1 "this looks like drifting grey haze" score.

        This deliberately **cannot raise an alarm on its own**. It keys on
        colourlessness, softness and movement, and an ordinary indoor webcam
        scene — a plain wall, a face, and the sensor noise every camera
        produces — satisfies all three. (Measured: a static wall plus a face
        plus typical webcam noise tripped the old standalone version on 6 of 6
        frames.) It is only ever used to nudge the confidence of a smoke box
        the model has already found.
        """
        gray = _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY)
        prev, ctx.prev_gray = ctx.prev_gray, gray
        if prev is None:
            return 0.0
        hsv = _cv2.cvtColor(frame, _cv2.COLOR_BGR2HSV)
        mask = ((hsv[:, :, 1] < 62) & (hsv[:, :, 2] > 85) &
                (hsv[:, :, 2] < 225)).astype(_np.uint8)
        coverage = float(mask.mean())
        if coverage < 0.30 or not mask.any():
            return 0.0
        motion = float(_cv2.absdiff(gray, prev)[mask > 0].mean())
        detail = float(_cv2.Laplacian(gray, _cv2.CV_16S).var())
        if motion < 2.5 or detail > 900:
            return 0.0
        return round(min(1.0, (coverage - 0.30) / 0.35), 2)

    # --- people (yolov8n) ---
    def _people(self, frame):
        """Count people, with filters that suit a hospital rather than a plant.

        The original filters assumed everyone is upright and mid-frame, and
        threw away any box wider than it is tall. In a hospital that rejects
        exactly the people who matter most: a patient lying on a bed or a
        trolley is far wider than tall, somebody sitting close to the camera is
        roughly square, and a person walking into shot is at the edge of the
        frame. Only genuinely absurd boxes are dropped now.
        """
        boxes = []
        h_frame, w_frame = frame.shape[:2]
        results = self.people_model.predict(frame, imgsz=YOLO_IMGSZ_PEOPLE,
                                            conf=PERSON_CONF, classes=[0],
                                            verbose=False)
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                w, h = x2 - x1, y2 - y1
                if h < 26 or w < 16 or w * h < 1100:
                    continue                      # too small to be a person here
                ar = h / max(w, 1)
                if ar < 0.25 or ar > 5.0:
                    continue                      # a sliver or a letterbox, not a body
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                if not (0.01 * w_frame < cx < 0.99 * w_frame):
                    continue
                if not (0.01 * h_frame < cy < 0.99 * h_frame):
                    continue
                boxes.append({"x": x1, "y": y1, "w": w, "h": h,
                              "label": "person", "conf": float(box.conf[0])})
        return boxes

    def detect(self, frame, ctx: "DetectContext" = None, want_people: bool = True):
        """Run detection on one frame.

        `ctx` carries the calling camera's own history (see DetectContext).
        `want_people=False` skips the person model for this pass and reuses the
        last headcount — the caller alternates so flame latency stays minimal.
        The returned summary's `people` is None on a skipped pass, which tells
        the caller to keep whatever count it already had.
        """
        ctx = ctx or self._fallback_ctx
        raw_boxes, fire_conf, smoke_conf_raw = self._fire(frame)

        if want_people:
            pboxes = self._people(frame)
            ctx.people = pboxes
            people = len(pboxes)
        else:
            pboxes = ctx.people              # reuse so overlays don't flicker
            people = None

        ycrcb = _cv2.cvtColor(frame, _cv2.COLOR_BGR2YCrCb)
        hint = self._smoke_texture_hint(frame, ctx)

        # Flame anywhere in the frame settles the question — haze next to a fire
        # is smoke, and no amount of optics should be allowed to argue otherwise.
        flame_present = any(b["label"] == "fire" and b["conf"] >= FIRE_CONF_TRIGGER
                            for b in raw_boxes)

        # ---- sanity-check every candidate box against the person picture ----
        fire_boxes, smoke_boxes = [], []
        fire_conf, smoke_conf = 0.0, 0.0
        water_like = False           # something hazy, but it looked like steam
        for b in raw_boxes:
            skin = _skin_fraction(ycrcb, b)
            on_person = _overlap_with_people(b, pboxes)
            if b["label"] == "smoke":
                # a face, a hand or a grey jumper is not smoke, no matter what
                # the model scored it
                if b["conf"] < SMOKE_CONF_TRIGGER:
                    continue
                if skin > SKIN_MAX_IN_BOX or on_person > PERSON_OVERLAP_VETO:
                    continue
                # ---- is this a fire product, or a water one? ----------------
                if not flame_present:
                    p = _fogsmoke.predict(frame, (b["x1"], b["y1"], b["x2"], b["y2"]))
                    if p is not None:
                        b["smoke_p"] = round(p, 3)
                        if p < FOG_VETO_BELOW:
                            # confidently steam, fog or a hazed-over lens
                            water_like = True
                            continue
                        if p < FOG_STRICT_BELOW:
                            # not sure — keep it, but it has to earn confirmation
                            # the slow way rather than on corroborating haze
                            b["needs_full_persist"] = True
                smoke_boxes.append(b)
                smoke_conf = max(smoke_conf, b["conf"])
            else:
                # warm skin under tungsten light can read as flame to a weak
                # detection — reject only when it is clearly a person's skin
                # AND the model was not confident
                if b["conf"] < 0.5 and skin > 0.35 and on_person > PERSON_OVERLAP_VETO:
                    continue
                fire_boxes.append(b)
                fire_conf = max(fire_conf, b["conf"])

        # ---- smoke must also persist; fire is called on the first frame ----
        now = _time.time()
        smoke = False
        if smoke_boxes:
            ctx.smoke_hits.append(now)
            # corroborating haze in the wider scene lets it confirm sooner —
            # unless the fog model was unconvinced, in which case the haze it
            # would be corroborating with is exactly what is in doubt
            doubted = any(b.get("needs_full_persist") for b in smoke_boxes)
            early = hint > 0.4 and not doubted
            needed = max(2, SMOKE_PERSIST_HITS - (1 if early else 0))
            recent = [t for t in ctx.smoke_hits if now - t <= SMOKE_PERSIST_WINDOW]
            smoke = len(recent) >= needed
            if smoke and hint > 0.4:
                smoke_conf = min(0.99, smoke_conf + 0.05)
        elif ctx.smoke_hits and now - ctx.smoke_hits[-1] > SMOKE_PERSIST_WINDOW:
            ctx.smoke_hits.clear()

        boxes = fire_boxes + (smoke_boxes if smoke else []) + pboxes
        summary = {
            "fire": fire_conf >= FIRE_CONF_TRIGGER, "fire_conf": round(fire_conf, 2),
            "smoke": smoke, "smoke_conf": round(smoke_conf if smoke else 0.0, 2),
            "people": people,
        }
        # Say when something hazy was seen and deliberately not called smoke.
        # A quiet suppression is indistinguishable from a blind camera, and the
        # operator is entitled to know which one they are looking at.
        if water_like and not smoke:
            summary["water_vapour"] = True
        return boxes, summary


_DETECTOR = None
_DETECTOR_LOCK = __import__("threading").Lock()

# Loading torch and the two models takes tens of seconds on a cold start, and
# it happens on a background thread so the dashboard is up immediately. That
# gap is invisible unless it is reported: a camera added during it looks
# perfectly healthy and simply never detects anything, which is the worst
# possible way to find out.
VISION_STATE = {"status": "idle", "detail": "", "since": 0.0, "seconds": 0.0}


def vision_status() -> dict:
    import time as _t
    st = dict(VISION_STATE)
    if st["status"] == "loading" and st["since"]:
        st["seconds"] = round(_t.time() - st["since"], 1)
    # The fog/steam model is optional. Report it either way — running without
    # it is a legitimate state, but silently running without it is not.
    fs = _fogsmoke.status()
    st["fogsmoke"] = {"status": fs["status"], "detail": fs["detail"],
                      "metrics": fs.get("metrics")}
    return st


def get_detector():
    """Singleton real detector (lazy — only when a live camera opens)."""
    global _DETECTOR
    import time as _t
    with _DETECTOR_LOCK:
        if _DETECTOR is None:
            VISION_STATE.update(status="loading", since=_t.time(),
                                detail="loading torch and the detection models")
            try:
                _DETECTOR = Detector()
                _DETECTOR.warmup()
            except Exception as e:
                VISION_STATE.update(status="failed", detail=str(e)[:120])
                raise
            VISION_STATE.update(
                status="ready", detail="fire, smoke and person detection live",
                seconds=round(_t.time() - VISION_STATE["since"], 1))
    return _DETECTOR


def preload_detector_async():
    """Load + warm the models on a background thread at server start, so the
    first camera a user adds detects on its very first frame instead of
    stalling for several seconds while torch wakes up."""
    import threading

    def _run():
        try:
            get_detector()
            print("   ✓ vision models ready (fire + person)")
        except Exception as e:                       # pragma: no cover
            print(f"   ⚠ vision models unavailable: {str(e)[:90]}")

    threading.Thread(target=_run, name="detector-preload", daemon=True).start()
