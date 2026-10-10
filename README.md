<div align="center">

# ⬢ SAFEX AI
### Industrial Safety Intelligence Platform

**Real-time hazard intelligence for manufacturing plants — fusing computer vision and an IoT sensor mesh into live risk scoring, predictive analysis, automated incident management, intelligent evacuation routing and an AI Safety Copilot.**

*ET AI Hackathon 2026 · Problem Statement 1 · Industrial Safety Intelligence*

</div>

---

## What it is

Industrial plants generate a flood of safety signals — flame detectors, gas
sensors, thermal readings, camera feeds, headcounts — but they sit in silos.
When something goes wrong, operators are left correlating alarms by hand while
the clock runs.

**SAFEX AI is the intelligence layer that sits on top of all of it.** It fuses
every signal into a single, explainable **Hazard Score** per zone, predicts
where risk is *heading*, opens and documents incidents automatically, routes
every worker to the safest exit, and lets an operator simply *ask* the AI
Safety Copilot what's happening and what to do next.

It is built to look and behave like software that could be deployed in a real
plant control room today — while running entirely on a laptop with **one
command and zero hardware**.

## Screenshots

<p align="center">
  <img src="screenshots/dashboard.png" alt="SAFEX AI dashboard in nominal simulation mode" width="49%" />
  <img src="screenshots/fire-response.png" alt="SAFEX AI dashboard responding to a simulated fire in the Canteen" width="49%" />
</p>

<p align="center"><em>Nominal monitoring (left) and a simulated fire response with live risk scoring and alerts (right).</em></p>

---

## Against the brief

| Required capability | Where it is |
|---|---|
| Monitor existing CCTV feeds | USB, IP-Webcam, RTSP/CCTV, video file, or the visitor's own browser camera — several per room |
| Presence and accessibility of extinguishers and exit signage | Reference-learnt presence, approach-zone accessibility, unlit-sign check |
| Blocked evacuation routes and obstructed corridors | Named objects + background change (catches debris nothing can classify) + crowding, all gated on persistence |
| Open/closed status of fire doors | Reference comparison, optional contact-sensor fusion, held-open timing |
| Smoke and flame detection | YOLOv8 on every feed, flame called on the first frame |
| Integrate multiple sensing technologies | IoT sensor mesh + computer vision + door contacts fused into one hazard score |
| Real-time alerts | Spoken, de-duplicated, with the recommended action |
| Periodic compliance reports | `▤ Report` — covers a period, states its own gaps, verifies its own records |
| Secure event logs with timestamps | Append-only, hash-chained, tamper-evident, exportable |
| Safe limited automated actions | Advisory equipment only; certified systems refused by design, disarmed by default, every attempt logged |

## Run it (60 seconds, no hardware)

```bash
pip install -r requirements.txt
python run.py
```

The command centre opens at **http://127.0.0.1:8000**. That's it — the plant
digital twin, sensor mesh and AI are all running locally in
**sensor-simulation mode**. No camera, GPU, database, cloud account or API key
required.

> **Live camera mode (optional).** To drive the platform from a real webcam /
> IP camera using the bundled YOLOv8 fire & people models:
> ```bash
> pip install ultralytics opencv-python numpy pyttsx3
> python run.py --camera
> ```

---

## The 90-second demo

1. The dashboard boots into a calm **green plant** — every room on every floor
   nominal. Three floor tabs sit above the map: **G · L1 · L2**.
2. In the **Scenario Console** (right), pick `Canteen` (Level 2), hit **🔥 Fire**.
3. Watch the platform react — within **under a second**:
   - the view **jumps to Level 2** and the Canteen pulses red;
   - the **Plant Hazard Index** climbs and the band shifts to HIGH → CRITICAL;
   - an **incident opens** with a timestamped timeline;
   - the **evacuation route animates across floors** — Canteen → corridor →
     **Stair Core A** → down two levels → East Muster Point — with worker dots
     streaming toward the stairs and a `↓ G` badge on the landing;
   - a **smart alert** fires and is **spoken aloud**;
   - the **predictive watchlist** flags neighbouring rooms before they escalate.
4. Ask the **AI Safety Copilot**: *"What should I do now?"* — it answers from
   live plant state, in priority order.
5. Click **Deploy response** on the incident → suppression begins, the score
   falls, the incident **auto-resolves**, and **MTTR** updates.
6. Click **Report** → a filing-ready **incident report** is generated instantly.

A full script is in [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md).

---

## The building

The plant is a three-storey site. Every floor is a grid of rooms; rooms that
share a wall share a doorway, and the evacuation graph is derived from that
grid — so the map and the routing can never disagree. Two stair cores (**A**
and **B**) run the full height of the building and are the only way between
levels; the four muster points are all at ground level.

| Floor | What's on it | Rooms |
|---|---|---|
| **G · Ground** | Process · Utilities · Egress | Reactor Halls A/B, Distillation, Compressor Bay, Substation, Solvent Storage, Chemical Warehouse, Boiler Room, Pump House, Gas Cylinder Yard, Fire Pump House, … + 4 muster points |
| **L1 · Level 1** | Logistics · Labs · Maintenance | Packaging Line, Bottling Hall, Quality Lab, Finished Goods, Maintenance Shop, Paint & Solvent Booth, Battery/UPS Room, … |
| **L2 · Level 2** | Control · Admin · Welfare | Control Room, Server/IT Room, Admin Offices, Canteen, Kitchen, Training Room, HSE Office, … |

Consequences of a real building, all modelled:

- **Evacuation descends.** Stairs cost more than corridors, so routes stay on a
  floor until they must drop; the drawer shows every hop with a `↓ G` marker at
  the floor change, and crowds on upper floors take proportionally longer to clear.
- **A blocked stair matters.** If a stair core is impassable, routes re-plan
  through the other core — and if both are gone, the zone is flagged as blocked
  egress and the alert says so.
- **Floors are never hidden.** Each tab carries its floor's worst score, band
  colour and headcount, and the map auto-follows a level that goes critical
  (switch it off with **AUTO-FOLLOW HAZARD** under the map).

---

## Draw your own building — the Site Editor

The building is **data, not code**. Open **⌂ Site** in the top bar and you get a
plan editor over the live platform:

- **Floors** — add, rename or delete levels. The stair cores link them, and the
  evacuation router walks people down and out.
- **Upload the real floor plan** (PNG/JPG) as a tracing backdrop, then drag
  rooms out over it. **✨ Auto-detect rooms** reads the drawing and proposes the
  enclosed regions it can find — you accept the ones that are right and adjust
  the rest. (No plan reader gets every drawing correct; nothing is added until
  you say so.)
- **Rooms carry their meaning** — category, typical occupancy, which hazards
  they are prone to, critical-asset and muster-point flags.
- **Doorways come from the geometry.** Rooms that share a wall are connected,
  so the map you drew and the graph the router walks can never disagree. A
  stubborn plan can be corrected with a forced link or a wall-off.
- **Fire-safety assets** — place extinguishers, exit signs, fire doors, hydrants,
  alarm points and monitored egress-route corridors directly on the plan.
- **Validation runs on every save** — a room with no route to a muster point is
  called out immediately, and deleting the last exit (or the floor holding all
  of them) is refused unless you force it. Every save is backed up.

Changes are live in a few hundred milliseconds — no restart. A room drawn now is
monitored now: give it a camera, inject a fire, and it opens an incident and
routes an evacuation like any other room.

Two presets ship in the box: **🏥 Hospital** (the default — wards, theatres, ICU,
oxygen manifold, kitchen) and **🏭 Plant** (the original industrial site).

## Cameras

- **Several cameras per room.** A ward covered from both ends, a corridor watched
  from two directions — detections are merged per room (worst fire confidence
  wins; headcount takes the best single view rather than the sum, so two cameras
  covering one ward don't count the same patients twice).
- **Any source** — USB index, IP-Webcam URL, RTSP/CCTV stream, a video file, the
  visitor's own browser camera, or a synthetic demo feed.

## Egress compliance — is the escape route actually usable?

A fire alarm tells you something is burning. This tells you the building would
fail an inspection *today*, while there is still time to fix it — which is the
whole point of continuous compliance monitoring.

Each monitored corridor is drawn on the plan as an **egress route** marker, and
the cameras in that room judge it two independent ways:

| How | Catches |
|---|---|
| **Named objects** — the person model already runs for flame detection, so its other classes come free | Beds and stretchers parked in corridors, chairs, visitors' luggage, planters, equipment left out |
| **Change from the corridor's learnt normal** (MOG2 per-pixel background) | Everything with no class at all — cartons, linen trolleys, an oxygen cylinder, debris brought down by a fire. *Something is there that should not be*, even when nothing can name it |
| **Dwell** on person boxes | People standing in the route rather than walking through it |
| **Smoke / flame in the route** | A corridor that is physically clear but unusable |

Every reading is gated on **persistence** — a nurse walking through is traffic,
a trolley left for two minutes is a violation. That gate is what makes this a
compliance signal rather than a stream of noise.

Severity runs **CLEAR → NARROWED → OBSTRUCTED → BLOCKED**, and it is not just a
label: an obstructed route becomes expensive for the evacuation router and a
blocked one becomes impassable, so people are routed around a corridor full of
stored beds exactly as they are routed around a fire. Verified: the NICU
normally leaves via the ward corridor; block that corridor and the route
switches to `W4 → W9 → Stair A → down → East Assembly Point`.

Each floor carries a compliance score, the site carries the average, and every
violation names the room, what is in the way, how long it has been there and
what to do about it.

**Measured behaviour** (synthetic corridors, real duty cycle):

| Scenario | Result |
|---|---|
| Empty corridor, 10 minutes, webcam-grade noise | no false alarms |
| Ward lights switched on and left on | absorbed, never raised |
| Stretcher parked | PENDING at first sight → **NARROWED confirmed at 12 s** |
| …still there 10 minutes later | still reported (the model freezes once a violation is confirmed, so a permanent obstruction can never fade into "normal") |
| Debris with no recognisable class | **caught** as "an unidentified object" |
| Camera nudged / exposure jump | reported as *camera view changed*, not as a blocked corridor |

**Cost.** The expensive part — YOLO — runs anyway for flame detection; the
corridor check adds a few milliseconds on top. It runs on a slow duty cycle
(every 4 s) while the building is calm, because a parked trolley is still
parked four seconds later, and switches to **every frame** the moment fire or
smoke is involved, when the question stops being "is this a violation" and
becomes "can people still get out".

**What it does not claim.** It cannot say *what* unidentified debris is — only
that something is there that should not be. Clear width in metres needs the
floor plan's scale (see calibration below); without it the platform reports the
fraction of the monitored area lost and says so, rather than inventing a
measurement.

## Fire doors — the most-propped-open thing in any hospital

A fire door only works closed. Wedged open by a doorstop, a chair or a stacked
box, it is a hole in the compartmentation and smoke goes straight through it.

**A door being open is not a violation** — people walk through doors all day.
The violation is a door that *stays* open, so the platform measures how long
and raises it past a threshold set per door (30 s by default). During an alarm
that grace period collapses to a few seconds, because the compartment is
failing right then.

Reading the state without a dataset of that hospital's doors:

- **Reference comparison.** The operator captures each door closed, once, from
  the camera that watches it. Every check compares the door region against that
  reference using mean-subtracted correlation plus edge structure — both chosen
  because they survive lighting changes, which raw pixels do not.
- **A contact sensor, if there is one.** Real installations put a magnetic reed
  switch on fire doors and it beats any camera. When one is wired in it is
  authoritative — and when it contradicts the camera the platform reports a
  **DISPUTED** state rather than quietly picking a side, because a switch and a
  camera disagreeing means one of them is broken.

Measured separation on the closed state: identical view **0.95**, ward lights
raised **0.85**, someone standing in front of the door **0.90**, camera nudged
two pixels **0.84** — against a door merely **ajar at 20° scoring 0.59**. A gap
is a failure; smoke does not wait for the door to be wide open.

An open fire door does **not** re-route anybody — it is not a physical
obstruction. What it does is breach the compartment, so smoke from that room is
carried into its neighbours instead of being held back, which the hazard field
then reflects. Blocked corridors re-route; open doors spread smoke. Two
different failures, modelled as two different things.

Each door starts as **NOT CALIBRATED** and says so, because a door nobody has
captured is a door nobody is monitoring, and pretending otherwise would leave a
silent gap in the audit.

## Calibration — teaching a camera where it is

Everything a camera knows is in its own pixels: a person is at (312, 208), a
corridor is "41% obstructed". Everything an operator works with is on the plan:
a room, a corridor, a width in metres. Calibration is the bridge.

Open **◱ Cameras → ⊹** on any camera and click four spots you can identify in
both views — corridor corners, a doorway, floor tiles. The server fits a
**homography** between the two planes, which is exactly the right model: a floor
is flat, a plan is flat, and a camera looking at a flat surface produces a
projective transform of it. Nothing has to be measured and no marker has to be
printed.

Record the floor's real width once (**⌂ Site → floor → width in metres**) and
three things change:

| Before calibration | After |
|---|---|
| "~41% of the monitored area is obstructed" | "~41% obstructed — roughly **5.0 m of 8.4 m** clear width remaining" |
| Detections stay inside the camera tile | People and objects appear **on the floor plan at their real position** |
| Every door and asset needs its region drawn by hand | A rectangle drawn on the plan **projects itself into the camera view** |

The fit is validated rather than trusted: four points define a homography
exactly, so a zero reprojection error proves nothing on its own. The platform
checks that the mapping stays invertible, that the frame corners land somewhere
sane on the plan, and that the points form a convex shape in a consistent order
— and refuses points that are collinear, clicked in a zigzag, or that send part
of the view to infinity, with a message saying which.

Calibration is optional. Everything else in the platform works without it; what
it buys is the difference between percentages and metres, and between "a camera
saw someone" and "someone is standing here".

## Extinguishers and exit signs

The brief asks for *presence and accessibility*, and those are two different
questions. Presence: is it still there — extinguishers get borrowed, taken for
servicing, moved when a ward is repainted, and an empty bracket is invisible
until the day it matters. Accessibility: can somebody reach it in three seconds
while a bin is burning — one behind a stack of cartons is as good as absent, and
that is the more common finding by far.

There is no COCO class for a fire extinguisher and no dataset of this hospital's
signage, so presence is read the way the fire doors are: the operator captures
the asset once, in place, and every check compares against that reference.
Unglamorous, works on any building without training anything — and unlike a
detector it cannot hallucinate an extinguisher that was never there.
Accessibility is measured from the detection boxes overlapping the approach
zone in front of it, which also names what is in the way.

Exit signs get one extra check: a sign that is present, unobstructed and **dark**
is a failed sign. Their brightness is compared with the reference too.

Findings land in the same compliance stream as everything else — `BLOCKED`
(access obstructed), `MISSING` (not where it should be), `UNLIT` — each with the
room, the camera that saw it, how long it has been like that and what to do.
Like the doors, an uncalibrated asset reports itself as **UNCALIBRATED** rather
than quietly passing.

## The compliance report

`▤ Report` on the compliance card, or `GET /api/compliance/report?period=week`.
Three things make it worth handing to an administrator:

* **It covers a period, not a moment.** The live dashboard says the corridors
  are clear *now*, which tells an inspector nothing about last Tuesday. The
  report reads the audit log, so a finding that was raised and cleared three
  days ago is still in it — with how long it lasted.
* **It says what it does not cover.** Rooms with no camera, fire doors with no
  captured reference, assets never learnt in place — all listed under *"What
  this report does not cover"*. They are not compliant or non-compliant, they
  are unobserved, and a report that quietly omitted them would overstate how
  much of the building is actually watched.
* **It verifies itself.** The audit chain is re-walked as the report is
  generated and the result is printed at the top, along with the chain head.

It also surfaces **repeated findings** — the same corridor blocked eleven times
this month is a process problem, not eleven incidents.

## Automated actions — and the line they never cross

The brief asks for automated actions *"without interfering with certified fire
protection systems"*, and that clause is the whole design rather than a footnote.

A hospital's fire alarm panel, its detectors, its sprinklers and its magnetic
hold-opens are **certified life-safety equipment**: third-party tested,
statutorily inspected, and required to work when everything else has failed —
including this platform. So SAFEX never writes to them. Asking it to is not an
error case, it is a refusal with a reason, and the refusal is logged:

```
trigger_fire_panel  -> refused: The building's fire alarm panel is certified
                       equipment. SAFEX never actuates or silences it.
release_sprinklers  -> refused: Suppression is certified equipment and is never
                       actuated by a monitoring system.
silence_alarm       -> refused: Silencing an alarm is a decision for the
                       responsible person at the panel, never for software.
```

What it *does* drive is its own advisory equipment on the other side of that
line — its own sounder, its own door hold-opens (power-to-hold, so any failure
including this platform dying lets the door close), way-finding indicators, PA
announcements and responder notifications. Every one of them fails safe.

Governance is deliberate:

| | |
|---|---|
| Automation is **off until armed** | an automatic action attempted while disarmed is refused and logged |
| Some actions **never** run alone | the PA announcement requires a human every time, armed or not |
| Anything affecting occupants **asks first** | with the device and its fail-safe behaviour shown in the prompt |
| Everything is visible **before** it happens | the console shows what would fire, not what already did |
| Every attempt is recorded | fired, refused, or blocked because the platform was disarmed |

## The audit log — what happened, and can you prove it

An inspector never asks what is happening now. They ask what happened on the
night of the 14th, and whether the record has been edited since. That is a
different job from the live dashboard, so it gets its own store: append-only
JSONL, one file per day, under `data/audit/`.

Every record carries the hash of the record before it, so each entry commits to
the whole history in front of it:

```
seq 7  incident.resolved   prev=a91f…  hash=2e9f…
seq 8  compliance.violation.opened   prev=2e9f…  hash=71bc…
```

Edit one field of an old line and every hash after it stops matching.
`GET /api/audit/verify` re-walks the chain and reports exactly where it breaks:

| | Result |
|---|---|
| Untouched log | `ok: true · 9 records · "nothing has been edited or removed in place"` |
| One old record quietly rewritten | `ok: false · broken at seq 8 · "record contents do not match its hash" · 7 records still intact` |

What gets recorded: incidents opened, acknowledged and resolved · compliance
violations opened and closed · mode changes · injected drills · plant resets ·
cameras added, removed, reconfigured and calibrated · fire doors calibrated ·
every save of the site plan · every operator action.

**Be precise about what this gives you.** It is tamper-*evident*, not
tamper-proof: somebody with write access to both the file and this source could
rewrite every record and recompute the whole chain. What closes that gap in a
real deployment is anchoring — periodically publishing the head hash somewhere
the operator does not control. `GET /api/audit` returns that head hash for
exactly this purpose, with the advice attached. Claiming more would be worse
than useless in an audit, because it would be believed.

`GET /api/audit/export` hands over the raw log for an inspector to keep.

## Responsiveness

Detection latency was the thing worth engineering, so it is worth stating plainly:

| Path | Latency |
|---|---|
| Injected scenario → zone CRITICAL, incident open, alert spoken | **< 1 s** |
| Camera calls fire → hazard field, evacuation and alerts updated | **~50 ms** |
| Anything above → pixels on the operator's screen | **≤ 250 ms** (4 Hz push) |

How it gets there:

- The control loop runs at **5 Hz** and integrates sensor physics against real
  elapsed time, so the loop rate can change without altering how an incident
  develops. A camera that flips its alarm state **wakes the loop immediately**
  rather than waiting for the next pass.
- **Capture and inference are separate threads** per camera. The feed runs at
  the camera's own frame rate (≈25 FPS on a typical webcam) while the detector
  chews on the newest frame it can get — slow inference costs box freshness,
  never video smoothness.
- Detections **latch**: the first frame a flame appears in raises the alarm, and
  it is held briefly afterwards so a wobble in model confidence cannot make the
  alarm chatter.
- **Your own camera streams the same way.** Frames pushed from a browser are
  stored and acknowledged immediately (~6 ms round trip); inference runs on the
  camera's own thread and simply takes the newest frame, skipping whatever
  arrived while it was busy. Running the model inside the send/reply loop
  capped the feed at ~3 FPS and made the picture drift further behind reality
  the longer it ran — now it holds 12-15 FPS and cannot fall behind, because
  nothing queues.
- Feeds are **MJPEG streams**, not one HTTP request per frame. The tiles you are
  looking at get real streams; the rest fall back to light polling, so the
  browser's connection limit can never starve the API or the websocket.

## Adding a camera

**◱ Cameras → ＋ Add camera / room.** Give it a name, pick any room on any floor,
and either paste a source or click one of the **USB devices detected on this
machine** (the server probes them for you).

Adding is **non-blocking**: the API returns immediately and the device is opened
on a worker thread with retry/backoff — so the dashboard never freezes, a camera
plugged in later starts working on its own, and a source that cannot be opened
says **OFFLINE** with the reason on the tile instead of sitting at 0 FPS. On
Windows, DirectShow is tried first because the default backend can take many
seconds to open a webcam. Each tile shows real capture FPS **and** detections
per second.

---

## What's inside

| Capability | What it does |
|---|---|
| **Risk Intelligence Engine** | Fuses gas, temperature, smoke, vibration, flame (CV) and occupancy into an explainable 0–100 Hazard Score per zone, with a per-factor breakdown. Consequence-aware: crowded and critical-asset zones weigh heavier. |
| **Multi-Floor Plant Map** | A control-room heat-map of the whole **three-storey building** — 49 rooms, two stair cores and 4 muster points across Ground / Level 1 / Level 2. One tab per floor, each showing that level's worst hazard and headcount, so a fire upstairs is never invisible. The view auto-follows whichever level is in trouble. |
| **Predictive Risk Analysis** | Projects each zone's score forward from its recent trajectory and raises a watchlist *before* thresholds are crossed. |
| **Intelligent Evacuation Router** | Hazard-aware A\* over the building graph — routes bend around fire/gas, **descend the stair cores** to reach ground-level muster points, and re-plan continuously; flags blocked egress and blocked stairs. |
| **Automated Incident Management** | Detects → escalates → tracks response → resolves, with a full audit timeline and one-click **incident report** generation. |
| **Smart Alerts + Voice** | De-duplicated, prioritised alerts with cooldowns; critical alerts are spoken via the browser (no OS TTS needed). |
| **AI Safety Copilot** | A conversational safety officer grounded in live plant state — always answers offline; optionally upgrades to LLaMA-3.3 via Groq. |
| **Safety Analytics** | Executive KPIs: Safety Index, active incidents, people at risk, workers protected, MTTR, monitoring uptime. |
| **Mock IoT Sensor Mesh** | Physically plausible sensor signals with an event engine — makes the whole platform demoable with zero hardware. |
| **Computer Vision (optional)** | YOLOv8 fire/smoke + people detection bridged in as a live sensor source. Capture and inference run on separate threads, so the feed stays at full frame rate while detection runs as fast as the machine allows; the first frame a flame appears in raises the alarm. |

---

## Architecture

```
                 ┌─────────────────────────────────────────────┐
   Cameras  ┐    │             PLANT ENGINE (5 Hz)             │
  (YOLOv8)  ├──▶ │  sensors → hazard → incidents → alerts →     │
 IoT mesh   ┘    │           evacuation → analytics            │
                 └───────────────┬──────────────┬──────────────┘
                                 │ snapshot()   │ commands
                        WebSocket│ (4 Hz)       │ (REST)
                                 ▼              ▼
                 ┌─────────────────────────────────────────────┐
                 │        FastAPI command-centre server         │
                 └───────────────────────┬─────────────────────┘
                                         ▼
                 ┌─────────────────────────────────────────────┐
                 │   Control-room dashboard (SPA, no build)     │
                 │ floor tabs · KPIs · trend · copilot · alerts │
                 └─────────────────────────────────────────────┘
```

The engine holds all state in memory behind a lock; the browser is a thin,
real-time view. No database, no auth, no external services. See
[`ARCHITECTURE.md`](ARCHITECTURE.md) for module-by-module detail.

---

## Project layout

```
safex_ai2/
├── run.py                 ← single entry point
├── requirements.txt
├── config.py              ← camera/grid config (used by CV mode)
├── best.pt · yolov8n.pt   ← bundled YOLOv8 models
│
├── safex/                 ← the platform
│   ├── settings.py            tunable thresholds, weights, branding
│   ├── sensors/               plant digital twin + mock IoT mesh
│   ├── core/                  hazard engine, incidents, alerts,
│   │                          evacuation, copilot, orchestrator
│   ├── analytics/             KPIs + predictive risk
│   ├── vision/                optional CV bridge (reuses original YOLO)
│   └── server/                FastAPI app + WebSocket + report renderer
│
├── web/                   ← control-room dashboard (SPA)
│   ├── index.html
│   ├── css/style.css
│   └── js/  app · floor · panels · copilot · controls
│
├── legacy/                ← original project, preserved & runnable
└── simulation/            ← original pygame evacuation simulations
```

---

## Design decisions (why it's built this way)

- **Runs with zero hardware.** The mock IoT mesh means a demo never depends on
  starting a fire in front of a webcam. The *same* engine accepts real camera
  input when it's available — CV is just another sensor.
- **Explainable, not a black box.** The Hazard Score is auditable maths a plant
  HSE team can tune, and every score ships with its drivers. The Copilot is
  grounded in that state and cannot hallucinate plant numbers.
- **No build step, no cloud.** The dashboard is dependency-light vanilla JS so
  it can't fail to compile mid-demo; the backend is a single FastAPI process.
- **The original CV work is preserved,** not discarded — it lives in `legacy/`
  and is wired back in through `safex/vision/`.

---

## Configuration (optional)

| Env var | Default | Purpose |
|---|---|---|
| `SAFEX_PORT` | `8000` | server port |
| `SAFEX_ORG` | *(demo plant name)* | plant name shown in the header/reports |
| `SAFEX_LLM` | `local` | set to `groq` to enable LLM narratives |
| `GROQ_API_KEY` | — | required only if `SAFEX_LLM=groq` |

Hazard weights, sensor envelopes and hazard bands are all tunable in
`safex/settings.py`.

---

<div align="center">
Built for the ET AI Hackathon 2026. &nbsp;·&nbsp; Local. Real-time. Explainable.
</div>

---

## Live Camera Intelligence 📹

SAFEX AI ships with a fully integrated multi-camera computer-vision layer. The
original YOLOv8 fire / smoke / person pipeline is preserved and wired directly
into the platform — no separate OpenCV window, everything lives in the browser.

**Two modes, one toggle** (top bar):

| Mode | What drives the plant | Cameras |
|------|----------------------|---------|
| **⎔ Simulation** *(default)* | IoT sensor mesh + scenario injections | Feeds still render, **mirroring** each zone's live state so you literally see the fire on the assigned camera |
| **◉ Live Camera** | **Real camera detections** — fire / smoke / person counts drive `cv_flame`, `cv_smoke`, `cv_people` for the assigned zone | Authoritative source; hazard, heatmap, incidents, evacuation and Copilot all react to the cameras |

Switching modes never removes anything — simulation stays fully intact.

**What the camera layer does**

- **Live feeds** on the dashboard + a dedicated **Camera Monitor** page (grid of
  every camera with fire / people / smoke / FPS and a LIVE / DEMO badge).
- **Camera → zone mapping.** Each camera belongs to exactly one zone; its
  detections only ever affect that zone, and the engine propagates the rest
  (neighbour smoke bleed, incidents, evacuation).
- **Person tracking.** Per-camera centroid tracking for stable counts, plus
  cross-camera movement inference — if a worker leaves one zone and an adjacent
  zone's camera gains one, that movement is logged; occupancy is maintained per
  zone (workers in-zone / evacuated / at risk).
- **Everything a sim event does, a detection does too:** hazard score, heatmap,
  alerts, incident timeline, evacuation routing and the AI Copilot.

**Default fleet** (re-map or add your own at runtime):

```
CAM-01 → Reactor Hall A     (Z1)
CAM-02 → Chemical Warehouse (Z7)
CAM-03 → Boiler Room        (Z9)
```

### Pointing it at real cameras

Out of the box, feeds run in **synthetic mode** (server-rendered surveillance
frames with real detection overlays) so the whole system is demoable on any
laptop with zero hardware. To use **real cameras + real YOLO detection**:

1. `pip install ultralytics pyttsx3` (models `best.pt` + `yolov8n.pt` already ship
   at the repo root).
2. Connect a camera and re-map a stream — either edit `config.py`
   (`CAMERA_SOURCES`) or hit the config endpoint at runtime:

   ```bash
   # USB webcam index 0 → Chemical Warehouse
   curl -X POST localhost:8000/api/cameras/config \
        -H 'Content-Type: application/json' \
        -d '{"camera_id":"CAM-02","source":"0","zone_id":"Z7"}'

   # IP / RTSP camera
   -d '{"camera_id":"CAM-02","source":"rtsp://user:pass@10.0.0.5/stream1","zone_id":"Z7"}'

   # Video file for a repeatable demo
   -d '{"camera_id":"CAM-02","source":"demo/warehouse_fire.mp4","zone_id":"Z7"}'
   ```

SAFEX auto-detects the source type (USB / IP / RTSP / file), and the **exact same
pipeline** then runs real detection — no code changes.

### Using real cameras & phone cameras — step by step

Everything below happens **in the browser** (no curl needed).

**1. Start in live-camera mode**
```bash
pip install ultralytics          # enables real YOLO (best.pt + yolov8n.pt ship here)
python run.py --camera           # attaches config.py CAMERA_SOURCES to the plant cameras
```
Then flip the top-bar toggle to **◉ LIVE CAM**. Now camera detections drive the plant.

**2. Attach your cameras to rooms** — open **◱ Cameras**, and on any tile click **⚙**:
- **Camera source:**
  - `0`, `1`, `2` … → a **USB / laptop webcam** (index).
  - `http://192.168.x.x:8080/video` → an **Android phone** running the **IP Webcam** app (start server, use the `/video` URL). Great for wireless multi-camera.
  - **DroidCam** → appears as a USB webcam, so just use its index (`1`, `2` …).
  - `rtsp://user:pass@10.0.0.5/stream1` → an **IP / CCTV camera**.
  - `demo/warehouse.mp4` → a **video file** for a repeatable demo.
- **Assigned room:** pick any zone (Reactor Hall, Chemical Warehouse, Canteen, …).
- Click **Apply**. Need more rooms? Use the **＋ Add camera / room** tile.

Each tile shows a badge: **LIVE** (real camera open), **DEMO** (synthetic feed), or
**OFFLINE** (a real source was set but couldn't open — check the index/URL).

**3. It behaves exactly like your original project.** The feed runs your original
detection: `best.pt` for **fire**, the classical OpenCV method for **smoke**, and
`yolov8n.pt` (with the same size/area/aspect/centre filters) for **people count**.

**4. Real detection → real response.** Point the Chemical-Warehouse camera at a
flame (e.g. a lighter). The moment YOLO detects fire:
- that camera's tile flips to **Fire: YES** with a red box + confidence,
- **Chemical Warehouse turns red** on the heatmap, smoke bleeds to neighbours,
- an **incident opens**, **evacuation routes animate**, **occupancy** (from the
  people detector) updates, a **spoken alert** fires, and the **Copilot** can
  explain it — identical to a simulated event, but driven by your camera.

> **Multi-camera movement:** if a person leaves one room's camera and an adjacent
> room's camera gains one, SAFEX infers the movement between those zones and keeps
> per-zone occupancy (workers in-zone / evacuated / at risk) in sync.

Switch back to **⎔ SIM** any time — simulation mode is fully preserved.
