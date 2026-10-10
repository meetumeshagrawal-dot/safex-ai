"""
camera_manager — registry + lifecycle for all cameras, and the single point
where camera detections flow into the hazard engine.

Mode discipline (the toggle the operator controls):
  • SIMULATION : the IoT sensor mesh + injected scenarios drive the plant.
                 Cameras still render, MIRRORING each zone's live state so the
                 operator literally sees the fire on the feed — but they do NOT
                 write back into the engine (that would be circular).
  • LIVE CAM   : camera detections are authoritative. Fire/smoke/person counts
                 drive cv_flame / cv_smoke / cv_people for the assigned zone,
                 and the hazard engine propagates everything else (heatmap,
                 incidents, evacuation, copilot) exactly as for a sim event.

With real hardware + YOLO, live detections come from CameraStream. Without
hardware, live detections come from a per-camera trigger (see .trigger) so the
end-to-end pipeline is fully demonstrable. Real detections always win.
"""
from __future__ import annotations
import threading
import time
from typing import Dict, List

from ..core.audit import audit
from ..sensors.plant_layout import ZONE_MAP
from .camera_stream import CameraConfig, CameraStream
from .tracker import CentroidTracker, OccupancyTracker
from .zone_mapper import ZoneMapper, zone_name, zone_exists

ASSET_NAMES = {"extinguisher": "Fire extinguisher", "exit_sign": "Exit sign",
               "hydrant": "Hydrant / hose reel", "alarm_point": "Manual call point"}


def _fleet_from_site() -> List[tuple]:
    """The camera fleet is part of the site document, so a building someone
    drew (or a preset they picked) arrives with its cameras already placed."""
    from ..site import site_store
    out = []
    for c in site_store.site.cameras:
        if c.enabled and c.room in ZONE_MAP:
            out.append((c.id, c.name, c.room, c.source))
    return out


def probe_local_devices(max_index: int = 4) -> List[Dict]:
    """Which USB camera indices actually exist on this machine.

    Used by the Camera Monitor so an operator picks a real device from a list
    instead of typing an index and discovering it was wrong when the tile shows
    "offline". Each probe is opened and released immediately.
    """
    import cv2
    from .camera_stream import _open_capture
    out: List[Dict] = []
    for idx in range(max_index):
        cap, err = _open_capture(idx, "usb")
        if cap is None:
            continue
        ok, frame = cap.read()
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        try:
            cap.release()
        except Exception:
            pass
        if ok and frame is not None:
            out.append({"index": idx, "label": f"USB camera {idx}",
                        "resolution": f"{w}x{h}" if w else "unknown"})
    return out


class CameraManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.mapper = ZoneMapper()
        self.streams: "Dict[str, CameraStream]" = {}
        self.centroids: Dict[str, CentroidTracker] = {}
        self.occupancy = OccupancyTracker()
        self._triggers: Dict[str, Dict] = {}     # cam_id -> {fire,smoke}
        self._trig_t0: Dict[str, float] = {}      # when a trigger began (s)
        self._syn_occ: Dict[str, float] = {}      # synthetic people count (drains)
        self._engine = None                       # set on the first tick
        self._suspend_persist = False             # see reconcile_with_site
        for cid, name, zid, src in _fleet_from_site():
            self.add_camera(cid, name, zid, source=src, start=False)
        self._apply_config_sources()
        self.sync_calibration()

    # --------------------------------------------------------- instant bridge
    def _on_detection_change(self, cam_id: str, det: Dict):
        """Called by a CameraStream the moment its alarm state flips. Pushes
        the detection into the engine and wakes the control loop, so a flame
        appearing on a feed reaches the hazard map in milliseconds rather than
        waiting for the next scheduled pass."""
        eng, st = self._engine, self.streams.get(cam_id)
        if eng is None or st is None:
            return
        try:
            if getattr(eng, "mode", "simulation") == "live":
                zid = st.cfg.zone_id
                eng.cv_flame[zid] = float(det["fire_conf"]) if det.get("fire") else 0.0
                eng.cv_smoke[zid] = float(det["smoke_conf"]) if det.get("smoke") else 0.0
            eng.notify()
        except Exception:
            pass

    def _new_stream(self, cfg: CameraConfig) -> CameraStream:
        return CameraStream(cfg, on_change=self._on_detection_change)

    def _apply_config_sources(self):
        """If launched with --camera, attach the real cameras listed in
        config.py CAMERA_SOURCES to the default cameras in order (laptop cam,
        phone cam, ...). Without --camera everything stays synthetic so the
        no-hardware demo is clean."""
        import os
        if os.environ.get("SAFEX_LIVE_CAMERAS") != "1":
            return
        try:
            import config as _cfg
            sources = list(getattr(_cfg, "CAMERA_SOURCES", []) or [])
        except Exception:
            sources = []
        for (cid, name, zid, _), src in zip(_fleet_from_site(), sources):
            if cid in self.streams:
                self.streams[cid] = self._new_stream(
                    CameraConfig(id=cid, name=name, zone_id=zid, source=src))

    # ------------------------------------------------------------- registry
    def next_camera_id(self) -> str:
        """The first free CAM-NN.

        Numbering from the fleet size breaks as soon as anything is deleted:
        remove one camera and the next add collides with an id that is still in
        use, and the new camera silently never appears.
        """
        with self._lock:
            taken = set(self.streams)
        n = 1
        while f"CAM-{n:02d}" in taken:
            n += 1
        return f"CAM-{n:02d}"

    def _persist(self):
        """Write the live fleet back into the site document.

        Suppressed while reconciling: reconciliation reads the site and edits
        the fleet, so writing back mid-way would erase the very cameras it is
        about to start — the fleet would end up empty every time somebody
        loaded a different building.

        A camera someone adds from the dashboard has to survive a restart — the
        site document is where the building lives, and its cameras are part of
        the building.
        """
        if self._suspend_persist:
            return
        from ..site import site_store
        from ..site.model import CameraDef
        with self._lock:
            live = {cid: (st.cfg.name, st.cfg.zone_id, str(st.cfg.source))
                    for cid, st in self.streams.items()}

        def edit(site):
            keep = []
            for c in site.cameras:
                if c.id in live:
                    c.name, c.room, c.source = live[c.id]
                    keep.append(c)
                # a camera no longer running is dropped, unless it was disabled
                elif not c.enabled:
                    keep.append(c)
            known = {c.id for c in keep}
            for cid, (name, room, source) in live.items():
                if cid not in known and source != "browser":
                    keep.append(CameraDef(id=cid, name=name, room=room,
                                          source=source))
            site.cameras = keep
        try:
            site_store.mutate(edit)
        except Exception as e:
            print(f"[cameras] could not persist the fleet: {e}")

    def add_camera(self, cam_id, name, zone_id, source="synthetic", start=True,
                   persist=False):
        """Register a camera. Returns immediately — opening the device (which
        can take seconds for a USB/RTSP source) happens on a worker thread, so
        the dashboard never freezes while a camera is being added."""
        if not self.mapper.assign(cam_id, zone_id):
            return {"ok": False, "error": f"unknown zone {zone_id}"}
        if cam_id in self.streams:
            return {"ok": False, "error": f"camera {cam_id} already exists"}
        cfg = CameraConfig(id=cam_id, name=name, zone_id=zone_id, source=source)
        stream = self._new_stream(cfg)
        with self._lock:
            self.streams[cam_id] = stream
            self.centroids[cam_id] = CentroidTracker()
            self._syn_occ[cam_id] = float(ZONE_MAP[zone_id].base_occupancy)
        if start:
            stream.start()
        self.sync_calibration()
        self.sync_egress_watch()
        if persist:
            self._persist()
            audit.record("camera.added",
                         f"Camera {cam_id} ({name}) added to "
                         f"{ZONE_MAP[zone_id].name}",
                         actor="operator", subject=cam_id,
                         detail={"room": zone_id, "source": str(source)})
        return {"ok": True, "camera": cam_id, "zone": zone_id,
                "zone_name": ZONE_MAP[zone_id].name,
                "floor": ZONE_MAP[zone_id].floor,
                "source_type": stream.cfg.source_type,
                "status": stream.status()}

    def configure(self, cam_id, source=None, zone_id=None, name=None):
        old = self.streams.get(cam_id)
        if not old:
            return {"ok": False, "error": "no such camera"}
        if zone_id and not self.mapper.assign(cam_id, zone_id):
            return {"ok": False, "error": f"unknown zone {zone_id}"}
        cfg = old.cfg
        prev_zone = cfg.zone_id
        new = CameraConfig(id=cam_id, name=name or cfg.name,
                           zone_id=zone_id or cfg.zone_id,
                           source=source if source is not None else cfg.source)
        stream = self._new_stream(new)
        with self._lock:
            self.streams[cam_id] = stream
            self._syn_occ[cam_id] = float(ZONE_MAP[new.zone_id].base_occupancy)
            self._triggers.pop(cam_id, None)
        stream.start()
        old.stop()                       # release the old device afterwards
        self.sync_calibration()
        self.sync_egress_watch()
        self._persist()
        # a camera that moved to another room must not leave a hazard behind
        if prev_zone != new.zone_id and self._engine is not None:
            self._clear_engine_zone(prev_zone)
        return {"ok": True, "camera": cam_id, "zone": new.zone_id,
                "zone_name": ZONE_MAP[new.zone_id].name,
                "floor": ZONE_MAP[new.zone_id].floor,
                "source_type": new.source_type, "status": stream.status()}

    def _clear_engine_zone(self, zone_id):
        eng = self._engine
        if eng is None or any(s.cfg.zone_id == zone_id for s in self.streams.values()):
            return
        eng.cv_flame.pop(zone_id, None)
        eng.cv_smoke.pop(zone_id, None)
        eng.cv_people.pop(zone_id, None)

    def start_all(self):
        for st in self.streams.values():
            st.start()

    def stop_all(self):
        for st in self.streams.values():
            st.stop()

    def remove_camera(self, cam_id):
        """Delete a camera. Returns the freed zone so the caller can clear any
        CV-driven hazard it was leaving behind."""
        with self._lock:
            st = self.streams.pop(cam_id, None)
            if st is None:
                return {"ok": False, "error": "no such camera"}
            self.centroids.pop(cam_id, None)
            self._triggers.pop(cam_id, None)
            self._trig_t0.pop(cam_id, None)
            self._syn_occ.pop(cam_id, None)
            self.mapper.remove(cam_id)
        zid = st.cfg.zone_id
        try:
            st.stop()
        except Exception:
            pass
        if not self._suspend_persist:
            audit.record("camera.removed",
                         f"Camera {cam_id} removed from "
                         f"{ZONE_MAP[zid].name if zid in ZONE_MAP else zid}",
                         actor="operator", subject=cam_id, detail={"room": zid})
        self._persist()
        orphan = not any(s.cfg.zone_id == zid for s in self.streams.values())
        return {"ok": True, "removed": cam_id, "zone": zid, "orphaned": orphan}

    # ------------------------------------------------------------- demo trigger
    def trigger(self, cam_id, kind):
        """Simulate a camera detection so Live mode is demoable without hardware."""
        if cam_id not in self.streams:
            return {"ok": False, "error": "no such camera"}
        if kind == "clear":
            self._triggers.pop(cam_id, None)
            self._trig_t0.pop(cam_id, None)
        elif kind == "fire":
            self._triggers[cam_id] = {"fire": True, "smoke": True}
            self._trig_t0[cam_id] = time.time()
        elif kind == "smoke":
            self._triggers[cam_id] = {"fire": False, "smoke": True}
            self._trig_t0[cam_id] = time.time()
        elif kind == "people":
            self._syn_occ[cam_id] = self._syn_occ.get(cam_id, 0) + 3
        elif kind in ("obstruct", "block"):
            st = self.streams[cam_id]
            heavy = kind == "block"
            st.set_synthetic_obstruction({
                "severity": "BLOCKED" if heavy else "OBSTRUCTED",
                "fraction": 0.72 if heavy else 0.41,
                "objects": ([{"label": "bed / stretcher", "raw": "bed", "conf": 0.83}]
                            if not heavy else
                            [{"label": "stores", "raw": "book", "conf": 0.71},
                             {"label": "bed / stretcher", "raw": "bed", "conf": 0.79}]),
                "people_standing": 0, "unknown_fraction": 0.12 if heavy else 0.0,
                "duration_s": 45.0, "confirmed": True, "reference_ready": True,
                "note": ("Egress route is impassable" if heavy else
                         "Egress width lost to bed / stretcher"),
            })
        elif kind == "clear_obstruction":
            self.streams[cam_id].set_synthetic_obstruction(None)
        elif kind in ("asset_blocked", "asset_missing", "asset_ok"):
            from ..site import site_store
            st = self.streams[cam_id]
            for m in site_store.site.markers:
                if m.type in ASSET_NAMES and m.room == st.cfg.zone_id:
                    if kind == "asset_ok":
                        st.set_synthetic_asset(m.id, None)
                    elif kind == "asset_blocked":
                        st.set_synthetic_asset(m.id, {
                            "state": "BLOCKED", "similarity": 0.71, "cover": 0.48,
                            "brightness_ratio": 1.0, "duration_s": 130.0,
                            "violation": True,
                            "note": f"{m.label or ASSET_NAMES.get(m.type)} access is "
                                    f"obstructed by stores"})
                    else:
                        st.set_synthetic_asset(m.id, {
                            "state": "MISSING", "similarity": 0.28, "cover": 0.0,
                            "brightness_ratio": 1.0, "duration_s": 240.0,
                            "violation": True,
                            "note": f"{m.label or ASSET_NAMES.get(m.type)} is not "
                                    f"where it should be"})
        elif kind in ("door_open", "door_closed"):
            from ..site import site_store
            st = self.streams[cam_id]
            opened = kind == "door_open"
            for m in site_store.site.markers:
                if m.type == "fire_door" and m.room == st.cfg.zone_id:
                    st.set_synthetic_door(m.id, None if not opened else {
                        "state": "HELD_OPEN", "similarity": 0.31,
                        "open_for_s": 92.0, "source": "camera", "sensor_state": "",
                        "note": "Fire door held open 1 min", "violation": True})
        if self._engine is not None:
            self._engine.notify()
        return {"ok": True, "camera": cam_id, "kind": kind}

    # ------------------------------------------------------------- per-tick
    def tick(self, engine):
        """Called from engine.tick() (inside engine lock). Bridges CV↔engine."""
        with self._lock:
            self._engine = engine
            mode = getattr(engine, "mode", "simulation")
            snap = getattr(engine, "_last_snapshot", {}) or {}
            zstate = {z["zone_id"]: z for z in snap.get("zones", [])}
            cam_counts: Dict[str, int] = {}
            # A room can be watched by several cameras, so detections are
            # aggregated per ROOM before they touch the engine — otherwise the
            # last camera in the loop would overwrite the others, and one
            # camera seeing a clear corridor would cancel another one seeing
            # flame in the same room.
            agg: Dict[str, Dict] = {}

            for cam_id, st in list(self.streams.items()):
                zid = st.cfg.zone_id
                if zid not in ZONE_MAP:          # room deleted from the plan
                    continue
                if st.is_live:                                   # real capture, or browser
                    if not st.is_active():
                        # a live source that isn't delivering frames yet (e.g. a
                        # browser cam before "Start Camera") — don't touch its zone
                        continue
                    det = st.detection()
                elif mode == "live":                             # synthetic + live
                    det = self._synthetic_live(cam_id, zid, zstate)
                    st.set_synthetic_detection(det)
                else:                                            # simulation mirror
                    det = self._mirror(zid, zstate)
                    st.set_synthetic_detection(det)

                a = agg.setdefault(zid, {"fire": 0.0, "smoke": 0.0,
                                         "people": 0, "live": False})
                if det.get("fire"):
                    a["fire"] = max(a["fire"], float(det.get("fire_conf", 0.0)))
                if det.get("smoke"):
                    a["smoke"] = max(a["smoke"], float(det.get("smoke_conf", 0.0)))
                # headcount takes the best view rather than the sum: two cameras
                # covering one ward would otherwise count the same patients twice
                a["people"] = max(a["people"], int(det.get("people", 0)))
                a["live"] = a["live"] or st.is_live

            for zid, a in agg.items():
                cam_counts[zid] = a["people"]
                if mode == "live":
                    engine.cv_flame[zid] = a["fire"]
                    engine.cv_smoke[zid] = a["smoke"]
                    # a REAL/browser camera is authoritative on occupancy; a
                    # synthetic feed reads back the engine's own drained count
                    # so evacuation still empties the room
                    if a["live"]:
                        engine.occupancy[zid] = a["people"]

            # occupancy tracker (movement inference between adjacent zones)
            self.occupancy.update(cam_counts)

    def _synthetic_live(self, cam_id, zid, zstate) -> Dict:
        trig = self._triggers.get(cam_id, {})
        fire = bool(trig.get("fire"))
        smoke = bool(trig.get("smoke"))
        # a detected fire is called at once, then grows over the first seconds
        age = (time.time() - self._trig_t0.get(cam_id, 0.0)) if (fire or smoke) else 0.0
        fconf = min(0.98, 0.62 + age * 0.09) if fire else 0.0
        sconf = min(0.9, 0.55 + age * 0.06) if smoke else 0.0
        # occupancy is owned + drained by the engine; the camera reports it back
        z = zstate.get(zid, {})
        people = int(z.get("occupancy", ZONE_MAP[zid].base_occupancy))
        return {"fire": fire, "fire_conf": round(fconf, 2),
                "smoke": smoke, "smoke_conf": round(sconf, 2),
                "people": people, "boxes": []}

    def _mirror(self, zid, zstate) -> Dict:
        z = zstate.get(zid)
        if not z:
            return {"fire": False, "fire_conf": 0.0, "smoke": False,
                    "smoke_conf": 0.0, "people": ZONE_MAP[zid].base_occupancy, "boxes": []}
        flame = z.get("flame", 0) or 0
        drivers = z.get("drivers", [])
        fireish = flame > 0.15 or (z.get("band") == "CRITICAL"
                                   and any("fire" in d.lower() or "flame" in d.lower() for d in drivers))
        smoke_read = (z.get("sensors", {}) or {}).get("smoke", 0)
        return {
            "fire": bool(fireish), "fire_conf": round(min(0.99, max(flame, z.get("score", 0) / 100)), 2) if fireish else 0.0,
            "smoke": smoke_read > 38, "smoke_conf": round(min(0.95, smoke_read / 100), 2),
            "people": int(z.get("occupancy", 0)), "boxes": [],
        }

    # ------------------------------------------------------------- views
    def summaries(self) -> List[Dict]:
        """Every camera, ordered by floor then id.

        Insertion order looks arbitrary the moment anything is deleted and
        re-added — the monitor wall should read like the building, not like the
        history of who clicked what.
        """
        with self._lock:
            items = [st.summary() for st in self.streams.values()]
        return sorted(items, key=lambda c: (c.get("floor", 0), c.get("id", "")))

    def camera_zone_labels(self) -> Dict[str, str]:
        """room -> label for the floor map ("Ward Cam" or "3 cameras")."""
        by_zone: Dict[str, List[str]] = {}
        for st in self.streams.values():
            by_zone.setdefault(st.cfg.zone_id, []).append(st.cfg.name)
        return {zid: (names[0] if len(names) == 1 else f"{len(names)} cameras")
                for zid, names in by_zone.items()}

    def movements(self) -> List[Dict]:
        return self.occupancy.recent_movements()

    def frame_jpeg(self, cam_id) -> bytes:
        st = self.streams.get(cam_id)
        return st.frame_jpeg() if st else b""

    def ensure_browser_camera(self, zone_id, cam_id="CAM-WEB", name="Browser Camera"):
        """Create (or re-point) a browser-fed camera mapped to a zone. The
        visitor's browser streams frames to /ws/camera/<cam_id>."""
        if not zone_exists(zone_id):
            return {"ok": False, "error": f"unknown zone {zone_id}"}
        if cam_id in self.streams:
            self.configure(cam_id, source="browser", zone_id=zone_id, name=name)
        else:
            self.add_camera(cam_id, name, zone_id, source="browser", start=True)
        z = ZONE_MAP[zone_id]
        return {"ok": True, "camera_id": cam_id, "zone_id": zone_id,
                "zone_name": z.name, "floor": z.floor,
                "ws": f"/ws/camera/{cam_id}"}

    def clear_zone(self, zone_id):
        """Responders dispatched — clear any camera detection driving this zone
        (demo triggers). A real camera still seeing fire will re-detect it."""
        cleared = self.cameras_in(zone_id)
        for cam_id in cleared:
            self._triggers.pop(cam_id, None)
            self._trig_t0.pop(cam_id, None)
        return cleared[0] if cleared else None

    def on_layout_changed(self):
        """Rooms were added, moved or deleted. Cameras whose room no longer
        exists are retired — a feed pointing at a room that isn't in the
        building any more would keep injecting hazard into nothing."""
        with self._lock:
            orphans = [cid for cid, st in self.streams.items()
                       if st.cfg.zone_id not in ZONE_MAP]
        # Persistence stays off for the whole pass. Loading a different building
        # orphans every camera of the old one, and saving that empty fleet back
        # would delete the new building's cameras before they are ever started.
        self._suspend_persist = True
        try:
            for cid in orphans:
                print(f"[cameras] {cid}: room removed from plan — retiring camera")
                self.remove_camera(cid)
        finally:
            self._suspend_persist = False
        self.reconcile_with_site()
        self.sync_egress_watch()
        with self._lock:
            for cid, st in self.streams.items():
                z = ZONE_MAP.get(st.cfg.zone_id)
                if z is not None:
                    self._syn_occ.setdefault(cid, float(z.base_occupancy))
        return {"retired": orphans}

    def sync_calibration(self):
        """Push each camera's stored image-to-plan mapping onto its stream."""
        from ..site import site_store
        cal = {c.id: c.calibration for c in site_store.site.cameras if c.calibration}
        with self._lock:
            for cid, st in self.streams.items():
                st.set_calibration(cal.get(cid))
        return {"calibrated": [c for c in cal if c in self.streams]}

    def plan_detections(self) -> Dict[str, List[Dict]]:
        """Detections from every calibrated camera, positioned on the plan and
        grouped by floor, so the map can draw people where they really are."""
        out: Dict[str, List[Dict]] = {}
        with self._lock:
            items = list(self.streams.items())
        for cid, st in items:
            z = ZONE_MAP.get(st.cfg.zone_id)
            if z is None:
                continue
            for d in st.plan_detections():
                out.setdefault(str(z.floor), []).append(
                    {**d, "camera": cid, "room": z.id})
        return out

    def sync_egress_watch(self):
        """Point every camera at the egress routes drawn in its room.

        Only rooms carrying a monitored route are analysed: a chair in a ward
        is furniture, the same chair in a fire exit is a violation, and the
        difference is a decision the operator makes on the plan — not something
        a camera can infer.
        """
        from ..site import site_store
        routes: Dict[str, List] = {}
        doors: Dict[str, List] = {}
        assets: Dict[str, List] = {}
        for m in site_store.site.markers:
            if m.type == "route" and m.room:
                routes.setdefault(m.room, []).append(m)
            elif m.type == "fire_door" and m.room:
                doors.setdefault(m.room, []).append(m)
            elif m.room:
                assets.setdefault(m.room, []).append(m)
        with self._lock:
            for cid, st in self.streams.items():
                # fire doors in this camera's room
                specs = []
                for d in doors.get(st.cfg.zone_id, []):
                    meta = d.meta or {}
                    # a calibrated camera works out for itself where the door
                    # drawn on the plan appears in its own view
                    roi = st.roi_for_plan_rect(d.rect) or meta.get("roi")
                    specs.append({
                        "id": d.id, "label": d.label or d.id, "roi": roi,
                        "reference": meta.get("reference"),
                        "held_open_seconds": meta.get("held_open_seconds", 30),
                    })
                st.watch_doors(specs)
                st.watch_assets([{
                    "id": a.id, "kind": a.type,
                    "label": a.label or ASSET_NAMES.get(a.type, a.type),
                    "roi": st.roi_for_plan_rect(a.rect) or (a.meta or {}).get("roi"),
                    "reference": (a.meta or {}).get("reference"),
                } for a in assets.get(st.cfg.zone_id, [])])
                marks = routes.get(st.cfg.zone_id) or []
                if not marks:
                    st.watch_egress(False)
                    continue
                meta = marks[0].meta or {}
                # How wide this corridor really is. Once the floor plan has a
                # scale, the drawn corridor IS a real dimension — no need to ask
                # anyone to measure it with a tape. Falls back to whatever was
                # entered by hand, and finally to a typical hospital corridor.
                floor = site_store.site.floor(ZONE_MAP[st.cfg.zone_id].floor)
                width_m = 0.0
                if floor is not None and floor.width_m:
                    short_side = min(marks[0].rect[2], marks[0].rect[3])
                    width_m = round(short_side * floor.width_m, 2)
                st._corridor_m = (width_m or float(meta.get("corridor_m", 0) or 0)
                                  or 2.4)
                if st.cfg.source_type != "synthetic":
                    st.watch_egress(True, roi=meta.get("roi"),
                                    corridor_m=st._corridor_m)
        return {"watching": [c for c, st in self.streams.items()
                             if st.obstruction() is not None or
                             getattr(st, "_obstruction", None) is not None]}

    def door_report(self) -> List[Dict]:
        """Fire door status across the site, one entry per door."""
        out: List[Dict] = []
        with self._lock:
            for cid, st in self.streams.items():
                z = ZONE_MAP.get(st.cfg.zone_id)
                for d in st.doors():
                    out.append({**d, "room": st.cfg.zone_id,
                                "room_name": z.name if z else st.cfg.zone_id,
                                "floor": z.floor if z else 0,
                                "camera": cid, "camera_name": st.cfg.name})
        return out

    def asset_report(self) -> List[Dict]:
        """Every monitored fire-safety asset, with where it is."""
        out: List[Dict] = []
        with self._lock:
            items = list(self.streams.items())
        for cid, st in items:
            z = ZONE_MAP.get(st.cfg.zone_id)
            for a in st.assets():
                out.append({**a, "room": st.cfg.zone_id,
                            "room_name": z.name if z else st.cfg.zone_id,
                            "floor": z.floor if z else 0,
                            "camera": cid, "camera_name": st.cfg.name})
        return out

    def capture_asset_reference(self, asset_id: str) -> Dict:
        """Learn an asset where it is now, and remember it on its marker."""
        from ..site import site_store
        marker = next((m for m in site_store.site.markers if m.id == asset_id), None)
        if marker is None:
            return {"ok": False, "error": f"no asset {asset_id}"}
        with self._lock:
            cams = [st for st in self.streams.values()
                    if st.cfg.zone_id == marker.room and asset_id in st._assets]
        for st in cams:
            ref = st.capture_asset_reference(asset_id)
            if ref and ref != "synthetic":
                def edit(site):
                    m = next(x for x in site.markers if x.id == asset_id)
                    m.meta = {**(m.meta or {}), "reference": ref,
                              "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                site_store.mutate(edit)
                audit.record("asset.calibrated",
                             f"{marker.label or asset_id} learnt in place",
                             actor="operator", subject=asset_id,
                             detail={"camera": st.cfg.id, "room": marker.room,
                                     "kind": marker.type})
                return {"ok": True, "asset": asset_id, "camera": st.cfg.id}
        if cams:
            return {"ok": False, "error": "this room's camera is a demo feed — "
                                          "there is no real asset to learn"}
        return {"ok": False, "error": "no camera in this room can see that asset"}

    def capture_door_reference(self, door_id: str) -> Dict:
        """Learn a door in its closed position and persist it on the marker."""
        from ..site import site_store
        marker = next((m for m in site_store.site.markers if m.id == door_id), None)
        if marker is None or marker.type != "fire_door":
            return {"ok": False, "error": f"no fire door {door_id}"}
        with self._lock:
            cams = [st for st in self.streams.values()
                    if st.cfg.zone_id == marker.room and door_id in st._doors]
            synth = [st for st in self.streams.values()
                     if st.cfg.zone_id == marker.room]
        for st in cams:
            ref = st.capture_door_reference(door_id)
            if ref and ref != "synthetic":
                def edit(site):
                    m = next(x for x in site.markers if x.id == door_id)
                    m.meta = {**(m.meta or {}), "reference": ref,
                              "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                site_store.mutate(edit)
                audit.record("door.calibrated",
                             f"Fire door {marker.label or door_id} learnt in its "
                             f"closed position",
                             actor="operator", subject=door_id,
                             detail={"camera": st.cfg.id, "room": marker.room})
                return {"ok": True, "door": door_id, "camera": st.cfg.id}
        if synth:
            return {"ok": False, "error": "this room's camera is a demo feed — "
                                          "there is no real door to learn"}
        return {"ok": False, "error": "no camera in this room can see that door"}

    def set_door_sensor(self, door_id: str, state: str) -> Dict:
        """Feed a magnetic contact sensor reading in from the field."""
        touched = []
        with self._lock:
            for cid, st in self.streams.items():
                if door_id in st._doors:
                    st.set_door_sensor(door_id, state)
                    touched.append(cid)
        return {"ok": bool(touched), "door": door_id, "cameras": touched,
                "state": state}

    def egress_report(self) -> List[Dict]:
        """Current corridor status per room, worst camera wins."""
        out: Dict[str, Dict] = {}
        with self._lock:
            for cid, st in self.streams.items():
                ob = st.obstruction()
                if not ob:
                    continue
                rank = {"CLEAR": 0, "PENDING": 1, "NARROWED": 2,
                        "OBSTRUCTED": 3, "BLOCKED": 4}
                prev = out.get(st.cfg.zone_id)
                if not prev or rank.get(ob["severity"], 0) > rank.get(prev["severity"], 0):
                    z = ZONE_MAP.get(st.cfg.zone_id)
                    out[st.cfg.zone_id] = {**ob, "room": st.cfg.zone_id,
                                           "room_name": z.name if z else st.cfg.zone_id,
                                           "floor": z.floor if z else 0,
                                           "camera": cid, "camera_name": st.cfg.name}
        return list(out.values())

    def set_alarm_urgency(self, fast: bool):
        """While the building is in alarm every corridor is re-checked
        continuously, because an escape route can be lost in seconds."""
        with self._lock:
            for st in self.streams.values():
                st.set_egress_urgency(fast)

    def reconcile_with_site(self):
        """Make the running fleet match the site document.

        Loading a different building — a preset, or someone else's site file —
        replaces the cameras along with the rooms. Without this the fleet keeps
        running the previous building's cameras, and the next save would write
        those stale entries back into the new site.

        Browser cameras are left alone: they belong to whoever is streaming
        from their own device right now, not to the stored building.
        """
        wanted = {cid: (name, room, src) for cid, name, room, src in _fleet_from_site()}
        with self._lock:
            running = {cid: st for cid, st in self.streams.items()}
        retired, added = [], []

        self._suspend_persist = True
        try:
            for cid, st in running.items():
                if st.cfg.source_type == "browser":
                    continue
                if cid not in wanted:
                    retired.append(cid)
            for cid in retired:
                self.remove_camera(cid)

            for cid, (name, room, src) in wanted.items():
                if cid not in self.streams and room in ZONE_MAP:
                    self.add_camera(cid, name, room, source=src, start=True)
                    added.append(cid)
                else:
                    st = self.streams.get(cid)
                    if st is not None and st.cfg.zone_id != room:
                        self.configure(cid, zone_id=room, name=name)
        finally:
            self._suspend_persist = False
        if retired or added:
            print(f"[cameras] fleet reconciled with the site: "
                  f"+{len(added)} -{len(retired)}")
        return {"added": added, "retired": retired}

    def cameras_in(self, zone_id: str) -> List[str]:
        """Every camera watching a room (a room may have several)."""
        return [cid for cid, st in self.streams.items()
                if st.cfg.zone_id == zone_id]

    def on_mode_change(self, mode):
        """Reset synthetic occupancy + triggers so a fresh drill starts clean."""
        with self._lock:
            if mode == "simulation":
                self._triggers.clear()
                self._trig_t0.clear()
            for cid, st in self.streams.items():
                self._syn_occ[cid] = float(ZONE_MAP[st.cfg.zone_id].base_occupancy)
