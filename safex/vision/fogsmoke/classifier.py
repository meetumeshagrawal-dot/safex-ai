"""
Runtime side of the fog/steam discriminator.

Loaded lazily and once. If the model file is absent or unreadable, `available()`
returns False and `predict()` returns None — callers must treat that as "no
opinion" and let the smoke through. A fire system that goes quiet because an
auxiliary model failed to load would be worse than one that never had it.
"""
from __future__ import annotations
import os
import threading

import numpy as np

from .features import extract, N_FEATURES

MODEL_PATH = os.path.join(os.path.dirname(__file__), "fogsmoke.joblib")

_lock = threading.Lock()
_bundle = None
_state = {"status": "unloaded", "detail": "", "metrics": None}


def _load():
    global _bundle
    if _bundle is not None or _state["status"] in ("failed", "missing"):
        return _bundle
    with _lock:
        if _bundle is not None:
            return _bundle
        if not os.path.exists(MODEL_PATH):
            _state.update(status="missing",
                          detail="fogsmoke.joblib not built — run "
                                 "python -m safex.vision.fogsmoke.train")
            return None
        try:
            import joblib
            b = joblib.load(MODEL_PATH)
            if b.get("n_features") != N_FEATURES:
                # features.py changed without retraining: refuse rather than
                # score a vector the model never saw
                _state.update(status="failed",
                              detail=f"model expects {b.get('n_features')} features, "
                                     f"features.py now produces {N_FEATURES} — retrain")
                return None
            _bundle = b
            _state.update(status="ready", detail="fog/steam discrimination active",
                          metrics=b.get("metrics"))
        except Exception as e:
            _state.update(status="failed", detail=str(e)[:120])
            return None
    return _bundle


def available() -> bool:
    return _load() is not None


def status() -> dict:
    _load()
    return dict(_state)


def predict(frame_bgr, box):
    """P(this region is fire smoke) in [0, 1], or None when unavailable.

    Low values mean the region looks like fog, steam or lens haze. `box` is
    (x1, y1, x2, y2) in pixels.
    """
    b = _load()
    if b is None:
        return None
    try:
        x = extract(frame_bgr, box).reshape(1, -1)
        return float(b["model"].predict_proba(x)[0, 1])
    except Exception as e:
        _state.update(status="failed", detail=str(e)[:120])
        return None
