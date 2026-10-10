"""
synthetic — renders believable surveillance frames with OpenCV when no
physical camera / model is available (demo + CI). The exact same annotation
overlay (draw_detections) is used for real camera frames, so a live YOLO feed
and a synthetic feed look identical to the operator.

Nothing here touches plant logic; it only turns a detection dict into pixels.
detection dict:
    {fire:bool, fire_conf:float, smoke:bool, smoke_conf:float,
     people:int, boxes:[{x,y,w,h,label,conf}]}
"""
from __future__ import annotations
import math
import time
from typing import Dict, List

import cv2
import numpy as np

W, H = 480, 360
_C_FIRE = (58, 68, 255)      # BGR red
_C_SMOKE = (150, 150, 160)
_C_PERSON = (255, 200, 90)   # BGR cyan-blue
_C_HUD = (219, 236, 55)      # BGR of accent #37ecdb


def _floor(seed: int) -> np.ndarray:
    """Static dark industrial backdrop with perspective floor lines."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (16, 12, 8)
    cv2.rectangle(img, (0, 0), (W, int(H * 0.42)), (26, 20, 14), -1)   # back wall
    vanish = (W // 2 + (seed % 60 - 30), int(H * 0.30))
    for gx in range(-4, 5):
        x = W // 2 + gx * 70
        cv2.line(img, (x, H), vanish, (44, 40, 32), 1, cv2.LINE_AA)
    for i in range(1, 7):                                             # depth rows
        y = int(H * 0.42 + (H - H * 0.42) * (i / 7) ** 1.6)
        cv2.line(img, (0, y), (W, y), (40, 36, 28), 1, cv2.LINE_AA)
    # a couple of equipment blocks for texture
    cv2.rectangle(img, (30, 150), (95, 250), (34, 30, 24), -1)
    cv2.rectangle(img, (W - 110, 140), (W - 40, 235), (34, 30, 24), -1)
    return img


def _person(img, cx, cy, scale, col):
    h = int(52 * scale)
    w = int(20 * scale)
    cv2.ellipse(img, (cx, cy - h), (w // 2, h // 2), 0, 0, 360, (col[0]//3, col[1]//3, col[2]//3), -1)
    cv2.circle(img, (cx, cy - h - int(9 * scale)), int(8 * scale), (col[0]//3, col[1]//3, col[2]//3), -1)


def make_scene(zone_name: str, seed: int, t: float, det: Dict) -> np.ndarray:
    img = _floor(seed)
    boxes: List[Dict] = []

    # people — deterministic wander so counts look alive
    n = int(det.get("people", 0))
    for i in range(n):
        ph = (seed * 7 + i * 53) % 360
        base_x = 70 + (i * 61 + seed * 13) % (W - 140)
        base_y = int(H * 0.55 + ((i * 37) % 100))
        cx = int(base_x + math.sin(t * 0.7 + ph) * 22)
        cy = int(base_y + math.cos(t * 0.5 + ph) * 8)
        scale = 0.75 + (base_y - H * 0.5) / H
        _person(img, cx, cy, scale, _C_PERSON)
        bw, bh = int(26 * scale), int(64 * scale)
        boxes.append({"x": cx - bw // 2, "y": cy - bh, "w": bw, "h": bh,
                      "label": "person", "conf": 0.72 + (i % 5) * 0.04})

    # smoke — translucent drifting blobs
    if det.get("smoke"):
        ov = img.copy()
        for k in range(6):
            sx = int(W * 0.5 + math.sin(t * 0.6 + k) * 60)
            sy = int(H * 0.5 - k * 18 - (t * 20) % 40)
            cv2.circle(ov, (sx, sy), 40 + k * 6, _C_SMOKE, -1)
        img = cv2.addWeighted(ov, 0.28, img, 0.72, 0)
        boxes.append({"x": int(W*0.32), "y": int(H*0.20), "w": int(W*0.36), "h": int(H*0.5),
                      "label": "smoke", "conf": float(det.get("smoke_conf", 0.66))})

    # fire — flickering glow + box
    if det.get("fire"):
        fx, fy = int(W * 0.5), int(H * 0.68)
        ov = img.copy()
        for r in range(60, 10, -10):
            flick = int(r * (0.85 + 0.15 * math.sin(t * 9 + r)))
            cv2.circle(ov, (fx, fy), flick, (20, 90, 255), -1)
        cv2.circle(ov, (fx, fy), 22, (80, 200, 255), -1)
        img = cv2.addWeighted(ov, 0.5, img, 0.5, 0)
        boxes.append({"x": fx - 46, "y": fy - 70, "w": 92, "h": 96,
                      "label": "fire", "conf": float(det.get("fire_conf", 0.88))})

    # sensor grain — cycled from a small pre-rolled bank; generating fresh
    # noise for every frame of every feed is a surprising amount of CPU once
    # several cameras are being watched at once
    img = cv2.add(img, _grain())
    return img, boxes


_GRAIN: List[np.ndarray] = []


def _grain() -> np.ndarray:
    if not _GRAIN:
        for _ in range(8):
            n = np.random.randint(0, 16, (H, W, 1), np.uint8)
            _GRAIN.append(np.repeat(n, 3, axis=2))
    return _GRAIN[int(time.time() * 12) % len(_GRAIN)]


def draw_detections(img: np.ndarray, boxes: List[Dict], cam_label: str,
                    zone: str, fps: float, live: bool, t: float) -> np.ndarray:
    """Shared YOLO-style overlay — used for BOTH real and synthetic frames."""
    for b in boxes:
        lab = b.get("label", "obj")
        col = _C_FIRE if lab == "fire" else _C_SMOKE if lab == "smoke" else _C_PERSON
        x, y, w, h = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
        cv2.rectangle(img, (x, y), (x + w, y + h), col, 2)
        tag = f"{lab} {b.get('conf',0)*100:.0f}%"
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
        cv2.rectangle(img, (x, y - th - 6), (x + tw + 6, y), col, -1)
        cv2.putText(img, tag, (x + 3, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (10, 10, 10), 1, cv2.LINE_AA)

    # scanline
    sy = int((t * 90) % H)
    cv2.line(img, (0, sy), (W, sy), (60, 90, 60), 1)

    # HUD frame
    cv2.rectangle(img, (0, 0), (W - 1, H - 1), (60, 70, 40), 1)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(img, stamp, (10, H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.42, _C_HUD, 1, cv2.LINE_AA)
    cv2.putText(img, f"{cam_label}  |  {zone}", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.46, _C_HUD, 1, cv2.LINE_AA)
    tag = "LIVE" if live else "DEMO"
    tcol = (80, 90, 255) if live else (255, 160, 60)
    cv2.circle(img, (W - 74, 16), 5, tcol, -1)
    cv2.putText(img, tag, (W - 62, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.46, tcol, 1, cv2.LINE_AA)
    cv2.putText(img, f"{fps:4.1f} FPS", (W - 82, H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.40, _C_HUD, 1, cv2.LINE_AA)
    return img


def encode_jpeg(img: np.ndarray, quality: int = 72) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""
