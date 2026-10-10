"""Periodic compliance report.

The thing a hospital administrator is actually asked for: a document that says
whether this building met its fire-safety obligations over a period, what it
failed on, for how long, and whether the record can be trusted.

Three properties make it useful rather than decorative:

* **It covers a period, not a moment.** A live dashboard says the corridors are
  clear right now, which tells an inspector nothing about last Tuesday. The
  report reads the audit log, so a violation that was opened and cleared three
  days ago is still in it.
* **It says what is not covered.** An uncalibrated fire door and a room with no
  camera are gaps in the monitoring, and a report that quietly omits them
  overstates how much of the building is actually watched. They get their own
  section.
* **It carries its own verification.** The audit chain is re-walked as the
  report is generated and the result is printed on it, so the document states
  whether the records behind it are intact.
"""
from __future__ import annotations
import html
import time
from typing import Dict, List

from .audit import audit

PERIODS = {"day": 86400, "week": 7 * 86400, "month": 30 * 86400,
           "quarter": 90 * 86400}


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h {(seconds % 3600) // 60} min"
    return f"{seconds // 86400} d {(seconds % 86400) // 3600} h"


def gather(snapshot: Dict, period: str = "week") -> Dict:
    """Collect everything the report needs, from live state plus the log."""
    window = PERIODS.get(period, PERIODS["week"])
    since = time.time() - window
    events = audit.entries(limit=5000, since=since)

    opened = [e for e in events if e["kind"] == "compliance.violation.opened"]
    incidents = [e for e in events if e["kind"] == "incident.opened"]
    resolved = [e for e in events if e["kind"] == "incident.resolved"]
    actions = [e for e in events if e["kind"] == "action.taken"]
    drills = [e for e in events if e["kind"] == "scenario.injected"]

    compliance = snapshot.get("compliance", {}) or {}
    live = compliance.get("violations", []) or []
    closed = compliance.get("history", []) or []

    # findings by where they were, so a floor with a chronic problem is obvious
    by_room: Dict[str, Dict] = {}
    for e in opened:
        room = e.get("subject") or "?"
        d = e.get("detail", {}) or {}
        entry = by_room.setdefault(room, {"room": room, "count": 0,
                                          "worst": "NARROWED", "floor": d.get("floor", 0),
                                          "types": set()})
        entry["count"] += 1
        entry["types"].add(d.get("type", "?"))
        rank = {"NARROWED": 1, "OBSTRUCTED": 2, "BLOCKED": 3}
        if rank.get(d.get("severity", ""), 0) > rank.get(entry["worst"], 0):
            entry["worst"] = d.get("severity", entry["worst"])
    repeat = sorted(by_room.values(), key=lambda r: -r["count"])

    # what is NOT being watched — the honest part
    gaps: List[Dict] = []
    cams = snapshot.get("cameras", []) or []
    rooms_with_cams = {c.get("zone_id") for c in cams}
    for z in snapshot.get("zones", []) or []:
        if z["zone_id"] not in rooms_with_cams and z.get("occupancy", 0) >= 4:
            gaps.append({"kind": "no camera", "where": z["name"],
                         "detail": f"{z['occupancy']} people typically present, "
                                   f"no camera covering it"})
    for d in snapshot.get("doors", []) or []:
        if d.get("state") == "UNCALIBRATED":
            gaps.append({"kind": "fire door not calibrated",
                         "where": f"{d.get('label') or d.get('door')} · {d.get('room_name','')}",
                         "detail": "no closed-position reference captured, so its "
                                   "state is not being monitored"})
    for a in snapshot.get("assets", []) or []:
        if a.get("state") == "UNCALIBRATED":
            gaps.append({"kind": "asset not calibrated",
                         "where": f"{a.get('label') or a.get('asset')} · {a.get('room_name','')}",
                         "detail": "not yet learnt in place, so its removal would "
                                   "not be noticed"})

    return {
        "period": period,
        "from": time.strftime("%Y-%m-%d %H:%M", time.localtime(since)),
        "to": time.strftime("%Y-%m-%d %H:%M"),
        "site": snapshot.get("meta", {}).get("org", "Site"),
        "compliance": compliance,
        "live_violations": live,
        "closed_violations": closed,
        "opened_count": len(opened),
        "incidents": incidents,
        "resolved": resolved,
        "actions": actions,
        "drills": drills,
        "repeat_offenders": [{**r, "types": sorted(r["types"])} for r in repeat[:8]],
        "gaps": gaps,
        "assets": snapshot.get("assets", []) or [],
        "doors": snapshot.get("doors", []) or [],
        "verification": audit.verify(),
        "head": audit.head(),
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_html(r: Dict) -> str:
    e = html.escape
    c = r["compliance"]
    band_color = {"COMPLIANT": "#1a7f4b", "MINOR": "#a67c00",
                  "MAJOR": "#c2410c", "CRITICAL": "#b91c1c"}
    score = c.get("score", 100)
    band = c.get("band", "COMPLIANT")

    floors = "".join(
        f"<tr><td>{e(f['name'])}</td><td class='num'>{f['score']}</td>"
        f"<td><span class='band' style='background:{band_color.get(f['band'],'#555')}'>"
        f"{f['band']}</span></td><td class='num'>{f['violations']}</td></tr>"
        for f in c.get("by_floor", []))

    def viol_rows(items, live=True):
        if not items:
            return ("<tr><td colspan='5' class='none'>None in this period.</td></tr>")
        out = []
        for v in items:
            age = _fmt_duration(v.get("duration_s", 0))
            out.append(
                f"<tr><td><span class='sev {v['severity'].lower()}'>{v['severity']}</span></td>"
                f"<td>{e(v.get('room_name',''))}</td>"
                f"<td>{e(v.get('detail',''))}</td>"
                f"<td>{e(v.get('summary','') or '')}</td>"
                f"<td class='num'>{age}</td></tr>")
        return "".join(out)

    repeats = "".join(
        f"<tr><td>{e(x['room'])}</td><td class='num'>{x['count']}</td>"
        f"<td>{e(', '.join(x['types']))}</td>"
        f"<td><span class='sev {x['worst'].lower()}'>{x['worst']}</span></td></tr>"
        for x in r["repeat_offenders"]) or \
        "<tr><td colspan='4' class='none'>No repeated findings.</td></tr>"

    gaps = "".join(
        f"<tr><td>{e(g['kind'])}</td><td>{e(g['where'])}</td><td>{e(g['detail'])}</td></tr>"
        for g in r["gaps"]) or (
        "<tr><td colspan='3' class='none'>Every occupied room is covered and "
        "every asset is calibrated.</td></tr>")

    assets = "".join(
        f"<tr><td>{e(a.get('label',''))}</td><td>{e(a.get('room_name',''))}</td>"
        f"<td><span class='sev {'ok' if a.get('state')=='OK' else 'obstructed'}'>"
        f"{a.get('state','?')}</span></td></tr>"
        for a in r["assets"]) or (
        "<tr><td colspan='3' class='none'>No fire-safety assets are being "
        "monitored yet.</td></tr>")

    ver = r["verification"]
    ver_html = (
        f"<div class='verify ok'><b>✓ Audit chain intact</b> — {ver['records']} records "
        f"verified. {e(ver['note'])}</div>" if ver["ok"] else
        f"<div class='verify bad'><b>⚠ Audit chain broken</b> at record "
        f"{ver.get('broken',{}).get('seq')} — {e(str(ver.get('broken',{}).get('reason','')))}. "
        f"{e(ver['note'])}</div>")

    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Fire Safety Compliance Report · {e(r['site'])}</title>
<style>
 :root{{--ink:#12161f;--mut:#5b6577;--line:#e3e7ee}}
 *{{box-sizing:border-box}}
 body{{font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);
   max-width:960px;margin:36px auto;padding:0 26px;background:#fff}}
 header{{border-bottom:3px solid var(--ink);padding-bottom:18px;margin-bottom:8px;
   display:flex;justify-content:space-between;align-items:flex-end;gap:20px}}
 h1{{margin:0;font-size:23px;letter-spacing:.4px}}
 .sub{{color:var(--mut);font-size:13px;margin-top:4px}}
 .headline{{text-align:right}}
 .headline .score{{font-size:44px;font-weight:800;line-height:1}}
 .headline .band{{font-size:12px;font-weight:700;letter-spacing:1px}}
 h2{{font-size:13px;text-transform:uppercase;letter-spacing:1.1px;color:var(--mut);
   border-bottom:1px solid var(--line);padding-bottom:6px;margin:30px 0 10px}}
 table{{width:100%;border-collapse:collapse;font-size:13px}}
 th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.7px;
   color:var(--mut);border-bottom:1px solid var(--line);padding:7px 9px}}
 td{{padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}}
 td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
 .none{{color:var(--mut);font-style:italic}}
 .band,.sev{{display:inline-block;padding:2px 9px;border-radius:11px;color:#fff;
   font-size:10.5px;font-weight:700;letter-spacing:.4px}}
 .sev.narrowed{{background:#a67c00}} .sev.obstructed{{background:#c2410c}}
 .sev.blocked{{background:#b91c1c}} .sev.ok{{background:#1a7f4b}}
 .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0 4px}}
 .kpi{{border:1px solid var(--line);border-radius:9px;padding:11px 13px}}
 .kpi b{{display:block;font-size:22px;line-height:1.2}}
 .kpi span{{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.6px}}
 .verify{{margin:14px 0;padding:11px 14px;border-radius:9px;font-size:12.5px}}
 .verify.ok{{background:#eefaf2;border:1px solid #b7e3c8}}
 .verify.bad{{background:#fdecec;border:1px solid #f0b4b4}}
 .note{{color:var(--mut);font-size:12px;margin-top:8px}}
 .foot{{margin-top:38px;color:var(--mut);font-size:11.5px;border-top:1px solid var(--line);
   padding-top:14px}}
 code{{background:#f4f6f9;padding:1px 5px;border-radius:4px;font-size:11.5px}}
 @media print{{body{{margin:0}} .verify{{break-inside:avoid}}}}
</style></head><body>
<header>
  <div>
    <h1>Fire Safety Compliance Report</h1>
    <div class='sub'>{e(r['site'])}<br>{e(r['from'])} → {e(r['to'])} · {e(r['period'])}ly report</div>
  </div>
  <div class='headline'>
    <div class='score' style='color:{band_color.get(band,"#555")}'>{score}</div>
    <div class='band' style='color:{band_color.get(band,"#555")}'>{band}</div>
  </div>
</header>

{ver_html}

<div class='kpis'>
  <div class='kpi'><b>{r['opened_count']}</b><span>Violations raised</span></div>
  <div class='kpi'><b>{len(r['live_violations'])}</b><span>Still open</span></div>
  <div class='kpi'><b>{len(r['incidents'])}</b><span>Fire incidents</span></div>
  <div class='kpi'><b>{len(r['gaps'])}</b><span>Monitoring gaps</span></div>
</div>

<h2>Compliance by floor</h2>
<table><thead><tr><th>Floor</th><th class='num'>Score</th><th>Status</th>
<th class='num'>Open findings</th></tr></thead><tbody>{floors}</tbody></table>

<h2>Open findings — action required</h2>
<table><thead><tr><th>Severity</th><th>Location</th><th>Finding</th><th>Detail</th>
<th class='num'>Open for</th></tr></thead><tbody>{viol_rows(r['live_violations'])}</tbody></table>

<h2>Findings closed during this period</h2>
<table><thead><tr><th>Severity</th><th>Location</th><th>Finding</th><th>Detail</th>
<th class='num'>Lasted</th></tr></thead><tbody>{viol_rows(r['closed_violations'], False)}</tbody></table>

<h2>Repeated findings — where the same problem keeps coming back</h2>
<table><thead><tr><th>Room</th><th class='num'>Times raised</th><th>Type</th>
<th>Worst</th></tr></thead><tbody>{repeats}</tbody></table>

<h2>Fire-safety assets</h2>
<table><thead><tr><th>Asset</th><th>Location</th><th>State</th></tr></thead>
<tbody>{assets}</tbody></table>

<h2>What this report does not cover</h2>
<p class='note'>Monitoring gaps are listed rather than omitted. A room with no
camera and a fire door with no captured reference are not compliant or
non-compliant — they are simply unobserved, and an inspection should treat them
as such.</p>
<table><thead><tr><th>Gap</th><th>Where</th><th>Why it matters</th></tr></thead>
<tbody>{gaps}</tbody></table>

<div class='foot'>
Generated automatically by SAFEX AI on {e(r['generated'])} from the tamper-evident
event log. Audit chain head: <code>{e(r['head']['head'][:32])}…</code><br>
This is an automated monitoring record intended to support — not replace — the
statutory fire-safety inspections and the certified detection and suppression
systems installed in this building.
</div>
</body></html>"""
