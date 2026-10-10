"""
Intelligent Evacuation Router.

Builds on the A* pathfinding already proven in the project's simulations, but
operates over the plant *zone graph* rather than a pixel grid. For every
occupied zone it computes the safest route to a muster point, where "safe"
means: shortest path whose cost is inflated by the live hazard score of each
zone it passes through. High-hazard zones become expensive, so routes bend
around a fire or gas cloud automatically — exactly the crowd/fire-aware
behaviour of the original `choose_best_exit`, generalised to a real floor plan.
"""
from __future__ import annotations
import heapq
from typing import Dict, List, Optional, Tuple

from ..sensors.plant_layout import EXITS, OPERATIONAL_ZONES, ZONE_MAP, get_zone

# A zone whose hazard exceeds this is treated as impassable (blocked route).
IMPASSABLE_HAZARD = 75.0

# Moving between floors costs more than walking a corridor — stairs are slow,
# and crowded stairs are slower still. This makes the router prefer to stay on
# a floor until it must descend, which is how real egress plans behave.
STAIR_COST = 2.4


def _edge_cost(hazard: float, frm: str, to: str) -> float:
    """Traversal cost of stepping from one room into the next."""
    if hazard >= IMPASSABLE_HAZARD:
        return float("inf")
    # 1 base step + non-linear penalty as hazard climbs
    cost = 1.0 + (hazard / 100.0) ** 1.5 * 12.0
    if ZONE_MAP[frm].floor != ZONE_MAP[to].floor:
        cost += STAIR_COST
    return cost


def route_from(zone_id: str, hazards: Dict[str, float]) -> Optional[dict]:
    """Hazard-aware A* from a zone to the safest reachable exit."""
    if zone_id in EXITS:
        return {"path": [zone_id], "exit": zone_id, "cost": 0.0, "blocked": False}

    open_set: List[Tuple[float, str]] = [(0.0, zone_id)]
    came: Dict[str, str] = {}
    g: Dict[str, float] = {zone_id: 0.0}
    best_exit = None

    while open_set:
        _, cur = heapq.heappop(open_set)
        if cur in EXITS:
            best_exit = cur
            break
        for nb in get_zone(cur).neighbors:
            step = _edge_cost(hazards.get(nb, 0.0), cur, nb)
            if step == float("inf"):
                continue
            ng = g[cur] + step
            if nb not in g or ng < g[nb]:
                g[nb] = ng
                came[nb] = cur
                # heuristic: 1 per hop (admissible, exits are few)
                heapq.heappush(open_set, (ng, nb))

    if best_exit is None:
        return {"path": [zone_id], "exit": None, "cost": float("inf"),
                "blocked": True, "path_floors": [ZONE_MAP[zone_id].floor],
                "floors_crossed": 0}

    path = [best_exit]
    cur = best_exit
    while cur in came:
        cur = came[cur]
        path.append(cur)
    path.reverse()
    exit_name = ZONE_MAP[best_exit].name if best_exit in ZONE_MAP else best_exit
    floors = [ZONE_MAP[p].floor for p in path]
    stairs = [p for p in path if ZONE_MAP[p].vertical]
    return {"path": path, "exit": best_exit, "exit_name": exit_name,
            "cost": round(g[best_exit], 2), "blocked": False,
            "path_floors": floors,
            "floors_crossed": len(set(floors)) - 1,
            "via_stairs": sorted(set(s.split("-")[0] for s in stairs))}


def plan_evacuation(hazards: Dict[str, float],
                    occupancy: Dict[str, int],
                    risk_threshold: float = 35.0) -> dict:
    """Route every *at-risk* occupied zone and summarise egress load.

    A zone is "at risk" only once its hazard crosses `risk_threshold`; below
    that, workers are considered safe in place and are not force-evacuated —
    which keeps the "people at risk" KPI meaningful during normal operation.
    """
    routes = {}
    exit_load: Dict[str, int] = {e: 0 for e in EXITS}
    floor_load: Dict[str, int] = {}
    people_at_risk = 0
    blocked_zones = []

    for z in OPERATIONAL_ZONES:
        occ = occupancy.get(z.id, 0)
        if occ <= 0 or hazards.get(z.id, 0.0) < risk_threshold:
            continue
        people_at_risk += occ
        r = route_from(z.id, hazards)
        routes[z.id] = {**r, "occupancy": occ, "floor": z.floor}
        floor_load[str(z.floor)] = floor_load.get(str(z.floor), 0) + occ
        if r["blocked"] or r["exit"] is None:
            blocked_zones.append(z.id)
        elif r["exit"]:
            exit_load[r["exit"]] += occ

    return {
        "routes": routes,
        "exit_load": exit_load,
        "floor_load": floor_load,
        "people_at_risk": people_at_risk,
        "blocked_zones": blocked_zones,
        "recommended_exit": (min(exit_load, key=exit_load.get)
                             if exit_load else None),
    }
