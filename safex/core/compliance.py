"""Fire-safety compliance state.

Turns what the cameras see into the language a hospital's fire officer actually
works in: violations with a location, a severity, an age and a recommended
action, plus a score per floor that says whether this building would pass an
inspection right now.

The distinction that matters here is between a *hazard* and a *violation*. A
fire is a hazard — it is happening and it drives the emergency response. A
trolley parked across a fire exit is a violation — nothing is burning, but the
building is not compliant, and the time to find out is a Tuesday afternoon and
not during an evacuation. This module owns the second kind, and the two meet
when a violation sits on a route people are being asked to use.
"""
from __future__ import annotations
import time
from typing import Dict, List

# What each violation costs the compliance score of its floor.
PENALTY = {"NARROWED": 4.0, "OBSTRUCTED": 12.0, "BLOCKED": 25.0}

SEVERITY_RANK = {"CLEAR": 0, "PENDING": 0, "NARROWED": 1,
                 "OBSTRUCTED": 2, "BLOCKED": 3}

ACTIONS = {
    "NARROWED": "Move the obstruction clear of the escape route.",
    "OBSTRUCTED": "Clear this route now — required escape width is not available.",
    "BLOCKED": "Escape route unusable. Clear it immediately and re-route "
               "anyone relying on it.",
}

ASSET_ACTIONS = {
    "BLOCKED": "Clear the access to it — an asset that cannot be reached in "
               "seconds is not available.",
    "MISSING": "Check whether it has been removed, borrowed or relocated, and "
               "restore it to its bracket.",
    "UNLIT": "The sign is not illuminated. Check its lamp and its supply.",
}

DOOR_ACTIONS = {
    "HELD_OPEN": "Release the door and remove whatever is holding it. A fire "
                 "door only works closed.",
    "DISPUTED": "Door contact and camera disagree - check the door and the "
                "sensor; one of them is faulty.",
}


class ComplianceMonitor:
    """Keeps the current violation set and its history."""

    def __init__(self):
        self._open: Dict[str, dict] = {}     # key -> violation
        self._history: List[dict] = []
        self._resolved_count = 0

    # ------------------------------------------------------------------ ingest
    def update(self, egress: List[dict], zone_bands: Dict[str, str],
               doors: List[dict] = None, assets: List[dict] = None) -> List[dict]:
        """Feed the latest corridor and fire-door readings.

        Returns violations that are newly opened, so the caller can announce
        them once rather than on every pass.
        """
        now = time.time()
        seen, opened = set(), []

        for d in (doors or []):
            if not d.get("violation"):
                continue
            key = "door:" + str(d.get("door"))
            seen.add(key)
            state = d.get("state", "HELD_OPEN")
            sev = "BLOCKED" if state == "HELD_OPEN" else "OBSTRUCTED"
            existing = self._open.get(key)
            if existing:
                existing.update(severity=sev, detail=d.get("note", ""),
                                last_seen=now,
                                duration_s=round(now - existing["opened"], 1))
            else:
                v = {
                    "id": "CV-%d" % (int(now*1000) % 1000000), "key": key,
                    "type": "fire_door", "severity": sev,
                    "room": d.get("room", ""), "room_name": d.get("room_name", ""),
                    "floor": d.get("floor", 0),
                    "detail": d.get("note", "Fire door not closed"),
                    "label": d.get("label") or d.get("door"),
                    "summary": "%s · read by %s" % (d.get("label") or d.get("door"),
                                                    d.get("source", "camera")),
                    "objects": [], "fraction": 0.0,
                    "camera": d.get("camera", ""),
                    "camera_name": d.get("camera_name", ""),
                    "opened": now, "last_seen": now, "duration_s": 0.0,
                    "action": DOOR_ACTIONS.get(state, "Close the fire door."),
                }
                self._open[key] = v
                opened.append(v)

        for a in (assets or []):
            if not a.get("violation"):
                continue
            key = "asset:" + str(a.get("asset"))
            seen.add(key)
            state = a.get("state", "BLOCKED")
            # a missing extinguisher is worse than an obstructed one: the
            # obstructed one can at least be dug out
            sev = "BLOCKED" if state == "MISSING" else "OBSTRUCTED"
            existing = self._open.get(key)
            if existing:
                existing.update(severity=sev, detail=a.get("note", ""),
                                last_seen=now,
                                duration_s=round(now - existing["opened"], 1))
            else:
                v = {
                    "id": "CV-%d" % (int(now*1000) % 1000000), "key": key,
                    "type": "asset", "asset_kind": a.get("kind", ""),
                    "severity": sev, "state": state,
                    "room": a.get("room", ""), "room_name": a.get("room_name", ""),
                    "floor": a.get("floor", 0),
                    "detail": a.get("note", "Fire-safety asset unavailable"),
                    "label": a.get("label") or a.get("asset"),
                    "summary": "%s · %s" % (a.get("label") or a.get("asset"), state.lower()),
                    "objects": [], "fraction": float(a.get("cover", 0) or 0),
                    "camera": a.get("camera", ""),
                    "camera_name": a.get("camera_name", ""),
                    "opened": now, "last_seen": now, "duration_s": 0.0,
                    "action": ASSET_ACTIONS.get(state, "Restore this asset to service."),
                }
                self._open[key] = v
                opened.append(v)

        for e in egress:
            sev = e.get("severity", "CLEAR")
            if not e.get("confirmed") or SEVERITY_RANK.get(sev, 0) == 0:
                continue
            key = f"egress:{e['room']}"
            seen.add(key)
            existing = self._open.get(key)
            if existing:
                existing.update(
                    severity=sev, detail=e.get("note") or existing["detail"],
                    fraction=e.get("fraction", 0), summary=e.get("summary", ""),
                    objects=e.get("objects", []), last_seen=now,
                    duration_s=round(now - existing["opened"], 1),
                    action=ACTIONS.get(sev, existing.get("action", "")))
            else:
                v = {
                    "id": f"CV-{int(now*1000)%1000000}",
                    "key": key, "type": "blocked_egress",
                    "severity": sev, "room": e["room"],
                    "room_name": e.get("room_name", e["room"]),
                    "floor": e.get("floor", 0),
                    "detail": e.get("note", "Escape route obstructed"),
                    "summary": e.get("summary", ""),
                    "objects": e.get("objects", []),
                    "fraction": e.get("fraction", 0),
                    "camera": e.get("camera", ""), "camera_name": e.get("camera_name", ""),
                    "opened": now, "last_seen": now, "duration_s": 0.0,
                    "action": ACTIONS.get(sev, ""),
                }
                self._open[key] = v
                opened.append(v)

        # anything not seen this pass has been cleared
        for key in [k for k in self._open if k not in seen]:
            v = self._open.pop(key)
            v["closed"] = now
            v["duration_s"] = round(now - v["opened"], 1)
            self._history.insert(0, v)
            self._history = self._history[:60]
            self._resolved_count += 1

        return opened

    # ------------------------------------------------------------------ views
    def violations(self) -> List[dict]:
        return sorted(self._open.values(),
                      key=lambda v: (-SEVERITY_RANK.get(v["severity"], 0),
                                     -v["duration_s"]))

    def blocked_rooms(self) -> Dict[str, str]:
        """Rooms whose escape route is compromised, for the evacuation router.

        A held-open fire door is a serious compliance failure but it does not
        make the room impassable - it makes smoke spread. Only a physical
        obstruction changes where people are routed.
        """
        return {v["room"]: v["severity"] for v in self._open.values()
                if v.get("type") not in ("fire_door", "asset")
                and SEVERITY_RANK.get(v["severity"], 0) >= 2}

    def open_fire_doors(self) -> List[str]:
        """Rooms with a fire door standing open - compartmentation is breached
        there, so smoke travels further than the walls suggest."""
        return [v["room"] for v in self._open.values()
                if v.get("type") == "fire_door"]

    def floor_scores(self, floors: List[dict]) -> List[dict]:
        by_floor: Dict[int, float] = {f["id"]: 100.0 for f in floors}
        counts: Dict[int, int] = {f["id"]: 0 for f in floors}
        for v in self._open.values():
            fid = v.get("floor", 0)
            if fid in by_floor:
                by_floor[fid] -= PENALTY.get(v["severity"], 5.0)
                counts[fid] += 1
        out = []
        for f in floors:
            score = max(0.0, round(by_floor[f["id"]], 1))
            out.append({"floor": f["id"], "name": f["name"],
                        "score": score, "violations": counts[f["id"]],
                        "band": ("COMPLIANT" if score >= 90 else
                                 "MINOR" if score >= 75 else
                                 "MAJOR" if score >= 50 else "CRITICAL")})
        return out

    def snapshot(self, floors: List[dict]) -> dict:
        vs = self.violations()
        floor_scores = self.floor_scores(floors)
        site_score = (round(sum(f["score"] for f in floor_scores) / len(floor_scores), 1)
                      if floor_scores else 100.0)
        return {
            "score": site_score,
            "band": ("COMPLIANT" if site_score >= 90 else
                     "MINOR" if site_score >= 75 else
                     "MAJOR" if site_score >= 50 else "CRITICAL"),
            "violations": vs,
            "open_count": len(vs),
            # only physical route blockages count here — a missing extinguisher
            # is severe but it does not block a corridor
            "blocked_routes": len([v for v in vs if v["severity"] == "BLOCKED"
                                   and v.get("type") == "blocked_egress"]),
            "open_doors": len([v for v in vs if v.get("type") == "fire_door"]),
            "asset_findings": len([v for v in vs if v.get("type") == "asset"]),
            "by_floor": floor_scores,
            "resolved_today": self._resolved_count,
            "history": self._history[:12],
        }
