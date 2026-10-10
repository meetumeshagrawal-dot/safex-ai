"""
Computer-vision bridge.

Wraps the project's original YOLOv8 fire/people detection pipeline and feeds
its output into the live PlantEngine as an extra sensor source. Import is fully
lazy: none of these modules touch torch / opencv / ultralytics until a camera
is actually started, so the platform runs anywhere in sensor-simulation mode
and lights up vision intelligence only on demand.
"""
