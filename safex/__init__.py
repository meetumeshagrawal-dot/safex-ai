"""
SAFEX AI — Industrial Safety Intelligence Platform
==================================================

A real-time safety intelligence layer for industrial plants that fuses
computer-vision (YOLOv8 fire / smoke / people detection) with a simulated
IoT sensor mesh to produce live hazard scoring, predictive risk analysis,
automated incident management, intelligent evacuation routing and an
AI Safety Copilot.

The package is intentionally dependency-light at import time: heavy vision
dependencies (torch / ultralytics / opencv) are loaded lazily so the whole
command-centre can run in "sensor-only" mode on any machine, and light up
camera intelligence automatically when those dependencies are present.
"""

__version__ = "2.0.0"
__product__ = "SAFEX AI — Industrial Safety Intelligence Platform"
__codename__ = "Sentinel"
