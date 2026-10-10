"""
zone_mapper — binds each camera to exactly one plant zone.

A camera's detections only ever affect its assigned zone; the hazard engine
then propagates consequences (neighbour smoke bleed, evacuation, incidents)
through the existing plant graph. Keeping this mapping in one place means the
camera layer never needs to know anything about plant physics.
"""
from __future__ import annotations
from typing import Dict, List

from ..sensors.plant_layout import ZONE_MAP


def zone_index():
    return ZONE_MAP


def zone_name(zone_id: str) -> str:
    z = zone_index().get(zone_id)
    return z.name if z else zone_id


def zone_exists(zone_id: str) -> bool:
    return zone_id in zone_index()


def operational_zone_ids() -> List[str]:
    return [zid for zid, z in zone_index().items() if not z.is_exit]


class ZoneMapper:
    """camera_id -> zone_id, with validation against the live plant layout.

    The mapping is many-to-one: several cameras may watch the same room, but a
    camera only ever belongs to one room.
    """

    def __init__(self, mapping: Dict[str, str] | None = None):
        self._map: Dict[str, str] = {}
        if mapping:
            for cam, zone in mapping.items():
                self.assign(cam, zone)

    def assign(self, camera_id: str, zone_id: str) -> bool:
        if not zone_exists(zone_id):
            return False
        self._map[camera_id] = zone_id
        return True

    def zone_for(self, camera_id: str) -> str | None:
        return self._map.get(camera_id)

    def camera_for(self, zone_id: str) -> str | None:
        """First camera watching a room (kept for callers that want just one)."""
        cams = self.cameras_for(zone_id)
        return cams[0] if cams else None

    def cameras_for(self, zone_id: str) -> List[str]:
        """Every camera watching a room.

        A room can carry several cameras — a ward with a camera at each end, a
        corridor covered from both directions — and all of them feed the same
        room's hazard picture.
        """
        return [cam for cam, zid in self._map.items() if zid == zone_id]

    def zone_camera_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for zid in self._map.values():
            out[zid] = out.get(zid, 0) + 1
        return out

    def as_dict(self) -> Dict[str, str]:
        return dict(self._map)

    def remove(self, camera_id: str) -> None:
        self._map.pop(camera_id, None)
