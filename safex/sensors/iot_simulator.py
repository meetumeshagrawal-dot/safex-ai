"""
Mock IoT sensor mesh.

Every operational zone is fitted with a suite of sensors (temperature, gas,
smoke, vibration, noise). Each sensor produces a realistic signal:

    reading = baseline + diurnal_drift + correlated_noise + active_event_ramp

An *event engine* lets operators (or the demo) inject incidents — a solvent
fire, a gas leak, a bearing failure — which ramp the relevant sensors in the
affected zone (and, more weakly, in neighbouring zones) along a physically
plausible curve, then decay once mitigated. This is what makes the platform
fully demoable with zero hardware while still behaving like a real plant.
"""
from __future__ import annotations
import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..settings import SENSOR_ENVELOPE
from .plant_layout import OPERATIONAL_ZONES, ZONE_MAP, get_zone

# Which sensors respond to which event type, and how hard (0-1 gain).
EVENT_SIGNATURES = {
    "fire": {"temperature": 1.0, "smoke": 0.9, "gas": 0.4, "fire": 1.0},
    "gas_leak": {"gas": 1.0, "smoke": 0.15},
    "overheat": {"temperature": 1.0, "vibration": 0.3, "smoke": 0.2},
    "mechanical": {"vibration": 1.0, "noise": 0.7, "temperature": 0.3},
    "smoke": {"smoke": 1.0, "gas": 0.2},
}

EVENT_LABELS = {
    "fire": "Fire / flame",
    "gas_leak": "Combustible gas leak",
    "overheat": "Thermal runaway",
    "mechanical": "Mechanical failure",
    "smoke": "Smoke event",
}

# How fast each event type develops, in intensity units **per second**. A fire
# is deliberately aggressive: an operator who injects one should see the zone
# react immediately, the way a real flame detector would trip.
EVENT_RAMP = {"fire": 0.55, "smoke": 0.45, "gas_leak": 0.38,
              "overheat": 0.30, "mechanical": 0.40}
EVENT_SEED = 0.35             # intensity an event starts at (instant response)
MITIGATION_DECAY = 0.30       # intensity lost per second once responders act
SENSOR_TAU = 0.55             # seconds — sensor response lag (low-pass filter)


@dataclass
class ActiveEvent:
    zone_id: str
    kind: str
    started: float
    intensity: float = EVENT_SEED  # current 0-1 ramp value
    target: float = 1.0            # peak intensity
    ramp_rate: float = 0.5         # per SECOND toward target
    mitigating: bool = False       # true once responders act -> decays
    id: str = field(default_factory=lambda: f"EV{int(time.time()*1000)%100000}")


class SensorMesh:
    """Holds live sensor state for every operational zone."""

    def __init__(self, seed: int = 7):
        self._rng = random.Random(seed)
        self._t0 = time.time()
        self.events: Dict[str, ActiveEvent] = {}
        # Persistent low-frequency drift per sensor so signals feel "alive".
        self._phase = {
            (z.id, s): self._rng.uniform(0, math.tau)
            for z in OPERATIONAL_ZONES for s in SENSOR_ENVELOPE
        }
        self.readings: Dict[str, Dict[str, float]] = {}
        self._init_readings()

    def _init_readings(self):
        for z in OPERATIONAL_ZONES:
            self.readings[z.id] = {
                s: SENSOR_ENVELOPE[s]["nominal"] for s in SENSOR_ENVELOPE
            }
            self.readings[z.id]["fire"] = 0.0  # flame confidence 0-1

    def resync(self):
        """The building changed under us — fit the sensor mesh to the new room
        list without disturbing rooms that are still there."""
        live = {z.id for z in OPERATIONAL_ZONES}
        for zid in [z for z in self.readings if z not in live]:
            self.readings.pop(zid, None)
        for z in OPERATIONAL_ZONES:
            if z.id not in self.readings:
                self.readings[z.id] = {
                    s: SENSOR_ENVELOPE[s]["nominal"] for s in SENSOR_ENVELOPE}
                self.readings[z.id]["fire"] = 0.0
            for s in SENSOR_ENVELOPE:
                self._phase.setdefault((z.id, s), self._rng.uniform(0, math.tau))
        # an event in a room that no longer exists has nowhere to happen
        for ev_id in [i for i, ev in self.events.items() if ev.zone_id not in live]:
            self.events.pop(ev_id, None)

    # ------------------------------------------------------------------ events
    def inject(self, zone_id: str, kind: str, target: float = 1.0) -> ActiveEvent:
        if zone_id not in ZONE_MAP or kind not in EVENT_SIGNATURES:
            raise ValueError(f"bad event {kind} @ {zone_id}")
        tgt = max(0.3, min(1.0, target))
        ev = ActiveEvent(zone_id=zone_id, kind=kind, started=time.time(),
                         target=tgt, intensity=min(EVENT_SEED, tgt),
                         ramp_rate=EVENT_RAMP.get(kind, 0.4))
        self.events[ev.id] = ev
        # apply the event to the sensor field straight away so the very next
        # snapshot already shows it — no waiting for the next physics step
        self.tick(0.0, snap=True)
        return ev

    def mitigate_zone(self, zone_id: str):
        """Mark all events in a zone as mitigating (responders deployed)."""
        for ev in self.events.values():
            if ev.zone_id == zone_id:
                ev.mitigating = True

    def clear(self):
        self.events.clear()
        self._init_readings()

    # -------------------------------------------------------------------- tick
    def tick(self, dt: float = 1.0, snap: bool = False
             ) -> Dict[str, Dict[str, float]]:
        """Integrate the sensor field forward by `dt` seconds.

        Everything is expressed per second, so the caller is free to run the
        loop at 1 Hz or 20 Hz without changing how an incident develops.
        `snap=True` writes the target values directly (no sensor lag), used the
        instant an event is injected so the dashboard reacts immediately.
        """
        now = time.time()
        elapsed = now - self._t0
        dt = max(0.0, min(2.0, dt))

        # advance / retire events
        dead = []
        for ev in self.events.values():
            if ev.mitigating:
                ev.intensity = max(0.0, ev.intensity - MITIGATION_DECAY * dt)
                if ev.intensity <= 0.01:
                    dead.append(ev.id)
            else:
                ev.intensity = min(ev.target, ev.intensity + ev.ramp_rate * dt)
        for d in dead:
            self.events.pop(d, None)

        # build per-zone event contribution (own zone + softened neighbours)
        contrib: Dict[str, Dict[str, float]] = {z.id: {} for z in OPERATIONAL_ZONES}
        for ev in self.events.values():
            sig = EVENT_SIGNATURES[ev.kind]
            for s, gain in sig.items():
                contrib[ev.zone_id][s] = max(
                    contrib[ev.zone_id].get(s, 0.0), gain * ev.intensity)
            # bleed into neighbours (smoke / gas migrate)
            for nb in get_zone(ev.zone_id).neighbors:
                if nb in contrib:
                    for s, gain in sig.items():
                        if s in ("smoke", "gas"):
                            contrib[nb][s] = max(contrib[nb].get(s, 0.0),
                                                 gain * ev.intensity * 0.35)

        # sensor lag: readings chase their target instead of jumping, which is
        # what a real transmitter does (and keeps a 5 Hz feed from looking noisy)
        alpha = 1.0 if snap else (1.0 - math.exp(-dt / SENSOR_TAU) if dt > 0 else 0.0)

        # compute readings
        for z in OPERATIONAL_ZONES:
            for s, env in SENSOR_ENVELOPE.items():
                nominal = env["nominal"]
                span = env["max"] - nominal
                # slow diurnal drift + measurement noise
                drift = math.sin(elapsed / 40 + self._phase[(z.id, s)]) * (span * 0.02)
                noise = self._rng.gauss(0, span * 0.012)
                event_push = contrib[z.id].get(s, 0.0) * span * 0.92
                target = max(nominal * 0.6, nominal + drift + noise + event_push)
                cur = self.readings[z.id].get(s, nominal)
                self.readings[z.id][s] = round(cur + (target - cur) * alpha, 2)
            # flame confidence (0-1) from fire signature — no lag, a flame
            # detector either sees it or it doesn't
            self.readings[z.id]["fire"] = round(contrib[z.id].get("fire", 0.0), 3)

        return self.readings

    # ----------------------------------------------------------------- helpers
    def zone_reading(self, zone_id: str) -> Dict[str, float]:
        return self.readings.get(zone_id, {})

    def active_event_summary(self) -> List[dict]:
        out = []
        for ev in self.events.values():
            out.append({
                "id": ev.id, "zone": ev.zone_id,
                "zone_name": get_zone(ev.zone_id).name,
                "kind": ev.kind, "label": EVENT_LABELS[ev.kind],
                "intensity": round(ev.intensity, 2),
                "mitigating": ev.mitigating,
                "age_s": round(time.time() - ev.started, 1),
            })
        return out
