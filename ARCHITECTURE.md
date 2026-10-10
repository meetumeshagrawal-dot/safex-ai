# 🏗 SAFEX AI — Architecture

This document explains how the platform is put together, module by module, and
the reasoning behind the key design choices.

## 1. Principles

1. **One process, in-memory state, no build step.** A hackathon demo must never
   fail to compile or lose a database connection on stage. State lives in a
   single locked object; the frontend is dependency-light vanilla JS.
2. **Hardware-optional.** The platform's primary input is a simulated IoT mesh,
   so it runs anywhere. Computer vision is treated as *just another sensor
   source* and merged in when available — the rest of the system is identical.
3. **Explainable over clever.** The Hazard Score is deterministic, auditable
   maths with a per-factor breakdown. The AI Copilot is grounded in that state.
4. **Preserve the original work.** The project's original YOLOv8 CV pipeline and
   pygame simulations are kept and re-integrated, not thrown away.

## 2. Data flow (one tick, 1 Hz)

```
SensorMesh.tick()                 → raw sensor readings per zone
        │  (+ engine.cv_flame / cv_people if cameras are live)
        ▼
HazardEngine.score_zone()         → ZoneRisk {score, band, factors, drivers}
        │
        ▼
HazardEngine.plant_score()        → plant-wide index (worst-weighted)
        │
        ├─▶ plan_evacuation()      → per-zone A* routes + exit load
        ├─▶ IncidentManager.update → open / escalate / respond / resolve
        ├─▶ AlertManager.evaluate  → new prioritised alerts
        └─▶ Analytics.record       → history, KPIs, predictive watchlist
        ▼
engine._last_snapshot             → single JSON document
        │
        ├─ WebSocket /ws  ─────────▶ dashboard (1 Hz)
        └─ Copilot.state()  ───────▶ grounded answers
```

## 3. Modules

### `safex/settings.py`
Single source of tunables: hazard fusion **weights**, sensor **safe-operating
envelopes**, hazard **bands**, LLM config, server config, branding. A plant
would adapt the platform here without touching engine code.

### `safex/sensors/plant_layout.py`
The **digital twin**: 20 `Zone`s on a 4×5 floor grid, each with a category,
typical occupancy, the hazards it's prone to, and adjacency. The adjacency graph
is **self-healed on import** (edges made bidirectional, dangling refs dropped)
so the evacuation router can never crash on a bad layout.

### `safex/sensors/iot_simulator.py`
The **mock IoT mesh**. Each sensor signal is
`baseline + diurnal drift + correlated noise + active-event ramp`. An **event
engine** injects incidents (fire, gas leak, overheat, mechanical, smoke) that
ramp the relevant sensors along a plausible curve, bleed smoke/gas into
neighbours, and decay once responders mitigate. This is what makes the platform
fully demoable with no hardware.

### `safex/core/hazard_engine.py`
The **Risk Intelligence Engine**. Normalises each reading to a 0–1 severity
against its envelope, weights and sums them into a 0–100 score, applies a
consequence multiplier for critical-asset zones, and returns an explainable
per-factor breakdown + human-readable drivers. `plant_score()` aggregates with a
worst-zone-weighted formula so real spikes are felt but noise isn't.

### `safex/core/evacuation.py`
The **Intelligent Evacuation Router**. Hazard-aware **A\*** over the zone graph:
stepping into a zone costs more as its hazard rises, and zones above a threshold
are impassable. Routes therefore bend around fire/gas and re-plan every tick.
Generalises the original project's `choose_best_exit` (crowd + fire avoidance)
to a real floor plan. Reports per-zone routes, exit load and blocked egress.

### `safex/core/incident.py`
**Incident lifecycle + reporting.** Opens an incident when a zone crosses the
open threshold, escalates severity, marks response, and auto-resolves with
hysteresis. Every transition is logged to a timeline. `build_report()` produces
a filing-ready report (root cause, timeline, corrective actions), rendered to
HTML by the server.

### `safex/core/alerts.py`
**Smart Alerts.** Converts hazard state into a prioritised feed with
de-duplication + cooldown (so the control room isn't flooded), and a `speak`
flag consumed by the browser voice system.

### `safex/analytics/analytics.py`
**KPIs + Predictive Risk.** Keeps per-zone score history; computes executive
KPIs (Safety Index, MTTR, people at risk, workers protected, uptime); and
projects each zone's score forward via damped linear extrapolation to build a
**watchlist** of zones trending into danger, with ETA to threshold.

### `safex/core/copilot.py`
The **AI Safety Copilot.** An intent-routed engine that answers *only* from the
live snapshot — status, worst zone, evacuation, prediction, incidents, people,
recommendations, and any zone by name. Always works offline. If `SAFEX_LLM=groq`
+ `GROQ_API_KEY` are set, it hands the structured context to LLaMA-3.3 for a
fluent narrative, with the grounded engine as a guaranteed fallback.

### `safex/core/engine.py`
The **orchestrator.** Owns all state behind an `RLock`, runs the 1 Hz loop in a
daemon thread, simulates occupancy dynamics (workers drain from evacuating
zones), merges CV signals, and exposes `snapshot()` plus operator commands
(`trigger_scenario`, `deploy_response`, `resolve_incident`, `reset`,
`build_report`). A module-level singleton `engine` is shared by the server.

### `safex/vision/` (optional)
`cv_bridge.py` runs the original YOLOv8 detectors in a background thread and
writes flame confidence + headcount into `engine.cv_flame` / `engine.cv_people`,
mapped to zones via `CAMERA_ZONE_MAP`. `detectors.py` reuses the exact model
logic from the original `fire_detection.py` / `people_detection.py`. All heavy
imports are lazy, so the core platform never depends on torch/opencv.

### `safex/server/app.py`
**FastAPI command centre.** Serves the SPA, streams the snapshot over WebSocket
at 1 Hz (with a polling fallback), and exposes the control REST API and the
HTML incident-report renderer. No database, no auth — state is the engine.

### `web/`
The **control-room dashboard**, split into focused modules: `app.js`
(WebSocket + state store + boot + helpers), `floor.js` (the signature hazard
map + animated route overlay + zone drawer), `panels.js` (KPIs, plant index,
trend chart, watchlist, alerts + voice, incident log), `copilot.js` (chat) and
`controls.js` (scenario console). No framework, no bundler.

## 4. Scaling notes (beyond the hackathon)

- **State**: the engine is stateless per-tick over its inputs; a production build
  would persist snapshots to a time-series DB and shard the engine per plant area.
- **Sensors**: `SensorMesh` would be replaced by an MQTT/OPC-UA ingest adapter
  emitting the same reading shape — nothing downstream changes.
- **Vision**: `cv_bridge` already isolates CV; more cameras = more entries in
  `CAMERA_ZONE_MAP`.
- **Multi-tenant**: one engine per plant behind the same server; the dashboard
  takes a plant id.

These are deliberately *not* implemented for the hackathon (no Docker, no cloud,
no microservices) — the goal is a polished, self-contained, demo-ready platform.

---

## 8. Vision subsystem (`safex/vision/`)

The computer-vision layer is treated as *just another sensor source*, so the
hazard engine never had to be rewritten to accommodate it. Modules:

| Module | Responsibility |
|--------|----------------|
| `zone_mapper.py` | Binds each `camera_id` → one `zone_id`, validated against the live plant layout. A camera's detections only ever touch its assigned zone. |
| `camera_stream.py` | One worker per camera. **One code path, two realities:** real `cv2.VideoCapture` + YOLO on hardware, or a synthetic surveillance renderer when no camera/model is present. Both emit annotated JPEG frames + a detection summary. |
| `detectors.py` | The original YOLOv8 fire / smoke / person logic, exposed per-frame via `Detector.detect()` (reused, not rewritten). Only loaded on real hardware. |
| `synthetic.py` | OpenCV renderer that turns a detection dict into a believable surveillance frame; `draw_detections()` is the **shared** overlay used for real *and* synthetic frames, so both look identical to the operator. |
| `tracker.py` | `CentroidTracker` (stable per-camera person IDs) + `OccupancyTracker` (per-zone occupancy and adjacent-zone movement inference). |
| `camera_manager.py` | Registry + lifecycle for the fleet, and the single point where detections flow into the engine, honouring the active mode. |

### Mode discipline (the Simulation / Live toggle)

`engine.mode ∈ {"simulation", "live"}`, flipped via `POST /api/mode`.

```
SIMULATION                           LIVE CAMERA
-----------                          -----------
sensor mesh + injections drive       camera detections drive
   engine hazard                        engine hazard
cameras MIRROR zone state            cameras are AUTHORITATIVE
   (read-only, no feedback)             cv_flame / cv_smoke / cv_people
scenario console: enabled            scenario console: disabled
```

Each tick, `engine.tick()` calls `camera_manager.tick(engine)` *inside the engine
lock*. In live mode it writes `cv_flame` / `cv_smoke` / `cv_people` for each
camera's zone; the existing `_recompute()` merge then treats a confirmed flame as
implying heat + smoke in-zone and bleeds smoke to neighbours — so a camera fire
produces the same heatmap, incident, evacuation and occupancy drain as a
simulated one. Without hardware, a per-camera trigger (`POST
/api/camera/{id}/trigger`) injects a detection so the full live pipeline is
demonstrable; real YOLO detections replace it transparently.

### Frame delivery

Frames are served as periodic JPEGs (`GET /api/camera/{id}/frame`) which the
browser refreshes via chained `onload` — robust, low-latency, and free of
long-lived streaming connections. Detection **stats** ride the existing 1 Hz
websocket snapshot (`state.cameras`, `state.camera_movements`, `meta.mode`).
