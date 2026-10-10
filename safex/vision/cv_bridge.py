"""
Camera → PlantEngine bridge.

Runs the original YOLOv8 detectors (bundled `best.pt` for fire/smoke and
`yolov8n.pt` for people — the exact models already in this repo) on one or more
camera feeds in a background thread, and writes the results into the live engine
as computer-vision signals:

    engine.cv_flame[zone]   flame confidence 0-1   (fuses into Hazard Score)
    engine.cv_people[zone]  detected head-count    (drives occupancy)

Each physical camera is mapped to a plant zone via CAMERA_ZONE_MAP so a real
deployment can wire "Camera 3 watches the Boiler Room". The heavy imports live
inside `start_camera_bridge()` so the rest of the platform never depends on them.
"""
from __future__ import annotations
import threading
import time

# Map each camera index (from config.CAMERA_SOURCES order) to a plant zone.
# Adjust to match where each camera physically points.
CAMERA_ZONE_MAP = {0: "A1", 1: "Z6"}   # laptop cam -> Control Room (L2), phone -> Solvent Storage (G)

_bridge_thread = None
_running = False


def start_camera_bridge():
    """Start the CV pipeline in a background thread. Raises if deps missing."""
    global _bridge_thread, _running
    if _running:
        return
    # heavy imports — only now
    import cv2  # noqa: F401  (import-time validation that opencv is present)
    from ..core.engine import engine
    from .detectors import CameraWorker

    _running = True

    def _loop():
        worker = CameraWorker()
        while _running:
            try:
                results = worker.step()   # {cam_id: {"flame":conf, "people":n}}
                # reset then apply (so a cleared feed drops to 0 next tick)
                engine.cv_flame.clear()
                engine.cv_people.clear()
                for cam_id, r in results.items():
                    zone = CAMERA_ZONE_MAP.get(cam_id)
                    if not zone:
                        continue
                    if r["flame"] > 0:
                        engine.cv_flame[zone] = r["flame"]
                    if r["people"] > 0:
                        engine.cv_people[zone] = r["people"]
            except Exception as e:
                print(f"[cv_bridge] {e}")
                time.sleep(1)
            time.sleep(0.1)
        worker.release()

    _bridge_thread = threading.Thread(target=_loop, daemon=True)
    _bridge_thread.start()


def stop_camera_bridge():
    global _running
    _running = False
