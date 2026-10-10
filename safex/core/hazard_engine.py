"""
Risk Intelligence Engine.

Fuses the heterogeneous signals available for each zone — IoT sensor readings,
computer-vision flame/smoke confidence and live occupancy — into a single,
explainable Hazard Score on a 0-100 scale.

Design goals:
  * **Explainable**: every score comes with a per-factor breakdown so an
    operator (or the Copilot) can say *why* a zone is dangerous.
  * **Consequence-aware**: occupancy and critical-asset zones amplify risk,
    reflecting that the same flame is more serious in a crowded canteen or a
    solvent store than in an empty corridor.
  * **Deterministic**: no ML black box — auditable maths a plant HSE team can
    trust and tune via `settings.HAZARD_WEIGHTS`.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List

from ..settings import HAZARD_WEIGHTS, SENSOR_ENVELOPE, hazard_band
from ..sensors.plant_layout import Zone, get_zone


def _normalise(value: float, env: dict) -> float:
    """Map a sensor reading to 0-1 severity against its safe envelope."""
    nominal, warn, crit = env["nominal"], env["warn"], env["crit"]
    if value <= nominal:
        return 0.0
    if value <= warn:
        return 0.35 * (value - nominal) / max(1e-6, (warn - nominal))
    if value <= crit:
        return 0.35 + 0.45 * (value - warn) / max(1e-6, (crit - warn))
    # beyond critical -> saturating toward 1.0
    over = (value - crit) / max(1e-6, (env["max"] - crit))
    return min(1.0, 0.80 + 0.20 * over)


@dataclass
class ZoneRisk:
    zone_id: str
    name: str
    score: float
    band: str
    color: str
    factors: Dict[str, float] = field(default_factory=dict)   # weighted contribs
    drivers: List[str] = field(default_factory=list)          # human-readable
    occupancy: int = 0
    trend: float = 0.0                                        # score delta / tick


class HazardEngine:
    def __init__(self):
        self._prev: Dict[str, float] = {}
        self._trend: Dict[str, float] = {}

    def score_zone(self, zone: Zone, reading: Dict[str, float],
                   occupancy: int, dt: float = 1.0) -> ZoneRisk:
        factors: Dict[str, float] = {}
        drivers: List[str] = []

        # sensor-derived severities
        for sensor in ("gas", "temperature", "smoke", "vibration"):
            env = SENSOR_ENVELOPE[sensor]
            sev = _normalise(reading.get(sensor, env["nominal"]), env)
            factors[sensor] = sev * HAZARD_WEIGHTS[sensor] * 100
            if sev > 0.55:
                drivers.append(f"{sensor} {reading.get(sensor)}{env['unit']}")

        # vision / flame confidence (already 0-1)
        flame = float(reading.get("fire", 0.0))
        factors["fire"] = flame * HAZARD_WEIGHTS["fire"] * 100
        if flame > 0.4:
            drivers.append(f"flame conf {flame:.0%}")

        # occupancy amplifier — more people => higher consequence
        occ_norm = min(1.0, occupancy / 15.0)
        factors["occupancy"] = occ_norm * HAZARD_WEIGHTS["occupancy"] * 100

        raw = sum(factors.values())

        # consequence multiplier for critical-asset zones
        if zone.critical_asset:
            raw *= 1.12
        score = round(min(100.0, raw), 1)

        # A confirmed flame — or heavy smoke — is a serious hazard in its own
        # right. It must raise the alarm even in an EMPTY room (no occupancy to
        # amplify it), so we floor the score by flame confidence. The lowest
        # rung sits just above the incident threshold, so the first frame a
        # camera calls fire on already opens an incident and speaks an alert.
        flame = float(reading.get("fire", 0.0))
        smoke_read = float(reading.get("smoke", 0.0))
        if flame >= 0.70:
            score = max(score, 82.0)          # CRITICAL
        elif flame >= 0.45:
            score = max(score, 70.0)          # HIGH
        elif flame >= 0.22:
            score = max(score, 58.0)          # opens an incident immediately
        elif smoke_read >= 65:
            score = max(score, 57.0)

        band, color = hazard_band(score)

        # trend is expressed in score points PER SECOND (smoothed), so it means
        # the same thing no matter how fast the control loop runs
        prev = self._prev.get(zone.id)
        rate = 0.0 if (prev is None or dt <= 0) else (score - prev) / dt
        ema = self._trend.get(zone.id, 0.0) * 0.72 + rate * 0.28
        self._trend[zone.id] = ema
        trend = round(ema, 2)
        self._prev[zone.id] = score

        return ZoneRisk(
            zone_id=zone.id, name=zone.name, score=score, band=band,
            color=color, factors={k: round(v, 1) for k, v in factors.items()},
            drivers=drivers, occupancy=occupancy, trend=trend,
        )

    def plant_score(self, zone_risks: List[ZoneRisk]) -> dict:
        """Aggregate plant-wide index (worst-zone weighted, not a flat mean)."""
        if not zone_risks:
            return {"score": 0.0, "band": "NORMAL", "color": "#3ddc97",
                    "worst_zone": None}
        ranked = sorted(zone_risks, key=lambda z: -z.score)
        scores = [z.score for z in ranked]
        # 60% worst zone + 40% mean of top-3 => spikes are felt, noise isn't
        worst = scores[0]
        top3 = sum(scores[:3]) / min(3, len(scores))
        plant = round(0.6 * worst + 0.4 * top3, 1)
        band, color = hazard_band(plant)
        return {"score": plant, "band": band, "color": color,
                "worst_zone": ranked[0].zone_id, "worst_name": ranked[0].name}
