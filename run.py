#!/usr/bin/env python3
"""
SAFEX AI — Industrial Safety Intelligence Platform
Single-command launcher.

    python run.py

Starts the command-centre server and opens the operations dashboard in your
browser. Runs fully offline in sensor-simulation mode (no cameras required);
computer-vision camera intelligence lights up automatically if the optional
vision dependencies and a camera are present (see `python run.py --camera`).
"""
import argparse
import sys
import threading
import time
import webbrowser

from safex.settings import HOST, PORT, PRODUCT_NAME, PRODUCT_TAGLINE


BANNER = f"""
   ⬢  {PRODUCT_NAME}
      {PRODUCT_TAGLINE}
   ────────────────────────────────────────────────
      Command centre  →  http://{HOST}:{PORT}
      API docs        →  http://{HOST}:{PORT}/api/docs
      Press Ctrl+C to stop.
   ────────────────────────────────────────────────
"""


def _open_browser():
    import os
    time.sleep(1.6)
    try:
        webbrowser.open(f"http://{os.environ.get('HOST', HOST)}:"
                        f"{os.environ.get('PORT', PORT)}")
    except Exception:
        pass


def _force_utf8_console():
    """Windows consoles still default to cp1252, which cannot print the box
    characters this banner (and the log lines) use — the process would die on
    its own startup message. Reconfiguring the streams fixes it everywhere."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _force_utf8_console()
    ap = argparse.ArgumentParser(description=PRODUCT_NAME)
    ap.add_argument("--no-browser", action="store_true",
                    help="don't auto-open the dashboard")
    ap.add_argument("--camera", action="store_true",
                    help="enable live computer-vision camera intelligence")
    ap.add_argument("--port", type=int, default=None,
                    help=f"port to serve on (default {PORT})")
    ap.add_argument("--host", default=None,
                    help=f"interface to bind (default {HOST})")
    args = ap.parse_args()

    # honour --host/--port by exporting them the way the server already reads
    # deployment settings, so one code path covers local, Render and Railway
    import os as _os
    if args.port:
        _os.environ["PORT"] = str(args.port)
    if args.host:
        _os.environ["HOST"] = args.host
    elif args.port:
        # PORT alone makes the server assume a cloud deploy and bind every
        # interface; asking for a different local port should not quietly put
        # the dashboard on the network
        _os.environ["HOST"] = HOST

    host = _os.environ.get("HOST", HOST)
    port = _os.environ.get("PORT", PORT)
    print(BANNER.replace(f"{HOST}:{PORT}", f"{host}:{port}"))

    if args.camera:
        # Enable real camera capture + YOLO detection. The camera manager will
        # attach the sources listed in config.py CAMERA_SOURCES (USB indices, IP
        # / RTSP URLs) to the plant cameras, in order. Requires `ultralytics`.
        import os
        os.environ["SAFEX_LIVE_CAMERAS"] = "1"
        print("   📷 Live camera intelligence ENABLED.")
        print("      • Sources are read from config.py → CAMERA_SOURCES")
        print("      • Flip the dashboard toggle to ◉ LIVE CAM to let detections")
        print("        drive the plant, or re-map cameras in the Camera Monitor.")
        try:
            import ultralytics  # noqa: F401
        except Exception:
            print("      ⚠  `ultralytics` not installed — feeds will run in DEMO")
            print("         mode until you `pip install ultralytics`.\n")
        else:
            print()

    if not args.no_browser:
        threading.Thread(target=_open_browser, daemon=True).start()

    try:
        from safex.server.app import run
        run()
    except KeyboardInterrupt:
        print("\n   ⬢  SAFEX AI command centre stopped. Stay safe.\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
