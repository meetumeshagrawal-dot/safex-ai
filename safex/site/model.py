"""The site document: floors, rooms, markers, cameras — and the geometry rules
that turn drawn rectangles into a walkable graph.

Rooms are rectangles in **normalised plan coordinates** (0-1 of the floor's
drawing area), so a floor-plan photo can sit behind them at any size and the
model stays resolution independent. Two rooms are connected when their
rectangles share a wall — which means the map an operator draws and the graph
the evacuation router walks can never disagree.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# How close two rectangles must be (in plan units) to count as sharing a wall,
# and how much of that wall they must actually share. The overlap rule is what
# stops rooms that merely touch at a corner from becoming a doorway.
TOUCH_TOLERANCE = 0.02
MIN_SHARED_WALL = 0.02

MARKER_TYPES = ("extinguisher", "exit_sign", "fire_door", "hydrant",
                "alarm_point", "route")


@dataclass
class Floor:
    id: int
    name: str
    short: str = ""
    subtitle: str = ""
    plan_image: str = ""          # optional uploaded floor-plan backdrop
    plan_opacity: float = 0.45
    order: int = 0                # display order, ground first
    width_m: float = 0.0          # how wide this plan really is, in metres

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "short": self.short,
                "subtitle": self.subtitle, "plan_image": self.plan_image,
                "plan_opacity": self.plan_opacity, "order": self.order,
                "width_m": self.width_m}


@dataclass
class Room:
    id: str
    name: str
    floor: int
    rect: List[float]                        # [x, y, w, h] normalised 0-1
    category: str = "Support"
    base_occupancy: int = 0
    hazards: List[str] = field(default_factory=list)
    critical: bool = False
    is_exit: bool = False
    vertical: bool = False                   # stair / lift core
    links: List[str] = field(default_factory=list)    # forced connections
    blocks: List[str] = field(default_factory=list)   # forced disconnections
    neighbors: List[str] = field(default_factory=list)  # computed

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "floor": self.floor,
                "rect": [round(v, 4) for v in self.rect],
                "category": self.category, "base_occupancy": self.base_occupancy,
                "hazards": list(self.hazards), "critical": self.critical,
                "is_exit": self.is_exit, "vertical": self.vertical,
                "links": list(self.links), "blocks": list(self.blocks)}

    @property
    def center(self) -> Tuple[float, float]:
        return (self.rect[0] + self.rect[2] / 2, self.rect[1] + self.rect[3] / 2)


@dataclass
class Marker:
    """A fire-safety asset placed on the plan.

    type: extinguisher | exit_sign | fire_door | hydrant | alarm_point | route
    A `route` marker carries a rectangle describing a corridor whose free width
    is monitored for obstructions.
    """
    id: str
    type: str
    floor: int
    rect: List[float]
    room: str = ""
    label: str = ""
    meta: Dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "floor": self.floor,
                "rect": [round(v, 4) for v in self.rect], "room": self.room,
                "label": self.label, "meta": dict(self.meta)}


@dataclass
class CameraDef:
    id: str
    name: str
    room: str
    source: str = "synthetic"
    enabled: bool = True
    # image <-> plan mapping, once someone has calibrated this camera
    calibration: Dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "room": self.room,
                "source": self.source, "enabled": self.enabled,
                "calibration": dict(self.calibration or {})}


# ---------------------------------------------------------------------------
# Geometry to graph
# ---------------------------------------------------------------------------
def _share_wall(a: Room, b: Room) -> bool:
    """True when two rectangles on the same floor share enough of a wall to be
    treated as connected by a doorway."""
    ax, ay, aw, ah = a.rect
    bx, by, bw, bh = b.rect
    ax2, ay2, bx2, by2 = ax + aw, ay + ah, bx + bw, by + bh

    # horizontal gap between them, and how much they overlap vertically
    gap_x = max(bx - ax2, ax - bx2)
    overlap_y = min(ay2, by2) - max(ay, by)
    if gap_x <= TOUCH_TOLERANCE and overlap_y >= MIN_SHARED_WALL:
        return True

    gap_y = max(by - ay2, ay - by2)
    overlap_x = min(ax2, bx2) - max(ax, bx)
    if gap_y <= TOUCH_TOLERANCE and overlap_x >= MIN_SHARED_WALL:
        return True
    return False


def adjacency_from_rects(rooms: List[Room]) -> None:
    """Recompute every room's neighbours in place, from the drawn geometry plus
    the explicit link/block overrides."""
    by_id = {r.id: r for r in rooms}
    for r in rooms:
        r.neighbors = []

    by_floor: Dict[int, List[Room]] = {}
    for r in rooms:
        by_floor.setdefault(r.floor, []).append(r)

    def connect(a: Room, b: Room):
        if b.id not in a.neighbors:
            a.neighbors.append(b.id)
        if a.id not in b.neighbors:
            b.neighbors.append(a.id)

    for floor_rooms in by_floor.values():
        for i, a in enumerate(floor_rooms):
            for b in floor_rooms[i + 1:]:
                if b.id in a.blocks or a.id in b.blocks:
                    continue
                if _share_wall(a, b):
                    connect(a, b)

    # explicit links (stair cores between floors, or a door the geometry missed)
    for r in rooms:
        for other_id in r.links:
            other = by_id.get(other_id)
            if other and other_id not in r.blocks and r.id not in other.blocks:
                connect(r, other)


@dataclass
class Site:
    name: str = "SAFEX Site"
    org: str = ""
    floors: List[Floor] = field(default_factory=list)
    rooms: List[Room] = field(default_factory=list)
    markers: List[Marker] = field(default_factory=list)
    cameras: List[CameraDef] = field(default_factory=list)
    preset: str = "custom"

    # ------------------------------------------------------------- accessors
    def floor(self, fid: int) -> Optional[Floor]:
        return next((f for f in self.floors if f.id == fid), None)

    def room(self, rid: str) -> Optional[Room]:
        return next((r for r in self.rooms if r.id == rid), None)

    def rooms_on(self, fid: int) -> List[Room]:
        return [r for r in self.rooms if r.floor == fid]

    def markers_on(self, fid: int) -> List[Marker]:
        return [m for m in self.markers if m.floor == fid]

    def exits(self) -> List[Room]:
        return [r for r in self.rooms if r.is_exit]

    def next_room_id(self, prefix: str = "R") -> str:
        taken = {r.id for r in self.rooms}
        n = 1
        while f"{prefix}{n}" in taken:
            n += 1
        return f"{prefix}{n}"

    def next_marker_id(self, kind: str) -> str:
        stem = {"extinguisher": "EX", "exit_sign": "ES", "fire_door": "FD",
                "hydrant": "HY", "alarm_point": "AP", "route": "RT"}.get(kind, "MK")
        taken = {m.id for m in self.markers}
        n = 1
        while f"{stem}-{n}" in taken:
            n += 1
        return f"{stem}-{n}"

    def next_floor_id(self) -> int:
        return (max((f.id for f in self.floors), default=-1)) + 1

    # ------------------------------------------------------------ maintenance
    def rebuild(self) -> None:
        self.floors.sort(key=lambda f: (f.order, f.id))
        adjacency_from_rects(self.rooms)

    def unreachable(self) -> List[str]:
        """Rooms with no path to any muster point — the one thing an operator
        must never be allowed to publish silently."""
        from collections import deque
        by_id = {r.id: r for r in self.rooms}
        seen = {r.id for r in self.rooms if r.is_exit}
        dq = deque(seen)
        while dq:
            cur = dq.popleft()
            for nb in by_id[cur].neighbors:
                if nb not in seen:
                    seen.add(nb)
                    dq.append(nb)
        return [r.id for r in self.rooms if not r.is_exit and r.id not in seen]

    def problems(self) -> List[dict]:
        """Everything wrong with the current draft, for the editor to show."""
        out: List[dict] = []
        if not self.floors:
            out.append({"level": "error", "msg": "No floors defined."})
        if not self.exits():
            out.append({"level": "error",
                        "msg": "No muster point anywhere — nobody can evacuate."})
        for rid in self.unreachable():
            r = self.room(rid)
            out.append({"level": "error", "room": rid,
                        "msg": f"{r.name if r else rid} has no route to a muster point."})
        for f in self.floors:
            if not self.rooms_on(f.id):
                out.append({"level": "warn", "floor": f.id,
                            "msg": f"{f.name} has no rooms yet."})
        seen_ids = set()
        for r in self.rooms:
            if r.id in seen_ids:
                out.append({"level": "error", "room": r.id,
                            "msg": f"Duplicate room id {r.id}."})
            seen_ids.add(r.id)
        return out

    # --------------------------------------------------------------- as JSON
    def as_dict(self) -> dict:
        return {
            "version": 1, "name": self.name, "org": self.org,
            "preset": self.preset,
            "floors": [f.as_dict() for f in self.floors],
            "rooms": [r.as_dict() for r in self.rooms],
            "markers": [m.as_dict() for m in self.markers],
            "cameras": [c.as_dict() for c in self.cameras],
        }

    @staticmethod
    def from_dict(d: dict) -> "Site":
        def keep(cls, raw):
            return {k: v for k, v in raw.items() if k in cls.__annotations__}
        site = Site(
            name=d.get("name", "SAFEX Site"), org=d.get("org", ""),
            preset=d.get("preset", "custom"),
            floors=[Floor(**keep(Floor, f)) for f in d.get("floors", [])],
            rooms=[Room(**keep(Room, r)) for r in d.get("rooms", [])],
            markers=[Marker(**keep(Marker, m)) for m in d.get("markers", [])],
            cameras=[CameraDef(**keep(CameraDef, c)) for c in d.get("cameras", [])],
        )
        site.rebuild()
        return site
