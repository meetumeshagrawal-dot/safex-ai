"""
Safety Analytics & Predictive Risk.

Two responsibilities:
  1. **KPIs / executive metrics** — the numbers a plant manager reports up:
     safety index, incidents today, MTTR, workers protected, uptime.
  2. **Predictive risk** — a lightweight forecast that projects each zone's
     hazard score a short horizon ahead using the slope of its recent history
     (a damped linear extrapolation). No heavy ML, but genuinely useful: it
     flags zones that are *trending* into danger before they cross a threshold,
     which is the whole value proposition of "predictive" safety.
"""
from __future__ import annotations
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List


class Analytics:
    def __init__(self, history: int = 60):
        self.history = history
        self._score_hist: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=history))
        self._plant_hist: Deque[float] = deque(maxlen=history)
        self.session_start = time.time()
        self.incidents_opened = 0
        self.resolutions = 0
        self.resolution_times: List[float] = []
        self.peak_plant_score = 0.0

    # ---------------------------------------------------------------- ingest
    def record(self, zone_risks: List, plant: dict):
        for zr in zone_risks:
            self._score_hist[zr.zone_id].append(zr.score)
        self._plant_hist.append(plant["score"])
        self.peak_plant_score = max(self.peak_plant_score, plant["score"])

    def record_incident_opened(self):
        self.incidents_opened += 1

    def record_incident_resolved(self, duration_s: float):
        self.resolutions += 1
        self.resolution_times.append(duration_s)

    # ------------------------------------------------------------- predictive
    def forecast(self, zone_id: str, horizon: int = 8) -> dict:
        """Project a zone's hazard score `horizon` ticks ahead."""
        hist = list(self._score_hist.get(zone_id, []))
        if len(hist) < 4:
            cur = hist[-1] if hist else 0.0
            return {"current": cur, "predicted": cur, "slope": 0.0,
                    "confidence": 0.0}
        window = hist[-8:]
        n = len(window)
        xs = list(range(n))
        mean_x = sum(xs) / n
        mean_y = sum(window) / n
        denom = sum((x - mean_x) ** 2 for x in xs) or 1e-6
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, window)) / denom
        # damped projection (risk rarely rises linearly forever)
        predicted = window[-1] + slope * horizon * 0.7
        predicted = max(0.0, min(100.0, predicted))
        # confidence from fit tightness
        var = sum((y - mean_y) ** 2 for y in window) / n
        conf = max(0.0, min(1.0, 1.0 - (var / 900.0)))
        return {"current": round(window[-1], 1), "predicted": round(predicted, 1),
                "slope": round(slope, 2), "confidence": round(conf, 2)}

    def predictive_watchlist(self, zone_risks: List) -> List[dict]:
        """Zones predicted to escalate — sorted by projected risk delta."""
        out = []
        for zr in zone_risks:
            f = self.forecast(zr.zone_id)
            delta = f["predicted"] - f["current"]
            if delta > 3 and f["predicted"] > 40:
                out.append({
                    "zone_id": zr.zone_id, "zone_name": zr.name,
                    "current": f["current"], "predicted": f["predicted"],
                    "delta": round(delta, 1), "confidence": f["confidence"],
                    "eta_ticks": max(1, int((55 - f["current"]) / max(0.1, f["slope"])))
                    if f["slope"] > 0 else None,
                })
        return sorted(out, key=lambda d: -d["delta"])[:5]

    # -------------------------------------------------------------------- KPIs
    def kpis(self, plant: dict, people_at_risk: int, active_incidents: int,
             protected: int) -> dict:
        safety_index = round(max(0.0, 100.0 - plant["score"]), 1)
        mttr = (sum(self.resolution_times) / len(self.resolution_times)
                if self.resolution_times else 0.0)
        uptime = round(time.time() - self.session_start, 0)
        return {
            "safety_index": safety_index,
            "plant_hazard": plant["score"],
            "incidents_today": self.incidents_opened,
            "active_incidents": active_incidents,
            "resolved": self.resolutions,
            "mttr_s": round(mttr, 1),
            "people_at_risk": people_at_risk,
            "workers_protected": protected,
            "peak_hazard": self.peak_plant_score,
            "monitoring_uptime_s": uptime,
        }

    def plant_history(self) -> List[float]:
        return list(self._plant_hist)

    def zone_history(self, zone_id: str) -> List[float]:
        return list(self._score_hist.get(zone_id, []))
