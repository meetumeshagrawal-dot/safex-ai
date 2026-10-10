"""Editable site model — floors, rooms, markers and cameras as data.

The plant used to be hardcoded in `plant_layout.py`. It now lives in a JSON
document the operator can edit from the dashboard: add or delete floors, draw
rooms over an uploaded floor-plan image, place fire-safety markers, and map
cameras to rooms. `plant_layout` became a thin adapter over this, so every
existing consumer (hazard engine, evacuation router, camera manager, Copilot)
keeps working unchanged.
"""
from .model import Floor, Marker, Room, Site, adjacency_from_rects
from .store import SiteStore, site_store

__all__ = ["Floor", "Room", "Marker", "Site", "adjacency_from_rects",
           "SiteStore", "site_store"]
