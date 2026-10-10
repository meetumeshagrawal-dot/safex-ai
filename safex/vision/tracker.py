"""
tracker — person tracking and occupancy inference.

Two cooperating pieces:
  • CentroidTracker  : assigns stable IDs to person boxes across frames of one
                       camera (classic nearest-centroid association). Used with
                       real detections to produce steady counts + trajectories.
  • OccupancyTracker : fuses per-camera people counts into a per-zone occupancy
                       picture, and infers movement between adjacent zones — if
                       a person leaves camera A (zone A) and an adjacent zone B
                       gains one about the same time, that's counted as A→B.

Only counts/mappings live here; hazard consequences stay in the engine.
"""
from __future__ import annotations
import time
from collections import OrderedDict
from typing import Dict, List, Tuple

from ..sensors.plant_layout import ZONE_MAP


class CentroidTracker:
    def __init__(self, max_lost: int = 12, max_dist: float = 70.0):
        self.next_id = 0
        self.objects: "OrderedDict[int, Tuple[float,float]]" = OrderedDict()
        self.lost: Dict[int, int] = {}
        self.max_lost = max_lost
        self.max_dist = max_dist

    def update(self, boxes: List[Dict]) -> Dict[int, Tuple[float, float]]:
        pts = [(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
               for b in boxes if b.get("label") == "person"]
        if not pts:
            for oid in list(self.lost):
                self.lost[oid] += 1
                if self.lost[oid] > self.max_lost:
                    self.objects.pop(oid, None)
                    self.lost.pop(oid, None)
            return dict(self.objects)

        if not self.objects:
            for p in pts:
                self._register(p)
            return dict(self.objects)

        ids = list(self.objects.keys())
        used_ids, used_pts = set(), set()
        for oid in ids:
            ox, oy = self.objects[oid]
            best, bd = -1, self.max_dist
            for i, (px, py) in enumerate(pts):
                if i in used_pts:
                    continue
                d = ((ox - px) ** 2 + (oy - py) ** 2) ** 0.5
                if d < bd:
                    bd, best = d, i
            if best >= 0:
                self.objects[oid] = pts[best]
                self.lost[oid] = 0
                used_ids.add(oid); used_pts.add(best)
        for oid in ids:
            if oid not in used_ids:
                self.lost[oid] = self.lost.get(oid, 0) + 1
                if self.lost[oid] > self.max_lost:
                    self.objects.pop(oid, None); self.lost.pop(oid, None)
        for i, p in enumerate(pts):
            if i not in used_pts:
                self._register(p)
        return dict(self.objects)

    def _register(self, p):
        self.objects[self.next_id] = p
        self.lost[self.next_id] = 0
        self.next_id += 1

    def count(self) -> int:
        return len(self.objects)


class OccupancyTracker:
    """Per-zone occupancy from camera counts + adjacent-zone movement inference."""

    def __init__(self):
        self._zone_people: Dict[str, int] = {}
        self._prev: Dict[str, int] = {}
        self._movements: List[Dict] = []
        self._evacuated_total = 0

    def update(self, cam_zone_counts: Dict[str, int]) -> None:
        """cam_zone_counts: zone_id -> people currently seen by its camera."""
        now = time.strftime("%H:%M:%S")
        # movement inference: pair a drop in one zone with a rise in a neighbour
        drops = {z: self._prev.get(z, 0) - c for z, c in cam_zone_counts.items()
                 if self._prev.get(z, 0) - c > 0}
        rises = {z: c - self._prev.get(z, 0) for z, c in cam_zone_counts.items()
                 if c - self._prev.get(z, 0) > 0}
        for zf, d in drops.items():
            neigh = set(ZONE_MAP[zf].neighbors) if zf in ZONE_MAP else set()
            for zt, r in rises.items():
                if zt in neigh:
                    moved = min(d, r)
                    if moved > 0:
                        self._movements.insert(0, {
                            "from": zf, "to": zt, "n": moved, "time": now})
        self._movements = self._movements[:8]
        self._zone_people = dict(cam_zone_counts)
        self._prev = dict(cam_zone_counts)

    def zone_people(self) -> Dict[str, int]:
        return dict(self._zone_people)

    def recent_movements(self) -> List[Dict]:
        return list(self._movements)

    def totals(self, hazard_zones: set) -> Dict[str, int]:
        at_risk = sum(n for z, n in self._zone_people.items() if z in hazard_zones)
        monitored = sum(self._zone_people.values())
        return {"monitored": monitored, "at_risk": at_risk,
                "safe": monitored - at_risk}
