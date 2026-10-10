"""
Plant Intelligence Engine — the orchestrator.

Runs the real-time loop that stitches every subsystem together:

    sensors → hazard scoring → incidents → alerts → evacuation → analytics

It owns the authoritative plant state behind a lock, exposes a single
`snapshot()` for the dashboard / Copilot, simulates occupancy dynamics (workers
drain out of a zone once it is evacuating) and accepts operator commands
(inject scenario, mitigate, acknowledge, resolve). Computer-vision input, when
available, is merged in as just another sensor source, so the platform behaves
identically with or without cameras.
"""
from __future__ import annotations
import random
import threading
import time
from typing import Dict, List, Optional

from .. import __version__
from ..settings import (ANALYTICS_SECONDS, LOOP_SECONDS, ORG_NAME,
                        PRODUCT_NAME, PRODUCT_TAGLINE)
from ..analytics.analytics import Analytics
from ..sensors.iot_simulator import EVENT_LABELS, SensorMesh
from ..sensors.plant_layout import (EXITS, FLOOR_COLS, FLOOR_ROWS,
                                     OPERATIONAL_ZONES, ZONES, floor_meta,
                                     get_zone, total_base_occupancy)
from .alerts import AlertManager
from .audit import audit
from .compliance import ComplianceMonitor
from .evacuation import plan_evacuation
from .hazard_engine import HazardEngine
from .incident import IncidentManager

def _vision_status() -> dict:
    """Whether the detection models are actually ready yet."""
    try:
        from ..vision.detectors import vision_status
        return vision_status()
    except Exception:
        return {"status": "unavailable", "detail": "", "seconds": 0.0}


def ZONE_MAP_LOCAL():
    from ..sensors.plant_layout import ZONE_MAP
    return ZONE_MAP


# dominant hazard per zone from its sensor signature (for incident labelling)
_HAZARD_OF_SENSOR = {"gas": "gas", "temperature": "temperature",
                     "smoke": "smoke", "vibration": "vibration", "fire": "fire"}


def _org_name() -> str:
    """Whose building this is.

    The loaded site document carries its own name — a hospital site says so —
    and that is what should appear over the dashboard and on every report.
    ORG_NAME from settings is the fallback for a site that never set one, and
    the SAFEX_ORG environment variable still overrides both.
    """
    import os
    env = os.environ.get("SAFEX_ORG")
    if env:
        return env
    try:
        from ..site import site_store
        org = (site_store.site.org or "").strip()
        if org:
            return org
    except Exception:
        pass
    return ORG_NAME


class PlantEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self.mesh = SensorMesh()
        self.hazard = HazardEngine()
        self.incidents = IncidentManager()
        self.alerts = AlertManager()
        self.analytics = Analytics()
        self.compliance = ComplianceMonitor()

        # live occupancy per zone (starts at base; drains during evacuation)
        self.occupancy: Dict[str, int] = {z.id: z.base_occupancy
                                          for z in OPERATIONAL_ZONES}
        self._prev_incident_ids = set()
        self._resolved_ids = set()
        self._last_snapshot: dict = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._tick_count = 0

        # real-time loop bookkeeping
        self._wake = threading.Event()   # set by cameras to force an instant pass
        self._last_analytics = 0.0
        self._watchlist: List[dict] = []
        self._drain_acc: Dict[str, float] = {}
        self._rev = 0                    # snapshot revision (websocket change test)
        self._last_band = ""             # plant band, for camera urgency switching
        self._last_compliance: Dict = {}

        # optional CV bridge (populated by vision module if cameras run)
        self.cv_flame: Dict[str, float] = {}
        self.cv_people: Dict[str, int] = {}
        self.cv_smoke: Dict[str, float] = {}

        # operating mode: 'simulation' (sensor mesh drives) or 'live' (cameras drive)
        self.mode: str = "simulation"
        self._cameras = None            # lazy CameraManager (see cameras property)
        self._cam_lock = threading.Lock()
        self._cam_failed = False

        self._recompute()   # prime an initial snapshot

        # the operator can redraw the building while this is running
        from ..site import site_store
        site_store.on_change(lambda site: self.on_layout_changed())

    # --------------------------------------------------------------- lifecycle
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        last = time.time()
        while self._running:
            # sleep until the next slot — unless a camera wakes us early
            self._wake.wait(LOOP_SECONDS)
            self._wake.clear()
            now = time.time()
            dt, last = now - last, now
            try:
                self.tick(dt)
            except Exception as e:  # keep the loop alive no matter what
                print(f"[engine] tick error: {e}")

    def notify(self):
        """Wake the control loop immediately (a camera saw something change)."""
        self._wake.set()

    def on_layout_changed(self):
        """The site plan was edited — rooms may have appeared, moved or gone.

        Everything keyed by room id is refitted to the new building: rooms that
        survived keep their occupancy and their live state, rooms that vanished
        take their sensors, hazards and open incidents with them.
        """
        with self._lock:
            live = {z.id for z in OPERATIONAL_ZONES}
            self.mesh.resync()
            self.occupancy = {z.id: self.occupancy.get(z.id, z.base_occupancy)
                              for z in OPERATIONAL_ZONES}
            for book in (self.cv_flame, self.cv_smoke, self.cv_people,
                         self._drain_acc, self.hazard._prev, self.hazard._trend):
                for gone in [k for k in book if k not in live]:
                    book.pop(gone, None)
            for gone in [z for z in self.incidents.active if z not in live]:
                self.incidents.resolve(gone, "Room removed from the site plan")
            if self._cameras is not None:
                try:
                    self._cameras.on_layout_changed()
                except Exception as e:
                    print(f"[engine] camera relayout failed: {e}")
            self._watchlist = []
            self._recompute()
        self.notify()

    # --------------------------------------------------------------- core tick
    def tick(self, dt: float = LOOP_SECONDS):
        dt = max(0.0, min(2.0, dt))
        with self._lock:
            self._tick_count += 1
            self.mesh.tick(dt)
            cams = self.cameras
            if cams is not None:
                try:
                    cams.tick(self)          # bridge camera detections ↔ engine
                except Exception as e:
                    print(f"[engine] camera tick error: {e}")
            self._update_occupancy(dt)
            self._recompute(dt)

    def _recompute(self, dt: float = LOOP_SECONDS):
        readings = self.mesh.readings

        # merge computer-vision signals as an extra sensor source
        from ..sensors.plant_layout import ZONE_MAP as _ZM
        for zid, conf in self.cv_flame.items():
            if zid in readings and conf > 0:
                readings[zid]["fire"] = max(readings[zid].get("fire", 0), conf)
                # a confirmed flame implies heat + smoke in the same zone, so the
                # camera drives the full sensor picture (mirrors a real fire)
                readings[zid]["temperature"] = max(readings[zid].get("temperature", 0), 50 + conf * 115)
                readings[zid]["smoke"] = max(readings[zid].get("smoke", 0), conf * 85)
                # smoke bleeds into adjacent zones (neighbours turn orange)
                for nb in (_ZM[zid].neighbors if zid in _ZM else []):
                    if nb in readings:
                        readings[nb]["smoke"] = max(readings[nb].get("smoke", 0), conf * 44)
        for zid, conf in self.cv_smoke.items():
            if zid in readings and conf > 0:
                readings[zid]["smoke"] = max(readings[zid].get("smoke", 0), conf * 100)
        occ = dict(self.occupancy)
        for zid, n in self.cv_people.items():
            occ[zid] = max(occ.get(zid, 0), n)

        # score every zone
        zone_risks = []
        for z in OPERATIONAL_ZONES:
            zr = self.hazard.score_zone(z, readings[z.id], occ.get(z.id, 0), dt)
            zone_risks.append(zr)
        plant = self.hazard.plant_score(zone_risks)

        # ---- compliance: what the cameras say about the escape routes ----
        cams = self._cameras
        egress, doors, assets = [], [], []
        if cams is not None:
            try:
                egress = cams.egress_report()
                doors = cams.door_report()
                assets = cams.asset_report()
            except Exception as e:
                print(f"[engine] compliance report failed: {e}")
        bands = {zr.zone_id: zr.band for zr in zone_risks}
        new_violations = self.compliance.update(egress, bands, doors, assets)

        # A fire door standing open is a hole in the compartmentation: smoke
        # crosses it instead of being held back, so the rooms next door take
        # more of it than the walls alone would suggest.
        for room in self.compliance.open_fire_doors():
            if readings.get(room, {}).get("smoke", 0) > 25:
                bleed = readings[room]["smoke"] * 0.55
                for nb in (_ZM[room].neighbors if room in _ZM else []):
                    if nb in readings:
                        readings[nb]["smoke"] = max(readings[nb].get("smoke", 0), bleed)

        # evacuation over current hazard field. A route the cameras report as
        # obstructed is expensive to walk and a blocked one is not walkable at
        # all — so people are routed around a corridor full of stored beds the
        # same way they are routed around a fire.
        hazards = {zr.zone_id: zr.score for zr in zone_risks}
        for room, severity in self.compliance.blocked_rooms().items():
            if room in hazards:
                hazards[room] = max(hazards[room],
                                    95.0 if severity == "BLOCKED" else 60.0)
        evac = plan_evacuation(hazards, occ)

        # incidents
        for zr in zone_risks:
            dominant = self._dominant_hazard(zr)
            mitigating = any(e["zone"] == zr.zone_id and e["mitigating"]
                             for e in self.mesh.active_event_summary())
            before = zr.zone_id in self.incidents.active
            self.incidents.update(zr.zone_id, zr.name, zr.score,
                                  zr.occupancy, dominant, mitigating)
            after = zr.zone_id in self.incidents.active
            if after and not before:
                self.analytics.record_incident_opened()
                inc = self.incidents.active.get(zr.zone_id)
                audit.record("incident.opened",
                             f"{dominant.title()} incident opened in {zr.name}",
                             subject=zr.zone_id,
                             detail={"score": zr.score, "band": zr.band,
                                     "occupancy": zr.occupancy,
                                     "hazard": dominant,
                                     "incident": getattr(inc, "id", "")})

        # detect newly-resolved incidents (auto or manual) for analytics
        for inc in self.incidents.history:
            if inc.id not in self._resolved_ids:
                self._resolved_ids.add(inc.id)
                self.analytics.record_incident_resolved(inc.duration_s)
                audit.record("incident.resolved",
                             f"Incident {inc.id} in {inc.zone_name} resolved "
                             f"after {int(inc.duration_s)}s",
                             subject=inc.zone_id,
                             detail={"incident": inc.id, "peak": inc.peak_score,
                                     "duration_s": inc.duration_s})

        # analytics + alerts. Scoring runs at loop rate, but the trend history
        # and the forecast stay on a 1 Hz grid so "points per second" and the
        # ETA maths keep their meaning (and the chart keeps ~1 min of history).
        now = time.time()
        if now - self._last_analytics >= ANALYTICS_SECONDS:
            self._last_analytics = now
            self.analytics.record(zone_risks, plant)
            self._watchlist = self.analytics.predictive_watchlist(zone_risks)
        new_alerts = self.alerts.evaluate(zone_risks, plant, evac)
        for v in new_violations:
            audit.record("compliance.violation.opened",
                         f"[{v['severity']}] {v['detail']} — {v['room_name']}",
                         subject=v["room"],
                         detail={"type": v["type"], "severity": v["severity"],
                                 "floor": v["floor"], "camera": v.get("camera", ""),
                                 "summary": v.get("summary", "")})
            a = self.alerts.compliance_alert(v)
            if a:
                new_alerts.append(a)
        if cams is not None and plant["band"] != self._last_band:
            self._last_band = plant["band"]
            try:
                cams.set_alarm_urgency(plant["band"] in ("HIGH", "CRITICAL"))
            except Exception:
                pass
        watchlist = self._watchlist

        protected = total_base_occupancy() - evac["people_at_risk"]
        kpis = self.analytics.kpis(plant, evac["people_at_risk"],
                                   self.incidents.snapshot()["active_count"],
                                   max(0, protected))

        self._last_compliance = self.compliance.snapshot(floor_meta())
        self._rev += 1
        self._last_snapshot = {
            "meta": {"product": PRODUCT_NAME, "tagline": PRODUCT_TAGLINE,
                     "org": _org_name(), "version": __version__,
                     "tick": self._tick_count, "ts": time.time(),
                     "rev": self._rev, "mode": self.mode,
                     "cv_active": bool(self.cv_flame or self.cv_people or self.cv_smoke),
                     "vision": _vision_status()},
            "plant": plant,
            "zones": [self._zone_view(zr, readings[zr.zone_id]) for zr in zone_risks],
            "cameras": self._cameras.summaries() if self._cameras else [],
            "camera_movements": self._cameras.movements() if self._cameras else [],
            "floors": floor_meta(),
            "floor_risk": self._floor_risk(zone_risks),
            "compliance": self._last_compliance,
            "actions": self._action_view(),
            "egress": egress,
            "doors": doors,
            "assets": assets,
            "plan_detections": (self._cameras.plan_detections()
                                if self._cameras else {}),
            "floor": {"rows": FLOOR_ROWS, "cols": FLOOR_COLS,
                      "exits": EXITS},
            "incidents": self.incidents.snapshot(),
            "alerts": self.alerts.snapshot(),
            "new_alerts": [a.as_dict() for a in new_alerts],
            "evacuation": evac,
            "watchlist": watchlist,
            "kpis": kpis,
            "events": self.mesh.active_event_summary(),
            "plant_history": self.analytics.plant_history(),
        }

    # ------------------------------------------------------------- occupancy
    def _update_occupancy(self, dt: float):
        """Drain workers from zones with an active evacuation route.

        Draining is deliberately gradual (and scaled to crowd size) so the
        animated routes remain visible for the whole incident rather than
        vanishing in a tick — larger crowds take proportionally longer to clear.
        People on an upper floor take longer, because they have stairs to walk.
        """
        snap = self._last_snapshot
        if not snap or dt <= 0:
            return
        evac = snap.get("evacuation", {})
        scores = {z["zone_id"]: z["score"] for z in snap.get("zones", [])}
        for zid, route in evac.get("routes", {}).items():
            if route.get("blocked"):
                continue
            occ = self.occupancy.get(zid, 0)
            if occ <= 0 or scores.get(zid, 0) <= 48:
                continue
            # people per second leaving: ~0.5/s, faster for big crowds, slower
            # the more floors they have to descend
            rate = (0.5 + occ / 24.0) / (1.0 + 0.6 * route.get("floors_crossed", 0))
            acc = self._drain_acc.get(zid, 0.0) + rate * dt
            leaving = int(acc)
            self._drain_acc[zid] = acc - leaving
            if leaving:
                self.occupancy[zid] = max(0, occ - leaving)

    # ------------------------------------------------------------- operator ops
    def trigger_scenario(self, zone_id: str, kind: str, intensity: float = 1.0):
        with self._lock:
            ev = self.mesh.inject(zone_id, kind, intensity)
            audit.record("scenario.injected",
                         f"{EVENT_LABELS.get(kind, kind)} injected into "
                         f"{get_zone(zone_id).name} (drill)",
                         actor="operator", subject=zone_id,
                         detail={"kind": kind, "intensity": intensity})
            # score the new state straight away so the response is in the very
            # next frame the dashboard receives, not up to a loop later
            self._recompute()
        self.notify()
        return {"ok": True, "event_id": ev.id, "zone": zone_id,
                "kind": kind, "label": EVENT_LABELS.get(kind, kind),
                "floor": get_zone(zone_id).floor}

    def deploy_response(self, zone_id: str):
        """Dispatch responders: mitigate the event and mark incident responding."""
        with self._lock:
            self.mesh.mitigate_zone(zone_id)
            # in live mode the hazard is camera-driven — suppress the detection
            # for this zone so the response actually takes effect
            if self.mode == "live" and self._cameras is not None:
                self._cameras.clear_zone(zone_id)
                self.cv_flame[zone_id] = 0.0
                self.cv_smoke[zone_id] = 0.0
            self.incidents.acknowledge(zone_id)
            self._recompute()
        audit.record("action.taken", f"Emergency response deployed to "
                     f"{get_zone(zone_id).name if zone_id in ZONE_MAP_LOCAL() else zone_id}",
                     actor="operator", subject=zone_id)
        self.notify()
        return {"ok": True, "zone": zone_id}

    def resolve_incident(self, zone_id: str):
        audit.record("action.taken", f"Incident in {zone_id} closed by the operator",
                     actor="operator", subject=zone_id)
        with self._lock:
            inc = self.incidents.resolve(zone_id, "Manually resolved by operator")
            if inc:
                self._resolved_ids.add(inc.id)
                self.analytics.record_incident_resolved(inc.duration_s)
            return {"ok": bool(inc)}

    # ------------------------------------------------------------- cameras / mode
    @property
    def cameras(self):
        """Lazily create + start the camera fleet on first access.

        Guarded: the control loop and the first HTTP request race for this, and
        building the fleet twice would leave two capture threads fighting over
        the same physical device (which shows up as a feed stuck at ~1 FPS).
        The fleet is also fully built before it is published, so no caller can
        ever observe a half-populated camera list.
        """
        if self._cameras is None and not self._cam_failed:
            with self._cam_lock:
                if self._cameras is None and not self._cam_failed:
                    try:
                        from ..vision.camera_manager import CameraManager
                        mgr = CameraManager()
                        mgr.start_all()
                        self._cameras = mgr
                    except Exception as e:
                        print(f"[engine] camera init failed: {e}")
                        self._cam_failed = True
        return self._cameras

    def set_mode(self, mode: str):
        if mode not in ("simulation", "live"):
            return {"ok": False, "error": "mode must be 'simulation' or 'live'"}
        with self._lock:
            if mode == self.mode:
                return {"ok": True, "mode": mode}
            self.mode = mode
            audit.record("mode.changed", f"Operating mode set to {mode}",
                         actor="operator", detail={"mode": mode})
            cams = self.cameras
            if mode == "live":
                # sim-injected events step aside; cameras become authoritative
                self.mesh.clear()
            else:
                # camera-driven hazards clear; sensor mesh resumes control
                self.cv_flame.clear(); self.cv_smoke.clear(); self.cv_people.clear()
                self.occupancy = {z.id: z.base_occupancy for z in OPERATIONAL_ZONES}
            self._drain_acc.clear()
            if cams is not None:
                cams.on_mode_change(mode)
            self._recompute()
            self.notify()
            return {"ok": True, "mode": mode}

    def trigger_camera(self, camera_id: str, kind: str):
        cams = self.cameras
        if cams is None:
            return {"ok": False, "error": "camera subsystem unavailable"}
        with self._lock:
            return cams.trigger(camera_id, kind)

    def reset(self):
        audit.record("plant.reset", "Plant state reset to nominal",
                     actor="operator")
        with self._lock:
            self.mesh.clear()
            self.incidents = IncidentManager()
            self.alerts = AlertManager()
            self.analytics = Analytics()
            self.hazard = HazardEngine()
            self.occupancy = {z.id: z.base_occupancy for z in OPERATIONAL_ZONES}
            self.cv_flame.clear()
            self.cv_people.clear()
            self.cv_smoke.clear()
            self._drain_acc.clear()
            self._watchlist = []
            if self._cameras is not None:
                self._cameras.on_mode_change(self.mode)
            self._resolved_ids.clear()
            self._recompute()
        self.notify()
        return {"ok": True}

    def build_report(self, incident_id: str):
        with self._lock:
            ctx = {"plant": self._last_snapshot.get("plant"),
                   "org": _org_name(),
                   "kpis": self._last_snapshot.get("kpis")}
            return self.incidents.build_report(incident_id, ctx)

    # ------------------------------------------------------------------- views
    def snapshot(self) -> dict:
        with self._lock:
            return self._last_snapshot

    def _action_view(self) -> dict:
        """A compact view of the action layer for the dashboard.

        Deliberately part of the live snapshot: automation the operator cannot
        see is automation they cannot supervise.
        """
        try:
            from .actions import actions
            with_state = {"plant": self._last_snapshot.get("plant", {}),
                          "incidents": self.incidents.snapshot(),
                          "compliance": self._last_compliance}
            snap = actions.snapshot(with_state)
            return {"armed": snap["armed"], "active": snap["active"],
                    "suggested": snap["suggested"], "history": snap["history"][:6],
                    "boundary": snap["boundary"]}
        except Exception as e:
            return {"armed": False, "active": [], "suggested": [], "error": str(e)[:80]}

    def _floor_risk(self, zone_risks) -> List[dict]:
        """Per-floor roll-up so the operator can see, at a glance, which level
        of the building is in trouble while looking at another one."""
        out = []
        for f in floor_meta():
            zs = [zr for zr in zone_risks if get_zone(zr.zone_id).floor == f["id"]]
            if not zs:
                out.append({"floor": f["id"], "score": 0.0, "band": "NORMAL",
                            "occupancy": 0, "critical": 0, "worst": None})
                continue
            worst = max(zs, key=lambda z: z.score)
            from ..settings import hazard_band
            band, _ = hazard_band(worst.score)
            out.append({
                "floor": f["id"], "score": worst.score, "band": band,
                "occupancy": sum(z.occupancy for z in zs),
                "critical": sum(1 for z in zs if z.band in ("HIGH", "CRITICAL")),
                "worst": worst.zone_id, "worst_name": worst.name,
            })
        return out

    def _zone_view(self, zr, reading) -> dict:
        return {
            "zone_id": zr.zone_id, "name": zr.name,
            "category": get_zone(zr.zone_id).category,
            "grid": get_zone(zr.zone_id).grid,
            "floor": get_zone(zr.zone_id).floor,
            "vertical": get_zone(zr.zone_id).vertical,
            "critical": get_zone(zr.zone_id).critical_asset,
            "score": zr.score, "band": zr.band, "color": zr.color,
            "trend": zr.trend, "occupancy": zr.occupancy,
            "factors": zr.factors, "drivers": zr.drivers,
            "sensors": {k: reading.get(k) for k in
                        ("temperature", "gas", "smoke", "vibration", "noise")},
            "flame": reading.get("fire", 0.0),
        }

    @staticmethod
    def _dominant_hazard(zr) -> str:
        # A confirmed flame dominates the narrative regardless of weighting.
        # The threshold is in *weighted* points: fire carries 0.16 of the score,
        # so 4 points is a flame confidence of about 0.25 — the same confidence
        # at which the detector calls fire in the first place. It used to sit at
        # 6, which meant a fire that had only just been seen got logged as a
        # temperature incident.
        if zr.factors.get("fire", 0) > 4 or "flame conf" in " ".join(zr.drivers):
            return "fire"
        if not zr.factors:
            return "unknown"
        key = max(zr.factors, key=zr.factors.get)
        return _HAZARD_OF_SENSOR.get(key, key)


# module-level singleton used by the server
engine = PlantEngine()
