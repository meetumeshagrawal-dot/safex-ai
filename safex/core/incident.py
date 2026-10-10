"""
Incident management.

Turns raw hazard excursions into governed *incidents* with a full audit
timeline (detection -> escalation -> response -> resolution) and produces an
automatic incident report suitable for HSE / regulatory filing. This is the
"paper trail" a real plant is legally required to keep, generated in seconds
instead of hours.
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _ts() -> float:
    return time.time()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S")


@dataclass
class TimelineEvent:
    t: float
    kind: str          # detection | escalation | response | note | resolution
    message: str

    def as_dict(self):
        return {"t": self.t, "time": _iso(self.t), "kind": self.kind,
                "message": self.message}


@dataclass
class Incident:
    zone_id: str
    zone_name: str
    hazard_kind: str
    severity: str
    id: str = field(default_factory=lambda: f"INC-{uuid.uuid4().hex[:6].upper()}")
    opened: float = field(default_factory=_ts)
    closed: Optional[float] = None
    status: str = "OPEN"        # OPEN | ACKNOWLEDGED | RESPONDING | RESOLVED
    peak_score: float = 0.0
    peak_occupancy: int = 0
    timeline: List[TimelineEvent] = field(default_factory=list)

    def log(self, kind: str, message: str):
        self.timeline.append(TimelineEvent(_ts(), kind, message))

    @property
    def duration_s(self) -> float:
        return (self.closed or _ts()) - self.opened

    def as_dict(self):
        return {
            "id": self.id, "zone_id": self.zone_id, "zone_name": self.zone_name,
            "hazard_kind": self.hazard_kind, "severity": self.severity,
            "status": self.status, "opened": self.opened,
            "opened_iso": _iso(self.opened),
            "closed_iso": _iso(self.closed) if self.closed else None,
            "duration_s": round(self.duration_s, 1),
            "peak_score": self.peak_score, "peak_occupancy": self.peak_occupancy,
            "timeline": [e.as_dict() for e in self.timeline],
        }


class IncidentManager:
    """Opens/updates/closes incidents driven by the hazard engine."""

    # A zone must exceed this score to open an incident, and drop below the
    # clear threshold (with hysteresis) to auto-resolve.
    OPEN_SCORE = 55.0
    CLEAR_SCORE = 35.0

    def __init__(self):
        self.active: Dict[str, Incident] = {}     # zone_id -> incident
        self.history: List[Incident] = []

    @staticmethod
    def _severity(score: float) -> str:
        if score >= 85:
            return "CRITICAL"
        if score >= 70:
            return "HIGH"
        if score >= 55:
            return "MEDIUM"
        return "LOW"

    def update(self, zone_id: str, zone_name: str, score: float,
               occupancy: int, dominant: str, mitigating: bool = False):
        """Feed a zone's latest state; manage its incident lifecycle."""
        inc = self.active.get(zone_id)

        if score >= self.OPEN_SCORE and inc is None:
            inc = Incident(zone_id=zone_id, zone_name=zone_name,
                           hazard_kind=dominant, severity=self._severity(score))
            inc.log("detection",
                    f"{dominant.title()} hazard detected in {zone_name} "
                    f"(score {score}). {occupancy} worker(s) in zone.")
            self.active[zone_id] = inc

        if inc is not None:
            inc.peak_score = max(inc.peak_score, score)
            inc.peak_occupancy = max(inc.peak_occupancy, occupancy)
            new_sev = self._severity(max(score, inc.peak_score))
            if SEVERITY_ORDER[new_sev] > SEVERITY_ORDER[inc.severity]:
                inc.severity = new_sev
                inc.log("escalation",
                        f"Escalated to {new_sev} (score {score}).")
            if mitigating and inc.status in ("OPEN", "ACKNOWLEDGED"):
                inc.status = "RESPONDING"
                inc.log("response",
                        "Emergency response deployed; suppression underway.")
            # auto-resolve with hysteresis
            if score <= self.CLEAR_SCORE:
                self.resolve(zone_id, reason="Hazard cleared below safe threshold")

    def acknowledge(self, zone_id: str):
        inc = self.active.get(zone_id)
        if inc and inc.status == "OPEN":
            inc.status = "ACKNOWLEDGED"
            inc.log("note", "Incident acknowledged by control room operator.")

    def resolve(self, zone_id: str, reason: str = "Manually resolved"):
        inc = self.active.pop(zone_id, None)
        if inc:
            inc.status = "RESOLVED"
            inc.closed = _ts()
            inc.log("resolution", reason)
            self.history.insert(0, inc)
        return inc

    # ------------------------------------------------------------------ views
    def snapshot(self) -> dict:
        active = [i.as_dict() for i in sorted(
            self.active.values(), key=lambda x: -x.peak_score)]
        history = [i.as_dict() for i in self.history[:20]]
        return {"active": active, "history": history,
                "active_count": len(active), "total_logged": len(self.history)}

    def get(self, incident_id: str) -> Optional[Incident]:
        for i in list(self.active.values()) + self.history:
            if i.id == incident_id:
                return i
        return None

    # ------------------------------------------------------- automatic report
    def build_report(self, incident_id: str, plant_context: dict) -> Optional[dict]:
        inc = self.get(incident_id)
        if not inc:
            return None
        d = inc.as_dict()
        # root-cause heuristic
        cause_map = {
            "fire": "Ignition of combustible material / flame source",
            "gas": "Loss of containment / combustible gas release",
            "temperature": "Thermal runaway / cooling failure",
            "smoke": "Combustion by-product accumulation",
            "vibration": "Mechanical imbalance / bearing degradation",
        }
        root = cause_map.get(inc.hazard_kind, "Undetermined — see telemetry")
        actions = [
            "Isolate affected zone and adjacent process lines.",
            "Verify all personnel accounted for at muster points.",
            "Inspect and recertify affected equipment before restart.",
            "Review sensor calibration and alarm thresholds for the zone.",
        ]
        return {
            "incident": d,
            "generated_iso": _iso(_ts()),
            "root_cause": root,
            "recommended_actions": actions,
            "plant_context": plant_context,
        }
