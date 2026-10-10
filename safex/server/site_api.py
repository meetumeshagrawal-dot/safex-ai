"""Site editor API — the building is data an operator can change at runtime.

Every write goes through `site_store`, which validates, backs up, saves
atomically and then tells the running engine to refit itself. So a room drawn
here is monitored a few hundred milliseconds later, without a restart.

Floor-plan images arrive as base64 in the JSON body rather than as multipart
uploads: it keeps the platform dependency-free (no python-multipart) and a
floor plan is a one-off upload of a couple of megabytes, not a hot path.
"""
from __future__ import annotations
import base64
import binascii
import re
from pathlib import Path
from typing import List

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..site import site_store
from ..site.model import Floor, Marker, MARKER_TYPES, Room
from ..site.store import PLANS_DIR

router = APIRouter(prefix="/api/site", tags=["site"])

MAX_PLAN_BYTES = 8 * 1024 * 1024
_IMAGE_TYPES = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "webp": "webp"}


def _ok(extra: dict = None) -> dict:
    site = site_store.site
    out = {"ok": True, "rev": site_store.rev, "problems": site.problems()}
    out.update(extra or {})
    return out


def _fail(msg: str, code: int = 400):
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def _clamp_rect(rect) -> List[float]:
    """Keep a drawn rectangle inside the plan and big enough to click."""
    try:
        x, y, w, h = [float(v) for v in rect]
    except Exception:
        raise ValueError("rect must be [x, y, w, h]")
    w = max(0.02, min(1.0, w))
    h = max(0.02, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return [round(v, 4) for v in (x, y, w, h)]


# ---------------------------------------------------------------- read / presets
@router.get("")
async def get_site():
    site = site_store.site
    d = site.as_dict()
    d["rooms"] = [{**r.as_dict(), "neighbors": list(r.neighbors)} for r in site.rooms]
    d["rev"] = site_store.rev
    d["problems"] = site.problems()
    d["marker_types"] = list(MARKER_TYPES)
    return d


@router.post("/preset")
async def apply_preset(body: dict):
    name = (body or {}).get("name", "")
    res = site_store.apply_preset(name)
    if not res.get("ok"):
        return _fail(res.get("error", "unknown preset"))
    return _ok({"preset": name, "site": site_store.site.name})


@router.post("/meta")
async def set_meta(body: dict):
    def edit(site):
        if body.get("name"):
            site.name = str(body["name"])[:80]
        if body.get("org") is not None:
            site.org = str(body["org"])[:120]
    site_store.mutate(edit)
    return _ok()


# ------------------------------------------------------------------------ floors
@router.post("/floor")
async def add_floor(body: dict):
    def edit(site):
        fid = site.next_floor_id()
        site.floors.append(Floor(
            id=fid,
            name=body.get("name") or f"Level {fid}",
            short=body.get("short") or f"L{fid}",
            subtitle=body.get("subtitle", ""),
            order=int(body.get("order", fid)),
        ))
    site_store.mutate(edit)
    return _ok({"floor": site_store.site.floors[-1].as_dict()})


@router.patch("/floor/{fid}")
async def update_floor(fid: int, body: dict):
    site = site_store.site
    if not site.floor(fid):
        return _fail(f"no floor {fid}", 404)

    def edit(s):
        f = s.floor(fid)
        for key in ("name", "short", "subtitle"):
            if body.get(key) is not None:
                setattr(f, key, str(body[key])[:60])
        if body.get("plan_opacity") is not None:
            f.plan_opacity = max(0.0, min(1.0, float(body["plan_opacity"])))
        if body.get("width_m") is not None:
            # how wide the plan is in reality. Nothing can infer this from an
            # image, so it is asked for — and everything expressed in metres
            # downstream depends on it being right.
            f.width_m = max(0.0, min(1000.0, float(body["width_m"])))
        if body.get("order") is not None:
            f.order = int(body["order"])
    site_store.mutate(edit)
    return _ok()


@router.delete("/floor/{fid}")
async def delete_floor(fid: int, force: bool = False):
    site = site_store.site
    if not site.floor(fid):
        return _fail(f"no floor {fid}", 404)
    if len(site.floors) <= 1:
        return _fail("a site needs at least one floor")
    # Deleting the floor that holds every muster point strands the whole
    # building. It is a legitimate step while restructuring a site, so it is
    # allowed — but only when the operator says so explicitly.
    exits_here = [r for r in site.exits() if r.floor == fid]
    if exits_here and len(exits_here) == len(site.exits()) and not force:
        return _fail(
            f"{site.floor(fid).name} holds every muster point — deleting it "
            f"leaves no way out of the building. Add an exit on another floor "
            f"first, or repeat with force=true.")

    def edit(s):
        s.floors = [f for f in s.floors if f.id != fid]
        gone = {r.id for r in s.rooms if r.floor == fid}
        s.rooms = [r for r in s.rooms if r.floor != fid]
        s.markers = [m for m in s.markers if m.floor != fid]
        s.cameras = [c for c in s.cameras if c.room not in gone]
        # a stair core that used to reach this floor must forget it
        for r in s.rooms:
            r.links = [l for l in r.links if l not in gone]
            r.blocks = [b for b in r.blocks if b not in gone]
    site_store.mutate(edit)
    return _ok({"deleted_floor": fid})


# ------------------------------------------------------------- floor plan image
@router.post("/floor/{fid}/plan")
async def upload_plan(fid: int, body: dict):
    """Store a floor-plan image and hang it behind that floor.

    Body: {"data": "<base64, optionally a data: URL>", "filename": "plan.png"}
    """
    site = site_store.site
    if not site.floor(fid):
        return _fail(f"no floor {fid}", 404)
    raw = (body or {}).get("data") or ""
    ext = "png"
    m = re.match(r"^data:image/([a-zA-Z]+);base64,(.*)$", raw, re.DOTALL)
    if m:
        ext = _IMAGE_TYPES.get(m.group(1).lower(), "png")
        raw = m.group(2)
    elif (body or {}).get("filename"):
        ext = _IMAGE_TYPES.get(
            Path(body["filename"]).suffix.lstrip(".").lower(), "png")
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return _fail("plan image is not valid base64")
    if not blob:
        return _fail("empty image")
    if len(blob) > MAX_PLAN_BYTES:
        return _fail(f"floor plan is larger than {MAX_PLAN_BYTES // (1024*1024)} MB")

    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    # one file per floor, versioned so the browser can't serve a stale plan
    for old in PLANS_DIR.glob(f"floor-{fid}-*"):
        old.unlink(missing_ok=True)
    name = f"floor-{fid}-{site_store.rev + 1}.{ext}"
    (PLANS_DIR / name).write_bytes(blob)

    def edit(s):
        s.floor(fid).plan_image = f"/assets/plans/{name}"
    site_store.mutate(edit)
    return _ok({"plan_image": f"/assets/plans/{name}", "bytes": len(blob)})


@router.delete("/floor/{fid}/plan")
async def delete_plan(fid: int):
    if not site_store.site.floor(fid):
        return _fail(f"no floor {fid}", 404)
    for old in PLANS_DIR.glob(f"floor-{fid}-*"):
        old.unlink(missing_ok=True)

    def edit(s):
        s.floor(fid).plan_image = ""
    site_store.mutate(edit)
    return _ok()


@router.post("/floor/{fid}/autodetect")
async def autodetect_rooms(fid: int, body: dict = None):
    """Suggest room rectangles from the uploaded floor plan.

    This is an assist, not a promise: it finds closed rectangular regions in
    the drawing and proposes them. The operator accepts, renames or redraws —
    which is far quicker than drawing 40 rooms from scratch, and honest about
    the fact that no vectoriser reads every plan correctly.
    """
    from ..site.planvision import detect_room_rects
    site = site_store.site
    floor = site.floor(fid)
    if not floor:
        return _fail(f"no floor {fid}", 404)
    if not floor.plan_image:
        return _fail("upload a floor plan image first")
    path = PLANS_DIR / Path(floor.plan_image).name
    if not path.exists():
        return _fail("floor plan file is missing")
    try:
        rects = detect_room_rects(str(path), **(body or {}))
    except Exception as e:
        return _fail(f"could not read the plan: {str(e)[:120]}")
    return {"ok": True, "count": len(rects), "rects": rects}


# ------------------------------------------------------------------------- rooms
@router.post("/room")
async def add_room(body: dict):
    site = site_store.site
    fid = int(body.get("floor", 0))
    if not site.floor(fid):
        return _fail(f"no floor {fid}", 404)
    try:
        rect = _clamp_rect(body.get("rect", [0.1, 0.1, 0.15, 0.2]))
    except ValueError as e:
        return _fail(str(e))
    rid = (body.get("id") or "").strip() or site.next_room_id(
        body.get("prefix") or "R")
    if site.room(rid):
        return _fail(f"room {rid} already exists")

    def edit(s):
        s.rooms.append(Room(
            id=rid, name=body.get("name") or rid, floor=fid, rect=rect,
            category=body.get("category", "Support"),
            base_occupancy=int(body.get("base_occupancy", 0)),
            hazards=list(body.get("hazards", [])),
            critical=bool(body.get("critical", False)),
            is_exit=bool(body.get("is_exit", False)),
            vertical=bool(body.get("vertical", False)),
            links=list(body.get("links", [])),
        ))
    site_store.mutate(edit)
    room = site_store.site.room(rid)
    return _ok({"room": {**room.as_dict(), "neighbors": room.neighbors}})


@router.patch("/room/{rid}")
async def update_room(rid: str, body: dict):
    if not site_store.site.room(rid):
        return _fail(f"no room {rid}", 404)

    def edit(s):
        r = s.room(rid)
        if body.get("rect") is not None:
            r.rect = _clamp_rect(body["rect"])
        if body.get("name"):
            r.name = str(body["name"])[:60]
        if body.get("category"):
            r.category = str(body["category"])[:30]
        if body.get("floor") is not None and s.floor(int(body["floor"])):
            r.floor = int(body["floor"])
        if body.get("base_occupancy") is not None:
            r.base_occupancy = max(0, int(body["base_occupancy"]))
        if body.get("hazards") is not None:
            r.hazards = [str(h) for h in body["hazards"]][:8]
        for flag in ("critical", "is_exit", "vertical"):
            if body.get(flag) is not None:
                setattr(r, flag, bool(body[flag]))
        if body.get("links") is not None:
            r.links = [str(x) for x in body["links"] if x != rid]
        if body.get("blocks") is not None:
            r.blocks = [str(x) for x in body["blocks"] if x != rid]
    try:
        site_store.mutate(edit)
    except ValueError as e:
        return _fail(str(e))
    room = site_store.site.room(rid)
    return _ok({"room": {**room.as_dict(), "neighbors": room.neighbors}})


@router.delete("/room/{rid}")
async def delete_room(rid: str, force: bool = False):
    site = site_store.site
    room = site.room(rid)
    if not room:
        return _fail(f"no room {rid}", 404)
    if room.is_exit and len(site.exits()) == 1 and not force:
        return _fail("that is the only muster point on the site — nobody could "
                     "evacuate. Add another exit first, or repeat with force=true.")

    def edit(s):
        s.rooms = [r for r in s.rooms if r.id != rid]
        s.markers = [m for m in s.markers if m.room != rid]
        s.cameras = [c for c in s.cameras if c.room != rid]
        for r in s.rooms:
            r.links = [l for l in r.links if l != rid]
            r.blocks = [b for b in r.blocks if b != rid]
    site_store.mutate(edit)
    return _ok({"deleted_room": rid})


@router.post("/room/{rid}/link")
async def toggle_link(rid: str, body: dict):
    """Force a doorway between two rooms, or wall one off.

    Geometry gets adjacency right nearly all the time, but a plan sometimes has
    a door where two rooms only touch at a corner, or a solid wall where two
    rooms sit flush. Both need an override the operator controls.
    """
    site = site_store.site
    other = (body or {}).get("other", "")
    mode = (body or {}).get("mode", "link")       # link | block | auto
    if not site.room(rid) or not site.room(other):
        return _fail("both rooms must exist", 404)
    if rid == other:
        return _fail("a room cannot link to itself")

    def edit(s):
        a, b = s.room(rid), s.room(other)
        for room, mate in ((a, other), (b, rid)):
            room.links = [x for x in room.links if x != mate]
            room.blocks = [x for x in room.blocks if x != mate]
        if mode == "link":
            a.links.append(other)
        elif mode == "block":
            a.blocks.append(other)
    site_store.mutate(edit)
    return _ok({"mode": mode})


# ----------------------------------------------------------------------- markers
@router.post("/marker")
async def add_marker(body: dict):
    site = site_store.site
    kind = (body or {}).get("type", "")
    if kind not in MARKER_TYPES:
        return _fail(f"type must be one of {', '.join(MARKER_TYPES)}")
    fid = int(body.get("floor", 0))
    if not site.floor(fid):
        return _fail(f"no floor {fid}", 404)
    try:
        default = [0.45, 0.45, 0.16, 0.10] if kind == "route" else [0.48, 0.48, 0.03, 0.04]
        rect = _clamp_rect(body.get("rect", default))
    except ValueError as e:
        return _fail(str(e))
    mid = site.next_marker_id(kind)

    def edit(s):
        s.markers.append(Marker(id=mid, type=kind, floor=fid, rect=rect,
                                room=body.get("room", ""),
                                label=str(body.get("label", ""))[:60],
                                meta=dict(body.get("meta", {}))))
    site_store.mutate(edit)
    return _ok({"marker": next(m.as_dict() for m in site_store.site.markers
                               if m.id == mid)})


@router.patch("/marker/{mid}")
async def update_marker(mid: str, body: dict):
    site = site_store.site
    if not any(m.id == mid for m in site.markers):
        return _fail(f"no marker {mid}", 404)

    def edit(s):
        m = next(x for x in s.markers if x.id == mid)
        if body.get("rect") is not None:
            m.rect = _clamp_rect(body["rect"])
        if body.get("label") is not None:
            m.label = str(body["label"])[:60]
        if body.get("room") is not None:
            m.room = str(body["room"])
        if body.get("meta") is not None:
            m.meta = dict(body["meta"])
    site_store.mutate(edit)
    return _ok()


@router.delete("/marker/{mid}")
async def delete_marker(mid: str):
    if not any(m.id == mid for m in site_store.site.markers):
        return _fail(f"no marker {mid}", 404)

    def edit(s):
        s.markers = [m for m in s.markers if m.id != mid]
    site_store.mutate(edit)
    return _ok({"deleted_marker": mid})
