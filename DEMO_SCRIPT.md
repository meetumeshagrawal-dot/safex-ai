# 🎬 SAFEX AI — demo script

A six-minute walkthrough for judges. Every step is one click except adding the
camera. Keep this open on a second screen or printed.

> **Before they arrive**
> ```bash
> python run.py --host 0.0.0.0
> ```
> Dashboard at http://127.0.0.1:8000 — from a phone or another laptop on the
> same network, `http://<your-laptop-ip>:8000`. The page is up in ~3 seconds,
> but **the detection models take longer to load** — about 15 s on a warm
> machine, up to 40 s on the first run after a reboot. The header chip tells you
> exactly where it is: `VISION LOADING 12s` → `VISION READY`. **Do not start
> demoing until it reads READY** — until then a camera shows video but detects
> nothing. So start the server a minute before you are called in, not as they
> sit down. **Volume on** — the spoken alerts land well.
>
> On a venue network `--host 0.0.0.0` puts the dashboard on that whole network.
> Fine for a demo; use plain `python run.py` anywhere it matters.

---

## Before you start: the phone

1. **IP Webcam** (Play Store) installed and tested — this is what turns a phone
   into a real CCTV feed. Note the `http://<phone-ip>:8080/video` URL it shows.
2. A **10-15 second fire video** in the gallery, close-up, clear flame.
3. A **fire extinguisher** photo and a **green EXIT sign** photo, full screen.
4. A **cardboard box or stack of books** to physically block the "corridor".

**Use your own phone hotspot**, not the venue WiFi — most venue networks isolate
clients from each other and the laptop will never reach the phone. Test this the
night before.

> The phone's *browser* camera ("📷 Use My Webcam") will **not** work over
> `http://` on a phone — Chrome only grants camera access on `https` or
> `localhost`. That is a browser rule, not a bug here. IP Webcam avoids it
> entirely because the laptop pulls from the phone.

---

## Act 1 — "This is a hospital, and it is quiet" (30s)

Dashboard, full screen. Point at the **G / L1 / L2** tabs.

> "Three floors. 42 rooms — Emergency and Radiology on the ground, ICU and
> theatres on Level 1, wards above. Two stair cores, three assembly points.
> Each tab carries that floor's worst score, so a fire upstairs is never
> invisible while you are looking at another floor."

Compliance card, top right: **100, COMPLIANT.**

---

## Act 2 — a real camera, a real flame (90s) ⭐

**◱ Cameras → ＋ Add camera / room**

- Name: `Ward Corridor Cam`
- Room: **Ward Corridor**
- Source: `http://<phone-ip>:8080/video`

> "This is not a simulation. That is my phone, on the network, as a CCTV feed."

Wait for the tile to read **LIVE** with real FPS. Now play the fire video in
front of the phone.

> "YOLOv8 calls flame on the **first frame** it appears in."

Switch back to the dashboard the moment it trips — **the view jumps to that
floor by itself.**

> "Room CRITICAL. Incident opened. Alert spoken. And the evacuation route has
> already been re-planned — out of the ward, along the corridor, down Stair A,
> out at the assembly point. That took six tenths of a second."

**If the network fails:** use the laptop's own webcam (source `0`), or the
**⚡ Fire** button on any camera tile. Say "on a demo feed" — never imply it is
live when it is not.

---

## Act 3 — the part detectors cannot do (90s) ⭐ *this is the problem statement*

Put the **box in front of the phone** (or press **⛔** on the tile).

> "Now I block that corridor. And notice what catches it — not just objects the
> model can name. Every corridor camera **learns what its corridor looks like
> empty**, so a carton, a linen trolley, debris brought down by the fire —
> things no classifier has a class for — are still caught.
>
> And nothing is a violation until it **stays**. A nurse walking through is
> traffic. A trolley left for two minutes is a finding."

Now **🚪** and **🧯** on the tile.

> "Fire door held open. That does not re-route anybody — the corridor is still
> walkable — what it does is breach the compartment, so smoke crosses into the
> neighbouring rooms, and the model shows that happening.
>
> And the extinguisher is not in its bracket."

Point at the compliance card: **100 → 79.3**, three findings, each with the
room, how long it has been like that, and what to do about it.

---

## Act 4 — automation, and the line it will not cross (60s) ⭐ *they will ask*

Tick **ARMED**. Run **Release SAFEX door hold-opens**.

> "SAFEX released its own door magnets. They are power-to-hold — if this
> platform dies, the door closes. Every action here fails safe."

Now the important bit:

> "But ask it to trigger the hospital's fire alarm panel and it **refuses**."

(Show it, or read the refusal.)

> "The panel, the detectors and the sprinklers are certified life-safety
> equipment. A monitoring system that could inhibit or spoof them would make the
> building less safe than having no monitoring at all. So we never write to
> them. That is not a limitation we ran into — it is the design, and every
> refusal is logged."

---

## Act 5 — the report an administrator actually gets (50s)

**▤ Report**.

> "This covers a **week**, not this moment — findings that were raised and
> cleared three days ago are still in it, with how long they lasted.
>
> And it states what it does **not** cover: rooms with no camera, doors never
> calibrated. Those are not compliant or non-compliant, they are unobserved,
> and hiding them would overstate how much of this building is watched."

Point at the green banner.

> "It also verifies itself. Every audit record carries the hash of the one
> before it. Edit an old record and the chain breaks — and the report says
> exactly where."

---

## Close (20s)

> "All ten technical requirements are implemented and measured. And we document
> what it cannot do as carefully as what it can — no metres without
> calibration, no naming unidentified debris, tamper-**evident** logging rather
> than tamper-proof. In fire safety that honesty is the product."

---

## Questions they will ask

| Question | Answer |
|---|---|
| False alarm rate? | Measured. A face plus webcam noise used to read as smoke on **6 of 6** frames; it is now **0 of 10**. Skin and person vetoes, plus smoke must persist 3 frames in 2.5 s — while flame is still called on the first frame. |
| Will you integrate with the fire panel? | No, deliberately. It is certified life-safety equipment. We run alongside it, never write to it. |
| Where is your extinguisher model? | There isn't one — COCO has no such class. The operator captures it once in place and we compare against that. It works in any building with no training, and it cannot invent an extinguisher that was never there. |
| How do you get metres? | You don't, without calibration — and we say so. Four clicks map the camera to the plan; enter the floor's real width and a corridor becomes "5.0 m of 8.4 m clear". |
| Will it run on old cameras? | 480p is plenty. 24.5 FPS on a laptop CPU with detection alongside. No GPU, no cloud, no database. |
| Is the log secure? | Tamper-**evident**, not tamper-proof. Publish the chain head outside the system and it becomes checkable against a copy the operator cannot rewrite. Claiming more would be worse than useless in an audit. |

---

## If something breaks

| Symptom | Do this |
|---|---|
| Phone camera won't connect | Switch to laptop webcam (source `0`), or use the **⚡ Fire** demo button and say so |
| Tile shows OFFLINE | The tile prints the reason — read it out; it is honest behaviour, not a crash |
| Everything is wedged | `Ctrl+C`, `python run.py --host 0.0.0.0`; page back in ~3 s, wait for **VISION READY** (~15 s) before detecting again — state rebuilds itself |
| Laptop dies | Play the recorded video — keep it **downloaded**, not streaming |
