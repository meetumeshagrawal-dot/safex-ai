"""
SAFEX AI command-centre server.

Serves the operations dashboard and exposes:
  * `GET  /`                     the dashboard (static SPA)
  * `WS   /ws`                   real-time plant snapshot stream (1 Hz)
  * `GET  /api/state`            latest snapshot (polling fallback)
  * `POST /api/scenario`         inject a scenario (fire / gas leak / ...)
  * `POST /api/respond`          deploy emergency response to a zone
  * `POST /api/resolve`          resolve an incident
  * `POST /api/reset`            reset the plant to nominal
  * `POST /api/copilot`          ask the AI Safety Copilot
  * `GET  /api/report/{id}`      auto-generated incident report (HTML)
  * `GET  /api/layout`           static plant layout (zones + graph)

The server is deliberately self-contained: no database, no auth, no external
services required. State lives in the in-memory `PlantEngine` singleton.
"""
from __future__ import annotations
import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..settings import HOST, PORT, PRODUCT_NAME, STREAM_SECONDS
from ..core.copilot import Copilot
from ..core.engine import engine
from ..site import site_store
from ..sensors.plant_layout import ZONES, floor_meta
from .site_api import router as site_router

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

app = FastAPI(title=PRODUCT_NAME, docs_url="/api/docs")
copilot = Copilot(engine.snapshot)

# Dedicated pools so camera work can never starve the request handlers:
#   _FRAME_POOL  — JPEG encoding for MJPEG viewers
#   _DETECT_POOL — YOLO on frames pushed from a visitor's browser
_FRAME_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="frame")
_DETECT_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="detect")


@app.on_event("startup")
async def _startup():
    engine.start()
    # Load + warm the YOLO models on a background thread now, so the first
    # camera someone adds starts detecting on its first frame instead of
    # stalling for several seconds while torch initialises.
    if os.environ.get("SAFEX_NO_PRELOAD") != "1":
        try:
            from ..vision.detectors import preload_detector_async
            preload_detector_async()
        except Exception as e:
            print(f"   ⚠ vision preload skipped: {str(e)[:80]}")
    print(f"\n  ⬢ {PRODUCT_NAME} command-centre live at "
          f"http://{HOST}:{PORT}\n")


app.include_router(site_router)


# --------------------------------------------------------------------- static
if (WEB_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=WEB_DIR / "css"), name="css")
if (WEB_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=WEB_DIR / "js"), name="js")
if (WEB_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


def _asset_version() -> str:
    """Short hash of the CSS/JS bundle. Appended to asset URLs so a browser (or
    Render's CDN) can never serve a stale stylesheet after a deploy — the URL
    itself changes whenever the files change."""
    import hashlib
    h = hashlib.md5()
    for sub in ("css", "js"):
        d = WEB_DIR / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*")):
            try:
                h.update(f.read_bytes())
            except Exception:
                pass
    return h.hexdigest()[:10]


@app.get("/", response_class=HTMLResponse)
async def index():
    idx = WEB_DIR / "index.html"
    if idx.exists():
        html = idx.read_text(encoding="utf-8")
        v = _asset_version()
        html = html.replace('href="/css/style.css"', f'href="/css/style.css?v={v}"')
        html = re.sub(r'src="(/js/[a-zA-Z0-9_\-]+\.js)"', rf'src="\1?v={v}"', html)
        return HTMLResponse(html, headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        })
    return HTMLResponse("<h1>SAFEX AI</h1><p>web/index.html not found.</p>")


# ------------------------------------------------------------------ websocket
@app.websocket("/ws")
async def ws_stream(ws: WebSocket):
    """Push the plant snapshot several times a second so a detection shows up
    on the dashboard within a couple of hundred milliseconds."""
    await ws.accept()
    try:
        while True:
            await ws.send_text(json.dumps(engine.snapshot(), default=str))
            await asyncio.sleep(STREAM_SECONDS)
    except WebSocketDisconnect:
        return
    except Exception:
        return


# ------------------------------------------------------------------- REST API
@app.get("/api/state")
async def api_state():
    return JSONResponse(json.loads(json.dumps(engine.snapshot(), default=str)))


@app.get("/api/layout")
async def api_layout():
    return {
        "floors": floor_meta(),
        "zones": [
            {"id": z.id, "name": z.name, "category": z.category, "grid": z.grid,
             "floor": z.floor, "vertical": z.vertical, "rect": z.rect,
             "is_exit": z.is_exit, "critical": z.critical_asset,
             "neighbors": z.neighbors, "hazards": z.hazards} for z in ZONES],
        "markers": [m.as_dict() for m in site_store.site.markers],
        "site": {"name": site_store.site.name, "org": site_store.site.org,
                 "preset": site_store.site.preset, "rev": site_store.rev},
    }


@app.post("/api/scenario")
async def api_scenario(body: dict):
    try:
        return engine.trigger_scenario(
            body["zone"], body["kind"], float(body.get("intensity", 1.0)))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/respond")
async def api_respond(body: dict):
    return engine.deploy_response(body["zone"])


@app.post("/api/resolve")
async def api_resolve(body: dict):
    return engine.resolve_incident(body["zone"])


@app.post("/api/reset")
async def api_reset():
    return engine.reset()


# ---------------------------------------------------------------- cameras / mode
@app.post("/api/mode")
async def api_mode(body: dict):
    return engine.set_mode(body.get("mode", "simulation"))


@app.get("/api/cameras")
async def api_cameras():
    cams = engine.cameras
    return JSONResponse({
        "mode": engine.mode,
        "cameras": cams.summaries() if cams else [],
        "movements": cams.movements() if cams else [],
        "zone_labels": cams.camera_zone_labels() if cams else {},
    })


@app.get("/api/camera/{camera_id}/frame")
async def api_camera_frame(camera_id: str, w: int = 0, q: int = 72):
    from fastapi.responses import Response
    cams = engine.cameras
    stream = cams.streams.get(camera_id) if cams else None
    if stream is None:
        return Response(status_code=404)
    loop = asyncio.get_event_loop()
    jpg = await loop.run_in_executor(_FRAME_POOL, stream.frame_jpeg_scaled,
                                     max(0, min(w, 960)), max(35, min(q, 95)))
    if not jpg:
        return Response(status_code=404)
    return Response(content=jpg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/camera/{camera_id}/stream")
async def api_camera_stream(camera_id: str, request: Request, w: int = 0, q: int = 72):
    """MJPEG feed — one long-lived response instead of a request per frame.

    Polling a JPEG endpoint costs a round trip per frame and is what makes a
    feed look like it is running at 0-5 FPS even when the camera is fine. A
    multipart stream pushes frames as fast as the camera produces them.
    """
    from fastapi.responses import StreamingResponse
    cams = engine.cameras
    stream = cams.streams.get(camera_id) if cams else None
    if stream is None:
        return JSONResponse({"error": "no such camera"}, status_code=404)

    boundary = "safexframe"

    async def gen():
        loop = asyncio.get_event_loop()
        last_seq, idle = -1, 0
        while True:
            # a reloaded tab leaves its old stream behind; without this the
            # server would keep rendering frames for a viewer that has gone
            if await request.is_disconnected():
                return
            seq = stream.frame_seq()
            if seq == last_seq:                 # nothing new yet — don't re-encode
                await asyncio.sleep(0.02)
                idle += 1
                if idle > 1500:                 # ~30 s of silence, give up
                    return
                continue
            last_seq, idle = seq, 0
            # JPEG encoding is CPU work — keep it off the event loop
            jpg = await loop.run_in_executor(
                _FRAME_POOL, stream.frame_jpeg_scaled,
                max(0, min(w, 960)), max(35, min(q, 95)))
            if not jpg:
                await asyncio.sleep(0.05)
                continue
            yield (b"--" + boundary.encode() + b"\r\n"
                   b"Content-Type: image/jpeg\r\n"
                   b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                   + jpg + b"\r\n")

    return StreamingResponse(
        gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "Pragma": "no-cache", "Connection": "close"})


@app.post("/api/camera/{camera_id}/trigger")
async def api_camera_trigger(camera_id: str, body: dict):
    return engine.trigger_camera(camera_id, body.get("kind", "fire"))


@app.post("/api/cameras/config")
async def api_cameras_config(body: dict):
    cams = engine.cameras
    if not cams:
        return JSONResponse({"ok": False, "error": "camera subsystem unavailable"}, status_code=400)
    return cams.configure(body.get("camera_id"), source=body.get("source"),
                          zone_id=body.get("zone_id"), name=body.get("name"))


@app.post("/api/cameras/add")
async def api_cameras_add(body: dict):
    cams = engine.cameras
    if not cams:
        return JSONResponse({"ok": False, "error": "camera subsystem unavailable"}, status_code=400)
    cid = body.get("camera_id") or cams.next_camera_id()
    return cams.add_camera(cid, body.get("name", cid), body.get("zone_id"),
                           source=body.get("source", "synthetic"), persist=True)


@app.post("/api/cameras/remove")
async def api_cameras_remove(body: dict, request: Request):
    cams = engine.cameras
    if not cams:
        return JSONResponse({"ok": False, "error": "camera subsystem unavailable"}, status_code=400)
    # deleting a camera is destructive — leave a trace of who asked
    print(f"[cameras] remove {body.get('camera_id')} requested by "
          f"{getattr(request.client, 'host', '?')} "
          f"ua={request.headers.get('user-agent','?')[:60]} "
          f"ref={request.headers.get('referer','-')}", flush=True)
    res = cams.remove_camera(body.get("camera_id"))
    # clear any CV-driven hazard the deleted camera was leaving in its zone
    if res.get("ok") and res.get("orphaned"):
        zid = res.get("zone")
        engine.cv_flame.pop(zid, None)
        engine.cv_smoke.pop(zid, None)
        engine.cv_people.pop(zid, None)
    return res


@app.get("/api/cameras/devices")
async def api_camera_devices():
    """Probe the machine's local USB camera indices, so the operator can pick a
    real device from a list instead of guessing an index."""
    from ..vision.camera_manager import probe_local_devices
    loop = asyncio.get_event_loop()
    found = await loop.run_in_executor(_FRAME_POOL, probe_local_devices, 4)
    return {"devices": found}


@app.post("/api/cameras/{camera_id}/calibration")
async def api_camera_calibration(camera_id: str, body: dict):
    """Fit this camera's image to the floor plan from four matched points.

    Body: {"points": [{"image":[x,y], "plan":[x,y]}, ... at least 4]}
    Coordinates are normalised 0-1 in each space.
    """
    from ..vision.calibration import fit
    from ..site import site_store
    cams = engine.cameras
    stream = cams.streams.get(camera_id) if cams else None
    if stream is None:
        return JSONResponse({"ok": False, "error": "no such camera"}, status_code=404)

    res = fit((body or {}).get("points") or [])
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)

    def edit(site):
        cam = next((c for c in site.cameras if c.id == camera_id), None)
        if cam is None:
            from ..site.model import CameraDef
            cam = CameraDef(id=camera_id, name=stream.cfg.name,
                            room=stream.cfg.zone_id,
                            source=str(stream.cfg.source))
            site.cameras.append(cam)
        cam.calibration = res["homography"]
    site_store.mutate(edit)
    cams.sync_calibration()
    from ..core.audit import audit
    audit.record("camera.calibrated",
                 f"{stream.cfg.name} mapped to the floor plan "
                 f"(quality {res['quality']})",
                 actor="operator", subject=camera_id,
                 detail={"quality": res["quality"],
                         "reprojection_error": res["reprojection_error"],
                         "points": len((body or {}).get("points") or [])})
    engine.notify()
    return {"ok": True, "camera": camera_id, "quality": res["quality"],
            "reprojection_error": res["reprojection_error"],
            "note": res.get("note", "")}


@app.delete("/api/cameras/{camera_id}/calibration")
async def api_camera_uncalibrate(camera_id: str):
    from ..site import site_store
    def edit(site):
        for c in site.cameras:
            if c.id == camera_id:
                c.calibration = {}
    site_store.mutate(edit)
    cams = engine.cameras
    if cams:
        cams.sync_calibration()
    return {"ok": True, "camera": camera_id}


@app.post("/api/asset/{asset_id}/calibrate")
async def api_asset_calibrate(asset_id: str):
    """Learn a fire-safety asset where it is now, so its absence is noticed."""
    cams = engine.cameras
    if not cams:
        return JSONResponse({"ok": False, "error": "camera subsystem unavailable"},
                            status_code=400)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(_FRAME_POOL, cams.capture_asset_reference, asset_id)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    engine.notify()
    return res


@app.post("/api/door/{door_id}/calibrate")
async def api_door_calibrate(door_id: str):
    """Learn a fire door in its closed position, from the camera watching it."""
    cams = engine.cameras
    if not cams:
        return JSONResponse({"ok": False, "error": "camera subsystem unavailable"},
                            status_code=400)
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(_FRAME_POOL, cams.capture_door_reference, door_id)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    engine.notify()
    return res


@app.post("/api/door/{door_id}/sensor")
async def api_door_sensor(door_id: str, body: dict):
    """Report a magnetic contact sensor reading from the field.

    Real installations put these on fire doors and they are more reliable than
    any camera, so the platform accepts them and fuses the two rather than
    pretending vision is the only possible source of truth.
    """
    cams = engine.cameras
    if not cams:
        return JSONResponse({"ok": False, "error": "camera subsystem unavailable"},
                            status_code=400)
    state = (body or {}).get("state", "")
    if state not in ("open", "closed", "unknown"):
        return JSONResponse({"ok": False,
                             "error": "state must be open, closed or unknown"},
                            status_code=400)
    res = cams.set_door_sensor(door_id, None if state == "unknown" else state)
    engine.notify()
    return res


@app.post("/api/cameras/browser")
async def api_cameras_browser(body: dict):
    """Create / re-point a browser-fed camera onto a zone. The browser then
    streams frames to the returned websocket path (/ws/camera/<camera_id>).
    This is what lets a Render-deployed backend use the VISITOR's webcam."""
    cams = engine.cameras
    if not cams:
        return JSONResponse({"ok": False, "error": "camera subsystem unavailable"}, status_code=400)
    return cams.ensure_browser_camera(body.get("zone_id"),
                                      cam_id=body.get("camera_id", "CAM-WEB"),
                                      name=body.get("name", "Browser Camera"))


@app.websocket("/ws/camera/{camera_id}")
async def ws_camera(ws: WebSocket, camera_id: str):
    """Receive JPEG frames from the visitor's browser, run the existing YOLO
    pipeline on each, and send detections back for on-canvas drawing. Detection
    also feeds the hazard engine (in Live mode) exactly like any other camera."""
    await ws.accept()
    cams = engine.cameras
    stream = cams.streams.get(camera_id) if cams else None
    if stream is None:
        await ws.send_json({"ok": False, "error": f"camera {camera_id} not found"})
        await ws.close()
        return
    loop = asyncio.get_event_loop()
    try:
        while True:
            data = await ws.receive_bytes()                 # one JPEG frame
            # run decode + YOLO off the event loop so streaming stays smooth
            result = await loop.run_in_executor(
                _DETECT_POOL, stream.process_browser_frame, data)
            await ws.send_json(result)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"ok": False, "error": str(e)[:120]})
        except Exception:
            pass


# ------------------------------------------------------------------- actions
@app.get("/api/actions")
async def api_actions():
    """What the platform can do, what it would do now, and what it refuses."""
    from ..core.actions import actions
    return actions.snapshot(engine.snapshot())


@app.post("/api/actions/arm")
async def api_actions_arm(body: dict):
    from ..core.actions import actions
    return actions.arm(bool((body or {}).get("armed", False)))


@app.post("/api/actions/run")
async def api_actions_run(body: dict):
    """Run one action. Anything that affects people needs an explicit confirm,
    and anything touching certified equipment is refused outright."""
    from ..core.actions import actions
    from ..sensors.plant_layout import ZONE_MAP
    b = body or {}
    zone = b.get("zone", "")
    res = actions.run(b.get("action", ""), zone=zone,
                      zone_name=ZONE_MAP[zone].name if zone in ZONE_MAP else zone,
                      confirmed=bool(b.get("confirmed")),
                      automatic=bool(b.get("automatic")),
                      reason=b.get("reason", ""))
    engine.notify()
    if not res.get("ok") and res.get("status") == "refused":
        return JSONResponse(res, status_code=400)
    return res


@app.post("/api/actions/revert")
async def api_actions_revert(body: dict):
    from ..core.actions import actions
    return actions.revert((body or {}).get("action", ""),
                          zone=(body or {}).get("zone", ""))


@app.get("/api/compliance/report", response_class=HTMLResponse)
async def api_compliance_report(period: str = "week"):
    """The periodic compliance report an administrator is asked for.

    Covers a period rather than a moment, reads the tamper-evident log so
    already-cleared findings still appear, and prints the chain verification on
    itself.
    """
    from ..core.report import gather, render_html
    from ..core.audit import audit
    data = gather(engine.snapshot(), period=period)
    audit.record("report.generated",
                 f"{period.title()}ly compliance report generated "
                 f"(score {data['compliance'].get('score')})",
                 actor="operator",
                 detail={"period": period,
                         "open_findings": len(data["live_violations"]),
                         "gaps": len(data["gaps"])})
    return HTMLResponse(render_html(data), headers={"Cache-Control": "no-store"})


@app.get("/api/compliance/report.json")
async def api_compliance_report_json(period: str = "week"):
    from ..core.report import gather
    return JSONResponse(json.loads(json.dumps(
        gather(engine.snapshot(), period=period), default=str)))


# --------------------------------------------------------------------- audit
@app.get("/api/audit")
async def api_audit(limit: int = 100, kind: str = "", day: str = "", since: float = 0.0):
    """The event log, newest first. `kind` matches a prefix, so `compliance.`
    returns every compliance event."""
    from ..core.audit import audit
    return {"entries": audit.entries(limit=max(1, min(limit, 1000)),
                                     kind=kind, day=day, since=since),
            "head": audit.head(), "stats": audit.stats()}


@app.get("/api/audit/verify")
async def api_audit_verify(day: str = ""):
    """Re-walk the hash chain and report the first record that does not match."""
    from ..core.audit import audit
    return audit.verify(day=day)


@app.get("/api/audit/export")
async def api_audit_export(day: str = ""):
    """The raw log, exactly as written, for an inspector to keep."""
    from fastapi.responses import Response
    from ..core.audit import audit, AUDIT_DIR
    import glob
    pattern = f"audit-{day}.jsonl" if day else "audit-*.jsonl"
    files = sorted(AUDIT_DIR.glob(pattern))
    if not files:
        return JSONResponse({"ok": False, "error": "no audit records yet"},
                            status_code=404)
    blob = chr(10).join(f.read_text(encoding="utf-8").strip() for f in files)
    name = f"safex-audit-{day or 'all'}.jsonl"
    return Response(content=blob, media_type="application/x-ndjson",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/copilot")
async def api_copilot(body: dict):
    q = (body.get("question") or "").strip()
    if not q:
        return {"answer": "Ask me about plant status, hazards, evacuation or "
                          "what to do next.", "engine": "local-grounded"}
    return copilot.answer(q)


@app.get("/api/report/{incident_id}", response_class=HTMLResponse)
async def api_report(incident_id: str):
    report = engine.build_report(incident_id)
    if not report:
        return HTMLResponse("<h1>Incident not found</h1>", status_code=404)
    return HTMLResponse(_render_report_html(report))


def _render_report_html(r: dict) -> str:
    inc = r["incident"]
    tl_rows = "".join(
        f"<tr><td>{e['time']}</td><td><span class='k {e['kind']}'>"
        f"{e['kind'].upper()}</span></td><td>{e['message']}</td></tr>"
        for e in inc["timeline"])
    actions = "".join(f"<li>{a}</li>" for a in r["recommended_actions"])
    plant = r.get("plant_context", {}).get("plant", {}) or {}
    org = r.get("plant_context", {}).get("org", "")
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>SAFEX Incident Report {inc['id']}</title>
<style>
  :root{{--ink:#0d1220;--mut:#5b6577;--line:#e4e8f0;--bad:#ff5470;--warn:#ff8c42}}
  *{{box-sizing:border-box}} body{{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;
  color:var(--ink);max-width:820px;margin:40px auto;padding:0 24px;background:#fff}}
  header{{border-bottom:3px solid var(--ink);padding-bottom:16px;margin-bottom:24px;
  display:flex;justify-content:space-between;align-items:flex-end}}
  h1{{margin:0;font-size:22px;letter-spacing:.5px}} .sub{{color:var(--mut);font-size:13px}}
  .badge{{display:inline-block;padding:4px 12px;border-radius:20px;font-weight:700;
  font-size:12px;color:#fff;background:var(--bad)}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px 32px;margin:18px 0}}
  .grid div b{{display:block;color:var(--mut);font-size:11px;text-transform:uppercase;
  letter-spacing:.6px}} .grid div span{{font-size:16px;font-weight:600}}
  h2{{font-size:14px;text-transform:uppercase;letter-spacing:1px;color:var(--mut);
  border-bottom:1px solid var(--line);padding-bottom:6px;margin-top:32px}}
  table{{width:100%;border-collapse:collapse}} td,th{{padding:8px 10px;
  border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
  .k{{font-size:10px;font-weight:700;padding:2px 8px;border-radius:10px;color:#fff;
  background:#5b6577}} .k.detection{{background:#3d7bff}} .k.escalation{{background:var(--warn)}}
  .k.response{{background:#8b5cf6}} .k.resolution{{background:#3ddc97;color:#04331f}}
  ol{{padding-left:20px}} .foot{{margin-top:40px;color:var(--mut);font-size:12px;
  border-top:1px solid var(--line);padding-top:14px}}
  @media print{{body{{margin:0}}}}
</style></head><body>
<header>
  <div><h1>INCIDENT REPORT · {inc['id']}</h1>
  <div class='sub'>{org} &nbsp;•&nbsp; Generated {r['generated_iso']}</div></div>
  <span class='badge'>{inc['severity']}</span>
</header>
<div class='grid'>
  <div><b>Zone</b><span>{inc['zone_name']} ({inc['zone_id']})</span></div>
  <div><b>Hazard type</b><span>{inc['hazard_kind'].title()}</span></div>
  <div><b>Opened</b><span>{inc['opened_iso']}</span></div>
  <div><b>Duration</b><span>{inc['duration_s']} s</span></div>
  <div><b>Peak hazard score</b><span>{inc['peak_score']} / 100</span></div>
  <div><b>Peak occupancy</b><span>{inc['peak_occupancy']} workers</span></div>
</div>
<h2>Probable Root Cause</h2><p>{r['root_cause']}</p>
<h2>Incident Timeline</h2>
<table><thead><tr><th>Time</th><th>Phase</th><th>Detail</th></tr></thead>
<tbody>{tl_rows}</tbody></table>
<h2>Recommended Corrective Actions</h2><ol>{actions}</ol>
<div class='foot'>Auto-generated by {PRODUCT_NAME} · plant hazard index at close:
{plant.get('score','—')} ({plant.get('band','—')}). This report is produced for
operational review and is not a substitute for a formal RCA investigation.</div>
</body></html>"""


def run():
    import uvicorn
    # honour the platform-provided PORT (Render/Railway/Heroku set $PORT and
    # require binding 0.0.0.0); fall back to the local settings otherwise.
    host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else HOST)
    port = int(os.environ.get("PORT", PORT))
    uvicorn.run(app, host=host, port=port, log_level="warning")
