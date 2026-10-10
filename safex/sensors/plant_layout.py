"""Digital twin of the site — an adapter over the editable site document.

The building used to be hardcoded here. It now comes from `safex.site`, which
an operator edits from the dashboard: add or delete floors, draw rooms over an
uploaded floor-plan image, place fire-safety markers, and map cameras to rooms.

Everything else in the platform still imports `ZONES`, `ZONE_MAP`, `EXITS`,
`OPERATIONAL_ZONES` and friends from this module, so those objects are
**mutated in place** when the site changes rather than rebound. A module that
did `from .plant_layout import ZONE_MAP` at import time therefore keeps seeing
the current building, which is what makes live editing safe.

Rooms are rectangles in normalised plan coordinates; two rooms are connected
when they share a wall. Muster points are the only exits, and stair cores are
the only way between floors — so the evacuation router naturally walks people
down a stairwell and out at ground level.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..site import site_store


@dataclass
class Zone:
    id: str
    name: str
    category: str
    grid: Tuple[int, int]          # legacy row/col hint (derived from the rect)
    base_occupancy: int
    hazards: List[str]
    floor: int = 0
    is_exit: bool = False
    critical_asset: bool = False
    vertical: bool = False
    rect: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.1, 0.1])
    neighbors: List[str] = field(default_factory=list)


# These containers are the platform's shared view of the building. They are
# never rebound — only mutated — see the module docstring.
ZONES: List[Zone] = []
ZONE_MAP: Dict[str, Zone] = {}
EXITS: List[str] = []
OPERATIONAL_ZONES: List[Zone] = []
STAIRS: List[str] = []
FLOORS: List = []

FLOOR_ROWS, FLOOR_COLS = 4, 6      # legacy grid hints for anything still asking


def _zone_from_room(room) -> Zone:
    # a coarse row/col, purely so legacy callers that expect a grid still work
    col = int(room.rect[0] * FLOOR_COLS)
    row = int(room.rect[1] * FLOOR_ROWS)
    return Zone(
        id=room.id, name=room.name, category=room.category,
        grid=(row, col), base_occupancy=room.base_occupancy,
        hazards=list(room.hazards), floor=room.floor,
        is_exit=room.is_exit, critical_asset=room.critical,
        vertical=room.vertical, rect=list(room.rect),
        neighbors=list(room.neighbors),
    )


def rebuild(site=None) -> None:
    """Re-derive every shared container from the current site document."""
    site = site or site_store.site
    zones = [_zone_from_room(r) for r in site.rooms]

    ZONES[:] = zones
    ZONE_MAP.clear()
    ZONE_MAP.update({z.id: z for z in zones})
    EXITS[:] = [z.id for z in zones if z.is_exit]
    OPERATIONAL_ZONES[:] = [z for z in zones if not z.is_exit]
    STAIRS[:] = [z.id for z in zones if z.vertical]
    FLOORS[:] = list(site.floors)

    # drop any dangling neighbour a hand-edited file might carry
    for z in ZONES:
        z.neighbors = [n for n in z.neighbors if n in ZONE_MAP and n != z.id]


rebuild()
site_store.on_change(rebuild)


# ---------------------------------------------------------------------------
# Helpers used across the platform
# ---------------------------------------------------------------------------
def get_zone(zone_id: str) -> Zone:
    return ZONE_MAP[zone_id]


def zone_floor(zone_id: str) -> int:
    z = ZONE_MAP.get(zone_id)
    return z.floor if z else 0


def floor_meta() -> List[dict]:
    return [{"id": f.id, "name": f.name, "subtitle": f.subtitle,
             "short": f.short or f.name[:2].upper(),
             "plan_image": f.plan_image, "plan_opacity": f.plan_opacity,
             "rows": FLOOR_ROWS, "cols": FLOOR_COLS} for f in FLOORS]


def zones_on(floor_id: int) -> List[Zone]:
    return [z for z in ZONES if z.floor == floor_id]


def total_base_occupancy() -> int:
    return sum(z.base_occupancy for z in ZONES)


def first_operational_zone() -> str:
    return OPERATIONAL_ZONES[0].id if OPERATIONAL_ZONES else ""
