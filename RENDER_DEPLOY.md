# Deploying SAFEX AI on Render (with working YOLOv8 + browser webcam)

The deployed backend can't open a physical camera, so SAFEX uses the **visitor's
browser webcam**: the browser streams frames to the server, the server runs
YOLOv8, and sends detections back. For that to work the server needs the YOLO
stack installed — that's what the updated `requirements.txt` handles.

## 0. Pin Python to 3.11  ← the build-failure fix

Render's newer default is **Python 3.14**, which has **no `torch` wheel** yet —
that's the `No matching distribution found for torch` build error.

This repo ships a **`.python-version`** file (`3.11.9`) so Render uses 3.11. If
your build still picks 3.14, also set an environment variable in the Render
dashboard (this always wins):

```
PYTHON_VERSION = 3.11.9
```

(torch, ultralytics, opencv all have solid 3.11 wheels.)

## 1. Make sure the models are in your repo
`best.pt` and `yolov8n.pt` must be committed at the project root (they're ~6 MB
each, fine for Git). Without them the server can't detect.

## 2. Render service settings

**Build command**
```
pip install -r requirements.txt
```

**Start command** (bind to Render's port)
```
uvicorn safex.server.app:app --host 0.0.0.0 --port $PORT
```
> `python run.py --no-browser` also works now — it reads `$PORT` automatically.

**Instance type**
- PyTorch + YOLO needs memory. The **512 MB Free tier will likely OOM** when the
  model loads. Use at least a **1 GB instance** (Render "Starter" 512 MB is tight;
  2 GB is comfortable). If you must stay small, the app still runs — detection
  just reports `YOLO not installed / low memory` and feeds show without boxes.

## 3. Why the new requirements.txt matters
- `opencv-python-headless` — plain `opencv-python` crashes on Render with
  `libGL.so.1: cannot open shared object file`. Headless has the same API.
- **CPU-only torch** via `--extra-index-url https://download.pytorch.org/whl/cpu`
  — the default torch wheel bundles CUDA (~2 GB) and breaks Render's build. The
  CPU wheel is ~200 MB.
- `ultralytics==8.3.0` + `torch==2.2.2` is a known-good pair.

## 4. Using it once deployed
1. Open the site (Render serves HTTPS — required for camera access).
2. **◱ Cameras → 📷 Use My Webcam** → pick a room → **▶ Start Camera** → allow
   the browser camera permission.
3. Toggle **◉ LIVE CAM** (top bar) so detections drive hazards.
4. Show a flame to the camera → fire box appears, that room goes red, an incident
   opens, evacuation routes animate, and the speaker announces the nearest exit.

Browser support: Chrome, Edge, Firefox (all support `getUserMedia` over HTTPS).

## 5. Managing cameras
- The three default cameras (Reactor Hall / Chemical Warehouse / Boiler Room)
  are demo feeds. On a server with no USB cameras they show **OFFLINE** if a USB
  source was configured — just **🗑 delete** the ones you don't need, or point one
  at your browser via "Use My Webcam".
- **⚙** on a tile re-maps a real camera (USB index / IP / RTSP / video file) to a
  room; **🗑** deletes it; **＋ Add camera / room** adds more.

## 6. Free-tier tip (no GPU / low memory)
If you can't run torch on your plan, keep detection **client-friendly**: the
browser-webcam feed and the dashboard still work; you just won't get YOLO boxes.
For a full demo with detection, run locally (`python run.py --camera`) where your
machine has the models + CPU/GPU, or use a ≥1 GB Render instance.
