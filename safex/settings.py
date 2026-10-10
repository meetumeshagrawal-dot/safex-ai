"""
Central configuration for the SAFEX AI platform.

Everything tunable lives here so operators can adapt the platform to a real
plant without touching engine code. Values are deliberately conservative and
mirror thresholds used in real process-safety practice.
"""
from __future__ import annotations
import os

# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------
PRODUCT_NAME = "SAFEX AI"
PRODUCT_TAGLINE = "Industrial Safety Intelligence Platform"
ORG_NAME = os.environ.get("SAFEX_ORG", "Meridian Heavy Industries — Plant 4")

# ---------------------------------------------------------------------------
# Real-time loop
#
# The control loop runs far faster than once a second so a flame that appears
# on a camera is reflected in the hazard field, the alerts and the evacuation
# routing within a couple of hundred milliseconds. Sensor *physics* is
# integrated with real elapsed time (dt), so the loop rate can change without
# altering how an incident behaves.
# ---------------------------------------------------------------------------
LOOP_SECONDS = 0.2          # engine control loop  (5 Hz)
STREAM_SECONDS = 0.25       # websocket snapshot push (4 Hz)
ANALYTICS_SECONDS = 1.0     # trend history / forecast cadence (1 Hz)
TICK_SECONDS = LOOP_SECONDS  # legacy alias (used as the client's expectation)
TELEMETRY_HISTORY = 120     # points of history kept per sensor
INCIDENT_HISTORY = 200      # max incidents retained in memory

# ---------------------------------------------------------------------------
# Camera / computer-vision responsiveness
# ---------------------------------------------------------------------------
CAMERA_CAPTURE_FPS = 25       # frame grabbing target (video smoothness)
DETECT_MIN_INTERVAL = 0.05    # floor on time between YOLO passes per camera
PEOPLE_EVERY_N = 2            # run the person model every Nth detection pass
FIRE_CONF_TRIGGER = 0.25      # confidence at which a flame is called
FIRE_HOLD_SECONDS = 2.0       # keep a detection latched this long after it drops
YOLO_IMGSZ_FIRE = 416         # smaller = faster; 416 keeps flame recall high
YOLO_IMGSZ_PEOPLE = 320
# Slightly below the usual 0.3: in a ward, missing a patient matters more than
# briefly over-counting one, and occupancy only amplifies the hazard score —
# it never raises an alarm on its own.
PERSON_CONF = 0.25

# --- smoke is deliberately harder to trigger than fire -----------------------
# Smoke is the ambiguous one: grey, soft-edged and slow, which describes a wall,
# a jumper, a face in dim light and plain sensor noise just as well as it
# describes smoke. So a smoke alarm needs a confident model detection, has to
# survive the sanity checks below, and has to persist — while a flame is still
# called on the very first frame it appears in.
SMOKE_CONF_TRIGGER = 0.55     # model confidence needed to even consider smoke
SMOKE_PERSIST_HITS = 3        # accepted frames needed inside the window below
SMOKE_PERSIST_WINDOW = 2.5    # seconds
SKIN_MAX_IN_BOX = 0.12        # >12% skin-tone pixels ⇒ it's a person, not smoke
PERSON_OVERLAP_VETO = 0.55    # box mostly inside a person ⇒ it's the person

# --- fog / steam discrimination ---------------------------------------------
# best.pt calls anything grey and soft-edged "smoke", which is also a fair
# description of kitchen steam, an autoclave venting, a shower door opening and
# condensation on the dome. A second model (safex/vision/fogsmoke) scores the
# region on the optics that actually differ — smoke absorbs and is warm-tinted
# and unevenly thick, water scatters and is bright and uniform.
#
# It is wired asymmetrically on purpose. On the held-out split it recalls 95.4%
# of real smoke, so roughly one smoke region in twenty is called water — which
# is a fine error to make when you are only *slowing* an alarm and an unforgiven
# one if you cancel it. So a confident "this is water" drops the box, a merely
# doubtful one only removes the early-confirmation shortcut, and if flame is
# visible anywhere in the frame the check is skipped entirely.
FOG_VETO_BELOW = 0.20         # p(smoke) under this ⇒ discard: it is water
FOG_STRICT_BELOW = 0.50       # under this ⇒ keep, but make it persist the full count

# --- egress / compliance monitoring ------------------------------------------
# A blocked corridor is a static condition, so it is checked on a slow duty
# cycle while the building is calm — the expensive part (YOLO) runs anyway for
# flame detection, and re-deciding the same answer 20 times a second buys
# nothing. The instant fire or smoke is involved it switches to every pass,
# because the question stops being "is this a violation" and becomes "can
# people still get out of here".
EGRESS_IDLE_INTERVAL = 4.0     # seconds between corridor checks when calm
EGRESS_ALERT_INTERVAL = 0.4    # ...and when something is burning

# ---------------------------------------------------------------------------
# Hazard scoring — fusion weights (must sum to ~1.0)
# ---------------------------------------------------------------------------
HAZARD_WEIGHTS = {
    "gas": 0.26,          # combustible / toxic gas concentration
    "temperature": 0.20,  # thermal excursion
    "smoke": 0.18,        # smoke density (sensor + CV)
    "fire": 0.16,         # confirmed flame (CV / flame detector)
    "vibration": 0.10,    # mechanical / structural anomaly
    "occupancy": 0.10,    # people at risk amplifier
}

# Hazard bands (score 0-100)
HAZARD_BANDS = [
    (0, 30, "NORMAL", "#3ddc97"),
    (30, 55, "ELEVATED", "#ffd166"),
    (55, 75, "HIGH", "#ff8c42"),
    (75, 101, "CRITICAL", "#ff5470"),
]

# ---------------------------------------------------------------------------
# Sensor safe-operating envelopes  (baseline, warning, critical)
# ---------------------------------------------------------------------------
SENSOR_ENVELOPE = {
    "temperature": {"unit": "°C", "nominal": 32, "warn": 60, "crit": 85, "max": 140},
    "gas":         {"unit": "ppm", "nominal": 40, "warn": 200, "crit": 400, "max": 900},
    "smoke":       {"unit": "%obs", "nominal": 3, "warn": 25, "crit": 45, "max": 100},
    "vibration":   {"unit": "mm/s", "nominal": 2.0, "warn": 7.0, "crit": 11.0, "max": 20},
    "noise":       {"unit": "dB", "nominal": 74, "warn": 92, "crit": 105, "max": 130},
}

# ---------------------------------------------------------------------------
# AI Safety Copilot — optional LLM backend (falls back to grounded local engine)
# ---------------------------------------------------------------------------
# Set SAFEX_LLM=groq and GROQ_API_KEY to enable LLM-authored narratives.
# With no key set, the Copilot runs a fully offline, state-grounded engine.
LLM_PROVIDER = os.environ.get("SAFEX_LLM", "local").lower()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("SAFEX_LLM_MODEL", "llama-3.3-70b-versatile")

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
HOST = os.environ.get("SAFEX_HOST", "127.0.0.1")
PORT = int(os.environ.get("SAFEX_PORT", "8000"))


def hazard_band(score: float):
    """Return (label, color) for a 0-100 hazard score."""
    for lo, hi, label, color in HAZARD_BANDS:
        if lo <= score < hi:
            return label, color
    return "CRITICAL", "#ff5470"
