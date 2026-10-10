"""
camera_stream — one camera's worker.

A single code path serves three realities:
  • REAL    : cv2.VideoCapture(source) frames + YOLO detection (user hardware).
  • BROWSER : JPEG frames pushed from a visitor's browser over a websocket.
  • DEMO    : synthetic surveillance frames driven by a detection dict, so the
              whole pipeline (feed, overlays, zone impact, evacuation) is fully
              demonstrable with no camera or model installed.

Design notes that matter for responsiveness
-------------------------------------------
* **Opening never blocks.** `start()` returns immediately; a background thread
  opens the device, retries with backoff, and the camera reports `connecting`
  until frames arrive. Adding a camera from the UI is therefore instant, and a
  device that is plugged in later simply starts working.
* **Capture and detection are separate threads.** The grab loop runs at the
  camera's own frame rate (so the feed is smooth and the FPS read-out is real),
  while a detector thread chews on the newest frame it can get. Slow inference
  can never stall the video — it only makes boxes update a little less often.
* **Detections latch.** The first frame a flame is seen on raises the alarm
  immediately; the detection is then held for a short window so a flicker in
  the model's confidence can't make the alarm chatter.

The camera never talks to plant physics directly — it only exposes a detection
summary + JPEG frames. camera_manager decides how those detections flow into
the hazard engine, honouring the active Simulation / Live mode.
"""
from __future__ import annotations
import platform
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import cv2

from . import synthetic as S
from ..settings import (CAMERA_CAPTURE_FPS, DETECT_MIN_INTERVAL,
                        EGRESS_ALERT_INTERVAL, EGRESS_IDLE_INTERVAL,
                        FIRE_HOLD_SECONDS)

_IS_WINDOWS = platform.system() == "Windows"

# Synthetic feeds are rendered on demand and paced by the clock, not captured.
SYNTH_FPS = 12.0


@dataclass
class CameraConfig:
    id: str
    name: str
    zone_id: str
    source: object = "synthetic"      # 0 / "1" (USB), http/rtsp URL, file path, or "synthetic"
    source_type: str = "synthetic"    # usb | ip | rtsp | file | browser | synthetic


def _classify(source) -> str:
    if isinstance(source, int):
        return "usb"
    s = str(source).strip()
    if s in ("", "synthetic", "demo"):
        return "synthetic"
    if s == "browser":
        return "browser"          # frames pushed from the visitor's browser
    if s.startswith("rtsp"):
        return "rtsp"
    if s.startswith("http"):
        return "ip"
    if s.isdigit():
        return "usb"
    return "file"


def _open_capture(source, source_type: str):
    """Open a capture as fast as the platform allows.

    On Windows the default MSMF backend can take many seconds to open a USB
    webcam (and sometimes fails outright); DirectShow is dramatically quicker,
    so it is tried first and the default backend is kept as a fallback.
    """
    attempts = []
    if source_type == "usb":
        idx = int(source)
        if _IS_WINDOWS:
            attempts = [(idx, cv2.CAP_DSHOW), (idx, cv2.CAP_MSMF), (idx, None)]
        else:
            attempts = [(idx, cv2.CAP_V4L2), (idx, None)]
    elif source_type in ("ip", "rtsp"):
        attempts = [(str(source), cv2.CAP_FFMPEG), (str(source), None)]
    else:                                    # video file
        attempts = [(str(source), None)]

    last_err = ""
    for target, backend in attempts:
        try:
            cap = cv2.VideoCapture(target) if backend is None \
                else cv2.VideoCapture(target, backend)
            if cap is not None and cap.isOpened():
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # always the newest frame
                    if source_type == "usb":
                        # MJPG first: DirectShow's default YUY2 is bandwidth
                        # limited and many webcams drop to 5-10 FPS at 640x480
                        # on it, which looks exactly like a stuttering feed.
                        cap.set(cv2.CAP_PROP_FOURCC,
                                cv2.VideoWriter_fourcc(*"MJPG"))
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                        cap.set(cv2.CAP_PROP_FPS, 30)
                except Exception:
                    pass
                return cap, ""
            if cap is not None:
                cap.release()
            last_err = "device did not open"
        except Exception as e:
            last_err = str(e)[:80]
    return None, last_err or "could not open source"


class CameraStream:
    def __init__(self, cfg: CameraConfig, on_change: Optional[Callable] = None):
        self.cfg = cfg
        self.cfg.source_type = _classify(cfg.source)
        self._on_change = on_change

        self._lock = threading.Lock()
        self._det: Dict = {"fire": False, "fire_conf": 0.0, "smoke": False,
                           "smoke_conf": 0.0, "people": 0, "boxes": []}
        self._boxes: List[Dict] = []
        self._frame = None              # latest raw BGR frame (real / browser)
        self._frame_seq = 0             # increments on every new frame
        self._detected_seq = -1         # last frame the detector consumed
        self._jpeg: bytes = b""
        self._jpeg_seq = -1
        self._scaled: Dict[int, tuple] = {}   # width -> (seq, jpeg bytes)

        self._seed = (abs(hash(cfg.id)) % 97) + 1
        self._t0 = time.time()
        self._cap_fps = 0.0
        self._det_fps = 0.0
        self._last_cap_ts = 0.0
        self._last_det_ts = 0.0
        self._last_frame_ts = 0.0       # last time a frame was decoded

        self._real = False              # a hardware/file capture is delivering
        self._requested_real = (self.cfg.source_type not in ("synthetic",))
        self._is_browser = (self.cfg.source_type == "browser")
        self._connecting = self._requested_real and not self._is_browser
        self._open_error = ""
        self._detector = None
        self._detector_error = ""
        self._detect_passes = 0
        self._ctx = None            # this camera's own detection history
        self._obstruction = None    # egress analyser, if this room has a route
        self._obst_state = None
        self._corridor_m = 0.0
        self._syn_obstruction = None  # staged obstruction for hardware-free demos
        self._homography = None       # image <-> plan mapping, once calibrated
        self._doors: Dict[str, object] = {}   # door_id -> DoorMonitor
        self._assets: Dict[str, object] = {}  # asset_id -> AssetMonitor
        self._syn_assets: Dict[str, Dict] = {}
        self._syn_doors: Dict[str, Dict] = {} # staged door states for demos
        self._obst_next = 0.0       # next scheduled egress check
        self._obst_fast = False     # something is happening — check often

        # detection latch (instant on, graceful off)
        self._fire_until = 0.0
        self._smoke_until = 0.0
        self._fire_conf = 0.0
        self._smoke_conf = 0.0

        self._run = False
        self._threads: List[threading.Thread] = []
        self._new_frame = threading.Event()

    # ------------------------------------------------------------- lifecycle
    def start(self):
        if self._run:
            return
        self._run = True
        if self.cfg.source_type not in ("synthetic", "browser"):
            self._spawn(self._capture_loop, "capture")
        if self.cfg.source_type != "synthetic":
            self._spawn(self._detect_loop, "detect")

    def _spawn(self, fn, tag):
        t = threading.Thread(target=fn, name=f"cam-{self.cfg.id}-{tag}",
                             daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self):
        self._run = False
        self._new_frame.set()

    # ------------------------------------------------------------- properties
    @property
    def is_live(self) -> bool:
        """A real capture, or a browser stream, is a live source."""
        return self._real or self._is_browser

    def is_active(self) -> bool:
        """Currently delivering frames (so it should drive its zone)."""
        return (time.time() - self._last_frame_ts) < 3.0

    def wants_live(self) -> bool:
        return self._requested_real

    # ------------------------------------------------------------ capture loop
    def _capture_loop(self):
        """Grab frames as fast as the device delivers them. Never runs
        inference — that belongs to the detect thread."""
        backoff = 1.0
        cap = None
        fails = 0
        min_dt = 1.0 / max(1, CAMERA_CAPTURE_FPS)
        while self._run:
            if cap is None:
                self._connecting = True
                cap, err = _open_capture(self.cfg.source, self.cfg.source_type)
                if cap is None:
                    self._open_error = err
                    self._real = False
                    self._connecting = False
                    # retry — a camera plugged in later should just start working
                    for _ in range(int(backoff * 10)):
                        if not self._run:
                            return
                        time.sleep(0.1)
                    backoff = min(8.0, backoff * 2)
                    self._connecting = True
                    continue
                backoff = 1.0
                self._open_error = ""

            t_start = time.time()
            try:
                ok, frame = cap.read()
            except Exception as e:
                ok, frame = False, None
                self._open_error = f"read error: {str(e)[:60]}"

            if not ok or frame is None:
                if self.cfg.source_type == "file":
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)     # loop video files
                    continue
                # A dropped grab is normal on USB webcams (Windows MSMF does it
                # regularly). Tearing the device down and reopening it on the
                # first miss is what makes a feed freeze for a second at a time,
                # so ride out a short run of failures first.
                fails += 1
                if fails < 40:                              # ~1s of hiccups
                    time.sleep(0.025)
                    continue
                self._real = False
                try:
                    cap.release()
                except Exception:
                    pass
                cap = None
                fails = 0
                self._open_error = self._open_error or "stream dropped — reconnecting"
                continue
            fails = 0

            frame = cv2.resize(frame, (S.W, S.H))
            with self._lock:
                self._frame = frame
                self._frame_seq += 1
                self._last_frame_ts = time.time()
            self._real = True
            self._connecting = False
            self._tick_fps("cap")
            self._new_frame.set()

            spent = time.time() - t_start
            if spent < min_dt:
                time.sleep(min_dt - spent)

        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    # ------------------------------------------------------------- detect loop
    def _detect_loop(self):
        """Run the models on the freshest frame available, forever."""
        while self._run:
            det = self._ensure_detector()
            if det is None:
                time.sleep(1.5)                    # models unavailable — retry
                continue
            self._new_frame.wait(0.5)
            self._new_frame.clear()
            if not self._run:
                return
            with self._lock:
                if self._frame is None or self._frame_seq == self._detected_seq:
                    continue
                frame = self._frame.copy()
                seq = self._frame_seq
            self._detected_seq = seq
            try:
                boxes, summ = det.detect(
                    frame, self._ctx,
                    want_people=(self._detect_passes % 2 == 0))
            except Exception as e:
                self._detector_error = f"detect error: {str(e)[:70]}"
                time.sleep(0.2)
                continue
            self._detect_passes += 1
            self._tick_fps("det")
            det_state = self._apply_detection(boxes, summ)
            self._maybe_check_egress(frame, boxes, det_state)
            if DETECT_MIN_INTERVAL:
                time.sleep(DETECT_MIN_INTERVAL)

    def _ensure_detector(self):
        if self._detector is not None:
            return self._detector
        try:
            from .detectors import DetectContext, get_detector
            self._detector = get_detector()
            # the models are shared; the history behind them must not be
            self._ctx = DetectContext()
            self._detector_error = ""
        except Exception as e:
            self._detector_error = f"detector unavailable: {str(e)[:60]}"
            self._detector = None
        return self._detector

    # ------------------------------------------------------------ detection io
    def _apply_detection(self, boxes: List[Dict], summ: Dict) -> Dict:
        """Merge a raw model result into the latched detection state."""
        now = time.time()
        raw_fire = bool(summ.get("fire"))
        raw_smoke = bool(summ.get("smoke"))
        was_fire = now < self._fire_until
        was_smoke = now < self._smoke_until

        if raw_fire:
            self._fire_until = now + FIRE_HOLD_SECONDS
            self._fire_conf = max(float(summ.get("fire_conf", 0.0)),
                                  self._fire_conf * 0.6)
        if raw_smoke:
            self._smoke_until = now + FIRE_HOLD_SECONDS
            self._smoke_conf = max(float(summ.get("smoke_conf", 0.0)),
                                   self._smoke_conf * 0.6)

        fire = raw_fire or now < self._fire_until
        smoke = raw_smoke or now < self._smoke_until
        if not fire:
            self._fire_conf = 0.0
        if not smoke:
            self._smoke_conf = 0.0

        det = {
            "fire": fire, "fire_conf": round(self._fire_conf if fire else 0.0, 2),
            "smoke": smoke, "smoke_conf": round(self._smoke_conf if smoke else 0.0, 2),
            "people": 0, "boxes": boxes,
        }
        people = summ.get("people")
        with self._lock:
            # a pass that skipped the person model reports None — keep the last
            # known count so the read-out doesn't blink between 3 and 0
            det["people"] = int(self._det.get("people", 0)) if people is None \
                else int(people)
            self._det = det
            self._boxes = boxes
        # a change of alarm state is worth waking the plant engine for
        if self._on_change and (fire != was_fire or smoke != was_smoke):
            try:
                self._on_change(self.cfg.id, det)
            except Exception:
                pass
        return det

    # --------------------------------------------------- egress obstruction
    def watch_egress(self, enabled: bool, roi=None, corridor_m: float = 0.0):
        """Turn corridor monitoring on for this camera.

        Only cameras whose room carries a monitored egress route run this —
        analysing every feed for obstructions would spend CPU watching rooms
        where a parked chair is furniture, not a violation.
        """
        if not enabled:
            self._obstruction = None
            self._obst_state = None
            return
        from .obstruction import ObstructionAnalyser
        if self._obstruction is None:
            self._obstruction = ObstructionAnalyser(roi)
        elif roi and list(roi) != self._obstruction.roi:
            self._obstruction.set_roi(list(roi))
        self._corridor_m = float(corridor_m or 0.0)

    def _maybe_check_egress(self, frame, boxes, det_state):
        """Run the corridor check on a duty cycle.

        A corridor obstruction is a *static* condition — a trolley parked in a
        fire exit is still there four seconds later — so checking it on every
        inference pass would burn CPU to re-learn the same answer twenty times a
        second. It runs slowly while the building is calm, and switches to every
        pass the moment fire or smoke is involved, because then the question
        changes from "is this a violation" to "can people still get out".
        """
        if self._obstruction is None and not self._doors and not self._assets:
            return
        alarm = bool(det_state.get("fire") or det_state.get("smoke") or self._obst_fast)
        now = time.time()
        if not alarm and now < self._obst_next:
            return
        self._obst_next = now + (EGRESS_ALERT_INTERVAL if alarm
                                 else EGRESS_IDLE_INTERVAL)
        for mon in self._doors.values():
            try:
                mon.check(frame, alarm=alarm)
            except Exception as e:
                self._detector_error = f"door check failed: {str(e)[:50]}"
        if self._assets:
            with self._lock:
                boxes = list(self._boxes)
            for mon in self._assets.values():
                try:
                    mon.check(frame, boxes)
                except Exception as e:
                    self._detector_error = f"asset check failed: {str(e)[:50]}"
        if self._obstruction is None:
            return
        try:
            self._obst_state = self._obstruction.analyse(
                frame, boxes, smoke=det_state.get("smoke", False),
                fire=det_state.get("fire", False))
        except Exception as e:
            self._detector_error = f"egress check failed: {str(e)[:50]}"

    def set_egress_urgency(self, fast: bool):
        """The plant is in alarm — check every corridor continuously."""
        self._obst_fast = bool(fast)

    # ------------------------------------------------------- plan calibration
    def set_calibration(self, data):
        from .calibration import load
        self._homography = load(data)

    def calibrated(self) -> bool:
        return self._homography is not None

    def plan_detections(self) -> List[Dict]:
        """Where this camera's detections are standing, on the floor plan.

        Only meaningful once calibrated — an uncalibrated camera knows what it
        sees but not where it is, and guessing a position would put people
        through walls on the operator's map.
        """
        if self._homography is None:
            return []
        with self._lock:
            boxes = list(self._boxes)
        out = []
        for b in boxes:
            p = self._homography.foot_point(b, S.W, S.H)
            if p is None:
                continue
            if not (-0.05 <= p[0] <= 1.05 and -0.05 <= p[1] <= 1.05):
                continue                    # projected outside the plan
            out.append({"label": b.get("label", "object"),
                        "conf": round(float(b.get("conf", 0)), 2),
                        "x": round(p[0], 4), "y": round(p[1], 4)})
        return out

    def roi_for_plan_rect(self, rect) -> Optional[List[float]]:
        """Turn a rectangle drawn on the plan into an image region to watch."""
        if self._homography is None:
            return None
        return self._homography.plan_bbox_in_image(rect)

    # ------------------------------------------------------------ fire doors
    def watch_doors(self, specs: List[Dict]):
        """Attach the fire doors this camera can see.

        specs: [{id, roi, reference, held_open_seconds, label}]
        """
        from .doorwatch import DoorMonitor
        keep = set()
        for spec in specs or []:
            did = spec.get("id")
            if not did:
                continue
            keep.add(did)
            mon = self._doors.get(did)
            if mon is None:
                self._doors[did] = DoorMonitor(
                    did, spec.get("roi") or [0.35, 0.15, 0.3, 0.7],
                    reference=spec.get("reference"),
                    held_open_seconds=spec.get("held_open_seconds") or 30.0,
                    label=spec.get("label", ""))
            else:
                if spec.get("roi"):
                    mon.set_roi(spec["roi"])
                mon.label = spec.get("label", mon.label)
        for gone in [d for d in self._doors if d not in keep]:
            self._doors.pop(gone, None)

    # ------------------------------------------------------ fire-safety assets
    def watch_assets(self, specs: List[Dict]):
        """Attach the extinguishers, signs and call points this camera can see."""
        from .assetwatch import AssetMonitor
        keep = set()
        for spec in specs or []:
            aid = spec.get("id")
            if not aid:
                continue
            keep.add(aid)
            mon = self._assets.get(aid)
            if mon is None:
                self._assets[aid] = AssetMonitor(
                    aid, spec.get("kind", "extinguisher"),
                    spec.get("roi") or [0.45, 0.4, 0.1, 0.2],
                    reference=spec.get("reference"), label=spec.get("label", ""))
            elif spec.get("roi"):
                mon.set_roi(spec["roi"])
        for gone in [a for a in self._assets if a not in keep]:
            self._assets.pop(gone, None)

    def capture_asset_reference(self, asset_id: str) -> Optional[str]:
        mon = self._assets.get(asset_id)
        if mon is None:
            return None
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
        if frame is None:
            return "synthetic" if self._is_synthetic() else None
        return mon.capture_reference(frame)

    def set_synthetic_asset(self, asset_id: str, state: Optional[Dict]):
        if not self._is_synthetic():
            return
        if state is None:
            self._syn_assets.pop(asset_id, None)
        else:
            self._syn_assets[asset_id] = state

    def assets(self) -> List[Dict]:
        out = []
        for aid, mon in self._assets.items():
            syn = self._syn_assets.get(aid)
            if syn is not None and self._is_synthetic():
                out.append({"asset": aid, "kind": mon.kind, "label": mon.label, **syn})
            else:
                out.append({"asset": aid, "kind": mon.kind, "label": mon.label,
                            **mon.state().as_dict()})
        for aid, syn in self._syn_assets.items():
            if aid not in self._assets:
                out.append({"asset": aid, "kind": "extinguisher", "label": aid, **syn})
        return out

    def capture_door_reference(self, door_id: str) -> Optional[str]:
        """Learn this door in its current (closed) position."""
        mon = self._doors.get(door_id)
        if mon is None:
            return None
        with self._lock:
            frame = None if self._frame is None else self._frame.copy()
        if frame is None and self._is_synthetic():
            return "synthetic"          # demo feeds need no reference
        return mon.capture_reference(frame) if frame is not None else None

    def set_door_sensor(self, door_id: str, state: Optional[str]):
        mon = self._doors.get(door_id)
        if mon is not None:
            mon.set_contact_sensor(state)

    def set_synthetic_door(self, door_id: str, state: Optional[Dict]):
        if not self._is_synthetic():
            return
        if state is None:
            self._syn_doors.pop(door_id, None)
        else:
            self._syn_doors[door_id] = state

    def doors(self) -> List[Dict]:
        out = []
        for did, mon in self._doors.items():
            syn = self._syn_doors.get(did)
            if syn is not None and self._is_synthetic():
                out.append({"door": did, "label": mon.label, **syn})
            else:
                out.append({"door": did, "label": mon.label, **mon.state().as_dict()})
        # a synthetic camera may carry staged doors it never built a monitor for
        for did, syn in self._syn_doors.items():
            if did not in self._doors:
                out.append({"door": did, "label": "", **syn})
        return out

    def mark_corridor_clear(self):
        """Operator confirms the corridor is clear — relearn it as normal."""
        if self._obstruction:
            self._obstruction.reset_reference()
            return True
        return False

    def set_synthetic_obstruction(self, state: Optional[Dict]):
        """Demo path: stage a corridor obstruction on a feed with no camera.

        Real hardware produces this from the frame; without hardware the whole
        compliance pipeline still has to be demonstrable end to end, exactly
        like the fire trigger.
        """
        if not self._is_synthetic():
            return
        self._syn_obstruction = state

    def obstruction(self) -> Optional[Dict]:
        syn = getattr(self, "_syn_obstruction", None)
        if syn is not None and self._is_synthetic():
            from .obstruction import clear_width_note
            return {**syn, "summary": clear_width_note(syn.get("fraction", 0),
                                                       self._corridor_m),
                    "corridor_m": self._corridor_m, "synthetic": True}
        if not self._obst_state:
            return None
        from .obstruction import clear_width_note
        d = self._obst_state.as_dict()
        d["summary"] = clear_width_note(self._obst_state.fraction, self._corridor_m)
        d["corridor_m"] = self._corridor_m
        return d

    def set_synthetic_detection(self, det: Dict):
        """Only used when this stream is synthetic (no real capture)."""
        if not self._is_synthetic():
            return
        with self._lock:
            self._det = dict(det)

    # ------------------------------------------ browser stream input (/ws/camera)
    def process_browser_frame(self, data: bytes) -> Dict:
        """Take one JPEG frame from the browser and answer immediately.

        Inference deliberately does **not** happen here. Running YOLO inside the
        send/reply loop meant the browser could only send one frame per
        inference (~3 FPS), and any burst queued up behind the model, so the
        picture drifted further and further behind reality.

        Now this only decodes and stores the newest frame — a couple of
        milliseconds — and the camera's own detect thread picks up whatever the
        latest frame happens to be, skipping everything in between. The video
        therefore runs at full rate and stays live, while detections land a
        fraction of a second later, which is the right trade for an alarm.
        """
        import numpy as np
        arr = np.frombuffer(data, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return {"ok": False, "error": "bad frame"}
        if frame.shape[1] != S.W or frame.shape[0] != S.H:
            frame = cv2.resize(frame, (S.W, S.H))
        with self._lock:
            self._frame = frame
            self._frame_seq += 1
            self._last_frame_ts = time.time()
        self._tick_fps("cap")
        self._new_frame.set()               # wake this camera's detect thread

        det = self.detection()               # newest result the detector has
        return {
            "ok": True, "w": S.W, "h": S.H,
            "fps": round(self._cap_fps, 1), "detect_fps": round(self._det_fps, 1),
            "fire": det["fire"], "fire_conf": det["fire_conf"],
            "smoke": det["smoke"], "smoke_conf": det["smoke_conf"],
            "people": det["people"], "boxes": det.get("boxes", []),
            "detector": self._detector is not None,
            "detector_error": self._detector_error,
        }

    # --------------------------------------------------------------------- fps
    def _tick_fps(self, which: str):
        now = time.time()
        if which == "cap":
            last, self._last_cap_ts = self._last_cap_ts, now
        else:
            last, self._last_det_ts = self._last_det_ts, now
        if not last:
            return
        dt = now - last
        if dt <= 0:
            return
        # clamp: two frames arriving back-to-back would otherwise spike the
        # average into nonsense like "119 FPS" on a 12 FPS feed
        inst = min(60.0, 1.0 / dt)
        if which == "cap":
            self._cap_fps = inst if not self._cap_fps else \
                self._cap_fps * 0.85 + inst * 0.15
        else:
            self._det_fps = inst if not self._det_fps else \
                self._det_fps * 0.8 + inst * 0.2

    # ------------------------------------------------------------ frame access
    def frame_seq(self) -> int:
        """Monotonic id of the newest renderable frame. Synthetic feeds are
        rendered on demand, so they advertise a clock-driven sequence that
        paces them at a steady ~12 FPS."""
        if self._is_synthetic():
            return int(time.time() * SYNTH_FPS)
        with self._lock:
            if self._frame is not None and self.is_active():
                return self._frame_seq
        # offline / connecting: refresh the status card a couple of times a sec
        return -int(time.time() * 2)

    def frame_jpeg_scaled(self, width: int = 0, quality: int = 72) -> bytes:
        """A JPEG at a requested width.

        The strip tiles are ~120 px wide; sending them the full 480 px frame is
        four times the bytes, the encode cost and the decode cost for no visible
        gain. Results are cached per width so several viewers of the same size
        share one encode.
        """
        if width <= 0 or width >= S.W:
            return self.frame_jpeg()
        seq = self.frame_seq()
        cached = self._scaled.get(width)
        if cached and cached[0] == seq:
            return cached[1]
        full = self.frame_jpeg()
        if not full:
            return full
        import numpy as np
        img = cv2.imdecode(np.frombuffer(full, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return full
        h = max(1, int(S.H * (width / float(S.W))))
        small = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
        jpg = S.encode_jpeg(small, quality)
        self._scaled[width] = (seq, jpg)
        return jpg

    def frame_jpeg(self) -> bytes:
        # anything that asked for a real source shows that source (or an honest
        # "connecting / offline" card) — never a fake scene pretending to be it
        if not self._is_synthetic():
            with self._lock:
                frame = self._frame if self.is_active() else None
                seq = self._frame_seq
                cached, cached_seq = self._jpeg, self._jpeg_seq
                boxes = list(self._boxes)
            if frame is not None:
                if cached and cached_seq == seq:
                    return cached                    # share work between viewers
                annotated = S.draw_detections(frame.copy(), boxes, self.cfg.name,
                                              self.cfg.zone_id, self._cap_fps,
                                              True, time.time() - self._t0)
                jpg = S.encode_jpeg(annotated)
                with self._lock:
                    self._jpeg, self._jpeg_seq = jpg, seq
                return jpg
            return self._placeholder()

        # synthetic: render on demand from the current detection, and share the
        # result — the strip, the monitor wall and the zone drawer can all be
        # watching the same camera, and rendering it three times is pure waste
        seq = self.frame_seq()
        with self._lock:
            if self._jpeg and self._jpeg_seq == seq:
                return self._jpeg
            det = dict(self._det)
        t = time.time() - self._t0 + 3
        img, boxes = S.make_scene(self.cfg.name, self._seed, t, det)
        det_boxes = det.get("boxes") or boxes
        # NOTE: no fps tick here. A synthetic feed is rendered on demand, so
        # counting renders would count *viewers* — two panels watching the same
        # demo camera used to make it claim 100+ FPS. It is paced at SYNTH_FPS.
        img = S.draw_detections(img, det_boxes, self.cfg.name, self.cfg.zone_id,
                                SYNTH_FPS, False, t)
        jpg = S.encode_jpeg(img)
        with self._lock:
            self._jpeg, self._jpeg_seq = jpg, seq
        return jpg

    def _placeholder(self) -> bytes:
        """Live source with no frame yet — say so on the tile instead of 0 FPS."""
        img, _ = S.make_scene(self.cfg.name, self._seed,
                              time.time() - self._t0, {"people": 0})
        img = S.draw_detections(img, [], self.cfg.name, self.cfg.zone_id, 0.0,
                                True, time.time() - self._t0)
        if self._is_browser:
            msg = "WAITING FOR BROWSER CAMERA"
        elif self._connecting:
            msg = "CONNECTING TO SOURCE..."
        else:
            msg = (self._open_error or "SOURCE OFFLINE")[:34].upper()
        (tw, _), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.putText(img, msg, (max(8, S.W // 2 - tw // 2), S.H // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (219, 236, 55), 1, cv2.LINE_AA)
        return S.encode_jpeg(img)

    # ------------------------------------------------------------------- views
    def detection(self) -> Dict:
        with self._lock:
            d = dict(self._det)
        # expire a latch even if the detector stopped producing frames
        now = time.time()
        if not self._is_synthetic():
            if d.get("fire") and now >= self._fire_until:
                d["fire"], d["fire_conf"] = False, 0.0
            if d.get("smoke") and now >= self._smoke_until:
                d["smoke"], d["smoke_conf"] = False, 0.0
        d["fps"] = SYNTH_FPS if self._is_synthetic() else round(self._cap_fps, 1)
        d["detect_fps"] = round(self._det_fps, 1)
        return d

    def _is_synthetic(self) -> bool:
        return self.cfg.source_type == "synthetic"

    def status(self) -> str:
        if self._is_browser:
            return "live" if self.is_active() else "waiting"
        if self._real and self.is_active():
            return "live"
        if self._requested_real:
            return "connecting" if self._connecting else "offline"
        return "demo"               # synthetic by design

    def summary(self) -> Dict:
        d = self.detection()
        from ..sensors.plant_layout import ZONE_MAP
        z = ZONE_MAP.get(self.cfg.zone_id)
        return {
            "id": self.cfg.id, "name": self.cfg.name,
            "zone_id": self.cfg.zone_id,
            "zone_name": z.name if z else self.cfg.zone_id,
            "floor": z.floor if z else 0,
            "source_type": self.cfg.source_type,
            "source": str(self.cfg.source), "status": self.status(),
            "open_error": self._open_error or self._detector_error,
            "detector": self._detector is not None,
            "live": bool(self.is_live and self.is_active()),
            "fire": bool(d["fire"]), "fire_conf": round(d["fire_conf"], 2),
            "smoke": bool(d["smoke"]), "smoke_conf": round(d["smoke_conf"], 2),
            "people": int(d["people"]), "fps": d["fps"],
            "detect_fps": d["detect_fps"],
            "obstruction": self.obstruction(),
            "doors": self.doors(),
            "assets": self.assets(),
            "calibrated": self.calibrated(),
            "plan_detections": self.plan_detections(),
            "watches_egress": bool(self._obstruction is not None
                                   or self._syn_obstruction is not None
                                   or self._corridor_m),
        }
