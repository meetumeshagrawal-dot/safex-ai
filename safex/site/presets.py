"""Ready-made sites.

`industrial` is the plant the platform shipped with — kept byte-for-byte
equivalent to the old hardcoded layout so nothing changes for anyone who never
opens the editor. `hospital` is the same building idea applied to a hospital,
which is the setting fire-safety compliance monitoring is usually deployed in.

Both are authored as floor grids because that is the fastest way to write a
building by hand. The grid is converted to normalised rectangles on load, after
which the editor treats them like any other drawn room.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from .model import CameraDef, Floor, Marker, Room, Site


def _rooms_from_grid(plan: List[List[Optional[str]]], meta: Dict[str, tuple],
                     floor_id: int, links: Dict[str, List[str]]) -> List[Room]:
    """Turn a floor grid into rectangles.

    Cells touch exactly, so two rooms in adjacent cells share a wall (a
    doorway) while diagonal cells only meet at a corner and stay unconnected —
    the same graph the hand-written adjacency used to describe.
    """
    rows = len(plan)
    cols = max(len(r) for r in plan)
    out: List[Room] = []
    for r, row in enumerate(plan):
        for c, rid in enumerate(row):
            if not rid:
                continue
            name, cat, occ, hazards, critical = meta[rid]
            out.append(Room(
                id=rid, name=name, floor=floor_id,
                rect=[c / cols, r / rows, 1 / cols, 1 / rows],
                category=cat, base_occupancy=occ, hazards=list(hazards),
                critical=critical,
                is_exit=cat == "Egress",
                vertical=cat == "Vertical",
                links=list(links.get(rid, [])),
            ))
    return out


# ===========================================================================
# INDUSTRIAL — the original plant
# ===========================================================================
_IND_ROOMS: Dict[str, tuple] = {
    "Z1":  ("Reactor Hall A",        "Process",     6, ["gas", "temperature", "fire"], True),
    "Z2":  ("Reactor Hall B",        "Process",     6, ["gas", "temperature", "fire"], True),
    "Z3":  ("Distillation Unit",     "Process",     4, ["gas", "temperature", "smoke", "fire"], True),
    "Z4":  ("Compressor Bay",        "Utilities",   3, ["vibration", "noise", "temperature"], False),
    "Z5":  ("Electrical Substation", "Utilities",   2, ["fire", "temperature"], True),
    "Z6":  ("Solvent Storage",       "Storage",     2, ["gas", "smoke", "fire"], True),
    "Z7":  ("Chemical Warehouse",    "Storage",     4, ["gas", "smoke", "fire"], False),
    "Z8":  ("Central Corridor",      "Circulation", 2, ["smoke"], False),
    "Z9":  ("Boiler Room",           "Utilities",   3, ["temperature", "smoke", "fire", "vibration"], True),
    "Z10": ("Pump House",            "Utilities",   2, ["vibration", "noise"], False),
    "Z11": ("Raw Material Intake",   "Logistics",   5, ["gas", "vibration"], False),
    "Z12": ("South Corridor",        "Circulation", 1, ["smoke"], False),
    "Z13": ("Fire Pump House",       "Safety",      1, ["vibration"], True),
    "Z14": ("Gas Cylinder Yard",     "Storage",     2, ["gas", "fire"], True),
    "Z15": ("Effluent Treatment",    "Support",     3, ["gas", "smoke"], False),
    "Z16": ("Ground Workshop",       "Support",     4, ["fire", "vibration", "noise"], False),
    "Z17": ("Security Gatehouse",    "Command",     2, ["fire"], False),

    "L1":  ("Packaging Line",        "Logistics",   8, ["temperature", "vibration"], False),
    "L2":  ("Bottling Hall",         "Logistics",   6, ["vibration", "noise"], False),
    "L3":  ("Quality Lab",           "Support",     3, ["gas", "smoke", "fire"], True),
    "L4":  ("Sample Store",          "Storage",     1, ["gas", "smoke"], False),
    "L5":  ("North Mezzanine",       "Circulation", 1, ["smoke"], False),
    "L6":  ("Loading Bay West",      "Logistics",   5, ["vibration", "noise"], False),
    "L7":  ("Finished Goods Store",  "Storage",     3, ["fire", "smoke"], False),
    "L8":  ("Level 1 Corridor",      "Circulation", 2, ["smoke"], False),
    "L9":  ("Maintenance Shop",      "Support",     4, ["fire", "vibration", "noise"], False),
    "L10": ("Compressor Mezzanine",  "Utilities",   2, ["vibration", "temperature"], False),
    "L11": ("Paint & Solvent Booth", "Process",     3, ["gas", "smoke", "fire"], True),
    "L12": ("Tool Crib",             "Support",     2, ["fire"], False),
    "L13": ("Battery / UPS Room",    "Utilities",   1, ["fire", "temperature", "gas"], True),

    "A1":  ("Control Room",          "Command",     5, ["fire"], True),
    "A2":  ("Server / IT Room",      "Command",     2, ["fire", "temperature"], True),
    "A3":  ("Admin Offices",         "Support",    12, ["fire"], False),
    "A4":  ("Meeting Suite",         "Support",     6, ["fire"], False),
    "A5":  ("Upper Corridor",        "Circulation", 2, ["smoke"], False),
    "A6":  ("Canteen",               "Support",    20, ["fire", "smoke"], False),
    "A7":  ("Kitchen",               "Support",     4, ["fire", "smoke", "gas"], True),
    "A8":  ("Level 2 Corridor",      "Circulation", 2, ["smoke"], False),
    "A9":  ("Training Room",         "Support",     8, ["fire"], False),
    "A10": ("HSE Office",            "Command",     3, ["fire"], False),
    "A11": ("Archive Store",         "Storage",     1, ["fire", "smoke"], False),
    "A12": ("Plant Room L2",         "Utilities",   1, ["temperature", "vibration"], False),
    "A13": ("Locker Rooms",          "Support",     6, ["fire"], False),

    "SA-G": ("Stair Core A · Ground", "Vertical", 1, ["smoke"], False),
    "SA-1": ("Stair Core A · L1",     "Vertical", 1, ["smoke"], False),
    "SA-2": ("Stair Core A · L2",     "Vertical", 1, ["smoke"], False),
    "SB-G": ("Stair Core B · Ground", "Vertical", 1, ["smoke"], False),
    "SB-1": ("Stair Core B · L1",     "Vertical", 1, ["smoke"], False),
    "SB-2": ("Stair Core B · L2",     "Vertical", 1, ["smoke"], False),

    "EXIT_N": ("North Muster Point", "Egress", 0, [], False),
    "EXIT_E": ("East Muster Point",  "Egress", 0, [], False),
    "EXIT_S": ("South Muster Point", "Egress", 0, [], False),
    "EXIT_W": ("West Muster Point",  "Egress", 0, [], False),
}

_IND_PLANS = {
    0: [
        ["Z1",     "Z2",  "Z3",  "Z4",  "Z5",   "EXIT_N"],
        ["Z6",     "Z7",  "Z8",  "Z9",  "SA-G", "EXIT_E"],
        ["Z10",    "Z11", "Z12", "Z13", "SB-G", "EXIT_S"],
        ["EXIT_W", "Z14", "Z15", "Z16", "Z17",  None],
    ],
    1: [
        ["L1",  "L2",  "L3",  "L4",  "L5"],
        ["L6",  "L7",  "L8",  "L9",  "SA-1"],
        ["L10", "L11", "L12", "L13", "SB-1"],
    ],
    2: [
        ["A1",  "A2",  "A3",  "A4",  "A5"],
        ["A6",  "A7",  "A8",  "A9",  "SA-2"],
        ["A10", "A11", "A12", "A13", "SB-2"],
    ],
}

_IND_FLOORS = [
    Floor(0, "Ground Floor", "G", "Process · Utilities · Egress", order=0),
    Floor(1, "Level 1", "L1", "Logistics · Labs · Maintenance", order=1),
    Floor(2, "Level 2", "L2", "Control · Admin · Welfare", order=2),
]

_STAIR_LINKS = {"SA-G": ["SA-1"], "SA-1": ["SA-2"], "SB-G": ["SB-1"], "SB-1": ["SB-2"]}

_IND_CAMERAS = [
    CameraDef("CAM-01", "Reactor Hall A", "Z1"),
    CameraDef("CAM-02", "Chemical Warehouse", "Z7"),
    CameraDef("CAM-03", "Boiler Room", "Z9"),
    CameraDef("CAM-04", "Packaging Line", "L1"),
    CameraDef("CAM-05", "Paint & Solvent Booth", "L11"),
    CameraDef("CAM-06", "Canteen", "A6"),
]


# ===========================================================================
# HOSPITAL — the setting the compliance brief targets
# ===========================================================================
_HOSP_ROOMS: Dict[str, tuple] = {
    # ---- ground: emergency and diagnostics ----
    "G1":  ("Ambulance Bay",       "Access",      3, ["fire"], False),
    "G2":  ("Emergency / Casualty", "Clinical",   14, ["fire", "gas"], True),
    "G3":  ("Triage",              "Clinical",    6, ["fire"], False),
    "G4":  ("Reception & Waiting", "Public",     22, ["fire"], False),
    "G5":  ("Pharmacy Store",      "Storage",     3, ["fire", "smoke"], True),
    "G6":  ("Radiology / CT",      "Clinical",    5, ["fire", "temperature"], True),
    "G7":  ("Pathology Lab",       "Clinical",    4, ["gas", "smoke", "fire"], True),
    "G8":  ("Main Corridor",       "Circulation", 6, ["smoke"], False),
    "G9":  ("Oxygen Manifold Room", "Utilities",  1, ["gas", "fire"], True),
    "G10": ("Generator Room",      "Utilities",   1, ["fire", "temperature", "noise"], True),
    "G11": ("Medical Records",     "Storage",     2, ["fire", "smoke"], False),
    "G12": ("Security Desk",       "Command",     2, ["fire"], False),

    # ---- level 1: theatres and critical care ----
    "H1":  ("Operating Theatre 1", "Clinical",    6, ["fire", "gas", "temperature"], True),
    "H2":  ("Operating Theatre 2", "Clinical",    6, ["fire", "gas", "temperature"], True),
    "H3":  ("Recovery Bay",        "Clinical",    8, ["fire", "gas"], True),
    "H4":  ("Scrub & Prep",        "Clinical",    3, ["fire"], False),
    "H5":  ("Theatre Corridor",    "Circulation", 3, ["smoke"], False),
    "H6":  ("ICU",                 "Clinical",   12, ["fire", "gas", "temperature"], True),
    "H7":  ("HDU",                 "Clinical",    8, ["fire", "gas"], True),
    "H8":  ("Nurse Station L1",    "Clinical",    4, ["fire"], False),
    "H9":  ("CSSD / Sterilisation", "Support",    3, ["temperature", "smoke", "fire"], True),
    "H10": ("Blood Bank",          "Storage",     2, ["fire", "temperature"], True),
    "H11": ("Clean Utility",       "Storage",     1, ["fire"], False),
    "H12": ("Plant Room L1",       "Utilities",   1, ["temperature", "vibration"], False),

    # ---- level 2: wards and welfare ----
    "W1":  ("General Ward A",      "Clinical",   18, ["fire", "smoke"], False),
    "W2":  ("General Ward B",      "Clinical",   18, ["fire", "smoke"], False),
    "W3":  ("Paediatric Ward",     "Clinical",   12, ["fire", "smoke"], True),
    "W4":  ("Nursery / NICU",      "Clinical",    6, ["fire", "gas", "temperature"], True),
    "W5":  ("Ward Corridor",       "Circulation", 4, ["smoke"], False),
    "W6":  ("Kitchen & Pantry",    "Support",     5, ["fire", "smoke", "gas"], True),
    "W7":  ("Canteen",             "Public",     20, ["fire", "smoke"], False),
    "W8":  ("Nurse Station L2",    "Clinical",    4, ["fire"], False),
    "W9":  ("Doctors' Duty Room",  "Support",     6, ["fire"], False),
    "W10": ("Linen Store",         "Storage",     1, ["fire", "smoke"], True),
    "W11": ("Administration",      "Command",     8, ["fire"], False),
    "W12": ("Electrical Riser L2", "Utilities",   1, ["fire", "temperature"], True),

    "ST-A-G": ("Stair A · Ground", "Vertical", 1, ["smoke"], False),
    "ST-A-1": ("Stair A · L1",     "Vertical", 1, ["smoke"], False),
    "ST-A-2": ("Stair A · L2",     "Vertical", 1, ["smoke"], False),
    "ST-B-G": ("Stair B · Ground", "Vertical", 1, ["smoke"], False),
    "ST-B-1": ("Stair B · L1",     "Vertical", 1, ["smoke"], False),
    "ST-B-2": ("Stair B · L2",     "Vertical", 1, ["smoke"], False),

    "MUSTER_N": ("North Assembly Point", "Egress", 0, [], False),
    "MUSTER_E": ("East Assembly Point",  "Egress", 0, [], False),
    "MUSTER_S": ("South Assembly Point", "Egress", 0, [], False),
}

_HOSP_PLANS = {
    0: [
        ["G1", "G2", "G3", "G4",  "G12",    "MUSTER_N"],
        ["G5", "G6", "G7", "G8",  "ST-A-G", "MUSTER_E"],
        ["G9", "G10", "G11", None, "ST-B-G", "MUSTER_S"],
    ],
    1: [
        ["H1", "H2",  "H3",  "H4",  "H5"],
        ["H6", "H7",  "H8",  "H9",  "ST-A-1"],
        ["H10", "H11", "H12", None, "ST-B-1"],
    ],
    2: [
        ["W1",  "W2",  "W3",  "W4",  "W5"],
        ["W6",  "W7",  "W8",  "W9",  "ST-A-2"],
        ["W10", "W11", "W12", None,  "ST-B-2"],
    ],
}

_HOSP_FLOORS = [
    Floor(0, "Ground Floor", "G", "Emergency · Diagnostics · Egress", order=0),
    Floor(1, "Level 1", "L1", "Theatres · Critical Care", order=1),
    Floor(2, "Level 2", "L2", "Wards · Welfare · Admin", order=2),
]

_HOSP_STAIR_LINKS = {"ST-A-G": ["ST-A-1"], "ST-A-1": ["ST-A-2"],
                     "ST-B-G": ["ST-B-1"], "ST-B-1": ["ST-B-2"]}

_HOSP_CAMERAS = [
    CameraDef("CAM-01", "Emergency Dept", "G2"),
    CameraDef("CAM-02", "Main Corridor", "G8"),
    CameraDef("CAM-03", "Oxygen Manifold", "G9"),
    CameraDef("CAM-04", "ICU", "H6"),
    CameraDef("CAM-05", "Theatre Corridor", "H5"),
    CameraDef("CAM-06", "Ward Corridor", "W5"),
    CameraDef("CAM-07", "Kitchen", "W6"),
]

# Fire-safety assets, so a hospital site arrives with something to audit.
_HOSP_MARKERS = [
    Marker("EX-1", "extinguisher", 0, [0.52, 0.38, 0.03, 0.05], room="G8", label="CO2 · corridor"),
    Marker("EX-2", "extinguisher", 0, [0.02, 0.70, 0.03, 0.05], room="G9", label="DCP · manifold"),
    Marker("EX-3", "extinguisher", 1, [0.62, 0.38, 0.03, 0.05], room="H5", label="CO2 · theatre"),
    Marker("EX-4", "extinguisher", 2, [0.62, 0.05, 0.03, 0.05], room="W5", label="ABC · ward"),
    Marker("EX-5", "extinguisher", 2, [0.02, 0.38, 0.03, 0.05], room="W6", label="K-class · kitchen"),
    Marker("ES-1", "exit_sign", 0, [0.83, 0.05, 0.035, 0.04], room="MUSTER_N"),
    Marker("ES-2", "exit_sign", 1, [0.80, 0.38, 0.035, 0.04], room="ST-A-1"),
    Marker("ES-3", "exit_sign", 2, [0.80, 0.38, 0.035, 0.04], room="ST-A-2"),
    # Fire doors sit where the camera watching that corridor can actually see
    # them — a door nobody has a view of cannot be monitored, and pretending
    # otherwise would leave a silent gap in the audit.
    Marker("FD-1", "fire_door", 1, [0.74, 0.10, 0.05, 0.16], room="H5",
           label="Theatre lobby door",
           meta={"roi": [0.62, 0.10, 0.34, 0.80], "held_open_seconds": 30}),
    Marker("FD-2", "fire_door", 2, [0.74, 0.10, 0.05, 0.16], room="W5",
           label="Ward lobby door",
           meta={"roi": [0.62, 0.10, 0.34, 0.80], "held_open_seconds": 30}),
    Marker("FD-3", "fire_door", 0, [0.60, 0.36, 0.05, 0.16], room="G8",
           label="Main corridor cross door",
           meta={"roi": [0.62, 0.10, 0.34, 0.80], "held_open_seconds": 45}),
    Marker("RT-1", "route", 0, [0.50, 0.34, 0.17, 0.32], room="G8", label="Main egress corridor"),
    Marker("RT-2", "route", 1, [0.60, 0.01, 0.20, 0.32], room="H5", label="Theatre egress corridor"),
    Marker("RT-3", "route", 2, [0.60, 0.01, 0.20, 0.32], room="W5", label="Ward egress corridor"),
]


def _build(name: str, org: str, preset: str, floors, plans, meta, links,
           cameras, markers=None) -> Site:
    rooms: List[Room] = []
    for floor in floors:
        rooms += _rooms_from_grid(plans[floor.id], meta, floor.id, links)
    site = Site(name=name, org=org, preset=preset,
                floors=[Floor(**f.as_dict()) for f in floors],
                rooms=rooms,
                markers=[Marker(**m.as_dict()) for m in (markers or [])],
                cameras=[CameraDef(**c.as_dict()) for c in cameras])
    site.rebuild()
    return site


def industrial() -> Site:
    return _build("Meridian Plant 4", "Meridian Heavy Industries — Plant 4",
                  "industrial", _IND_FLOORS, _IND_PLANS, _IND_ROOMS,
                  _STAIR_LINKS, _IND_CAMERAS)


def hospital() -> Site:
    return _build("St. Marina General Hospital",
                  "St. Marina General Hospital — Main Block",
                  "hospital", _HOSP_FLOORS, _HOSP_PLANS, _HOSP_ROOMS,
                  _HOSP_STAIR_LINKS, _HOSP_CAMERAS, _HOSP_MARKERS)


def blank() -> Site:
    """An empty site with one floor — the starting point when someone wants to
    draw their own building over an uploaded plan."""
    site = Site(name="New Site", org="", preset="custom",
                floors=[Floor(0, "Ground Floor", "G", "Draw your rooms", order=0)])
    site.rebuild()
    return site


PRESETS = {"industrial": industrial, "hospital": hospital, "blank": blank}
