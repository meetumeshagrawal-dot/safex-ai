"""
Smart Alerts.

Converts hazard state into a prioritised, de-duplicated alert feed. Alerts
carry a severity, a target zone, a recommended action and a "speak" flag used
by the browser voice-alert system. De-duplication with a cooldown prevents the
control room being flooded — a common failing of naive threshold alarms.
"""
from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

PRIORITY = {"INFO": 0, "WARNING": 1, "DANGER": 2, "CRITICAL": 3}


@dataclass
class Alert:
    severity: str
    zone_id: str
    zone_name: str
    title: str
    action: str
    t: float = field(default_factory=time.time)
    speak: bool = False
    voice: str = ""          # full spoken PA announcement (evacuation + exit)
    id: str = field(default_factory=lambda: f"AL{int(time.time()*1000)%1000000}")

    def as_dict(self):
        return {"id": self.id, "severity": self.severity, "zone_id": self.zone_id,
                "zone_name": self.zone_name, "title": self.title,
                "action": self.action, "t": self.t, "speak": self.speak,
                "voice": self.voice}


class AlertManager:
    def __init__(self, cooldown: float = 12.0, keep: int = 60):
        self.cooldown = cooldown
        self.feed: Deque[Alert] = deque(maxlen=keep)
        self._last_key_time: Dict[str, float] = {}

    def _emit(self, key: str, alert: Alert) -> Optional[Alert]:
        now = time.time()
        if now - self._last_key_time.get(key, 0) < self.cooldown:
            return None
        self._last_key_time[key] = now
        self.feed.appendleft(alert)
        return alert

    def evaluate(self, zone_risks: List, plant: dict,
                 evacuation: dict) -> List[Alert]:
        """Produce alerts for the current tick (returns only *new* ones)."""
        new: List[Alert] = []
        routes = evacuation.get("routes", {})

        def evac_voice(zr):
            """Spoken PA-system evacuation announcement naming the nearest exit."""
            from ..sensors.plant_layout import ZONE_MAP
            r = routes.get(zr.zone_id)
            occ = getattr(zr, "occupancy", 0)
            who = (f"{occ} personnel" if occ else "All personnel")
            if r and not r.get("blocked") and r.get("exit"):
                ex = r["exit"]
                exit_name = ZONE_MAP[ex].name if ex in ZONE_MAP else ex
                return (f"Attention. Emergency in {zr.name}. {who}, evacuate "
                        f"immediately. Nearest safe exit is the {exit_name}. "
                        f"Move there now and clear the zone.")
            return (f"Attention. Emergency in {zr.name}. {who}, evacuate now. "
                    f"Primary exits are blocked — await marshal instructions.")

        for zr in zone_risks:
            if zr.band == "CRITICAL":
                a = self._emit(f"crit:{zr.zone_id}", Alert(
                    "CRITICAL", zr.zone_id, zr.name,
                    f"CRITICAL hazard in {zr.name} (score {zr.score})",
                    "Initiate zone evacuation and dispatch emergency response.",
                    speak=True, voice=evac_voice(zr)))
                if a:
                    new.append(a)
            elif zr.band == "HIGH":
                # a high hazard with people present also gets an evac announcement
                occ = getattr(zr, "occupancy", 0)
                a = self._emit(f"high:{zr.zone_id}", Alert(
                    "DANGER", zr.zone_id, zr.name,
                    f"High hazard rising in {zr.name} (score {zr.score})",
                    "Acknowledge and pre-position response team.",
                    speak=True,
                    voice=evac_voice(zr) if occ else
                          f"Warning. High hazard detected in {zr.name}. "
                          f"Response team, pre-position and stand by."))
                if a:
                    new.append(a)
            elif zr.band == "ELEVATED" and zr.trend > 4:
                a = self._emit(f"trend:{zr.zone_id}", Alert(
                    "WARNING", zr.zone_id, zr.name,
                    f"Rapidly rising risk in {zr.name} (+{zr.trend}/tick)",
                    "Investigate source before it escalates.",))
                if a:
                    new.append(a)

        for zid in evacuation.get("blocked_zones", []):
            from ..sensors.plant_layout import ZONE_MAP
            zname = ZONE_MAP[zid].name if zid in ZONE_MAP else zid
            a = self._emit(f"blocked:{zid}", Alert(
                "CRITICAL", zid, zname,
                f"Evacuation route BLOCKED from {zname}",
                "All safe exits impassable — open alternative egress immediately.",
                speak=True,
                voice=(f"Danger. Evacuation routes from {zname} are blocked. "
                       f"Do not use primary exits. Await rescue instructions.")))
            if a:
                new.append(a)

        return new

    def compliance_alert(self, v: dict) -> Optional[Alert]:
        """Raise a fire-safety compliance violation.

        Deliberately worded for the person who has to fix it — where it is,
        what is in the way, and what to do — because unlike a fire alarm this
        one is actionable long before anything is burning.
        """
        sev = {"NARROWED": "WARNING", "OBSTRUCTED": "DANGER",
               "BLOCKED": "CRITICAL"}.get(v["severity"], "WARNING")
        speak = v["severity"] in ("OBSTRUCTED", "BLOCKED")

        if v.get("type") == "asset":
            title = f"{v.get('label','Fire-safety asset')} — {v['room_name']}"
            voice = (f"Fire safety alert. {v.get('label','An asset')} in "
                     f"{v['room_name']} is {v.get('state','unavailable').lower()}.")
        elif v.get("type") == "fire_door":
            # a held-open fire door is a different failure from a blocked
            # corridor, and telling a nurse to "clear the escape route" when
            # the problem is a wedge under a door helps nobody
            door = v.get("label") or "Fire door"
            title = f"{door} not closed — {v['room_name']}"
            voice = (f"Fire safety alert. {door} in {v['room_name']} is being "
                     f"held open. Close it now.")
        else:
            what = ", ".join(o["label"] for o in (v.get("objects") or [])[:2])
            title = (f"Escape route {v['severity'].lower()} in {v['room_name']}"
                     + (f" — {what}" if what else ""))
            voice = (f"Fire safety alert. The escape route in {v['room_name']} is "
                     f"{v['severity'].lower()}. Clear it immediately.")

        return self._emit(f"compliance:{v['key']}", Alert(
            sev, v["room"], v["room_name"], title,
            v.get("action", "Clear the escape route."),
            speak=speak, voice=voice if speak else ""))

    def snapshot(self, limit: int = 25) -> List[dict]:
        return [a.as_dict() for a in list(self.feed)[:limit]]
