import cv2
import numpy as np
import time
import pyttsx3

from config import EXITS
from camera_manager import CameraManager
from building_map import create_grid, mark_fire
from fire_detection import detect_fire
from people_detection import detect_people
from utils import map_to_grid, remove_duplicate_people

engine = pyttsx3.init()
last_alert = 0

people_memory = []
memory_timer = 0

frame_count = 0
last_people = []  # 🔥 cache detections

cam_manager = CameraManager()


# 🧠 SMART EXIT (crowd + fire)
def choose_best_exit(global_people, exits, grid):
    exit_load = {}

    for ex in exits:
        count = 0

        for person in global_people:
            pr, pc = person
            er, ec = ex

            if abs(pr - er) + abs(pc - ec) < 3:
                count += 1

        # 🔥 avoid exits near fire
        danger = False
        for r in range(-2, 3):
            for c in range(-2, 3):
                nr, nc = ex[0] + r, ex[1] + c
                if 0 <= nr < len(grid) and 0 <= nc < len(grid[0]):
                    if grid[nr][nc] == 2:
                        danger = True

        if danger:
            exit_load[ex] = 999
        else:
            exit_load[ex] = count

    best_exit = min(exit_load, key=exit_load.get)
    return best_exit, exit_load


while True:
    frames = cam_manager.read_frames()

    if not frames:
        break

    grid = create_grid()
    global_people = []
    global_fire_positions = []

    processed_frames = []
    total_people = 0
    fire_detected_global = False

    frame_count += 1
    run_detection = (frame_count % 2 == 0) 

    # 🎥 PROCESS EACH CAMERA
    for cam_id, frame in enumerate(frames):

        if frame is None:
            continue

        h, w, _ = frame.shape

        # 🔥 FIRE DETECTION
        fire_detected, fire_center, _ = detect_fire(frame)

        if fire_detected and fire_center:
            fire_detected_global = True

            fire_pos = map_to_grid(fire_center[0], fire_center[1], w, h)
            global_fire_positions.append(fire_pos)

        # 👥 PEOPLE DETECTION (OPTIMIZED)
        small = cv2.resize(frame, (224, 168))  # 🔥 smaller = faster

        if run_detection:
            detected_people = detect_people(small)

            if len(detected_people) > 0:
                people_memory = detected_people
                memory_timer = 5  # 🔥 keep for 5 frames
            else:
                if memory_timer > 0:
                    memory_timer -= 1
                else:
                    people_memory = []

            people = people_memory
        else:
            people = people_memory

        cam_people_count = len(people)
        total_people += cam_people_count

        scale_x = w / 224
        scale_y = h / 168

        for person in people:
            (x1, y1, x2, y2) = person["box"]
            (px, py) = person["center"]

            x1 = int(x1 * scale_x)
            y1 = int(y1 * scale_y)
            x2 = int(x2 * scale_x)
            y2 = int(y2 * scale_y)

            px = int(px * scale_x)
            py = int(py * scale_y)

            # 🟩 PERSON BOX
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0,255,0), 2)

            start = map_to_grid(px, py, w, h)
            global_people.append((start, (px, py)))

        # 📊 CAMERA TEXT
        cv2.putText(frame, f"CAM {cam_id}", (10,30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)

        cv2.putText(frame, f"People: {cam_people_count}", (10,60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)

        if fire_detected:
            cv2.putText(frame, "AI EVACUATION ACTIVE", (10,90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2)

        processed_frames.append(frame)
    
    # 🔁 REMOVE DUPLICATE PEOPLE ACROSS CAMERAS
    global_people = remove_duplicate_people(global_people)

    # 🔥 APPLY FIRE TO GRID
    for fire_pos in global_fire_positions:
        grid = mark_fire(grid, fire_pos)

    # 🧠 EXIT DECISION
    grid_people_only = [p[0] for p in global_people]
    best_exit, exit_load = choose_best_exit(grid_people_only, EXITS, grid)

    # 🖥️ DASHBOARD
    if len(processed_frames) >= 2:
        dashboard = np.hstack(processed_frames[:2])
    elif len(processed_frames) == 1:
        dashboard = processed_frames[0]
    else:
        continue

    h, w, _ = dashboard.shape

    # 👥 TOTAL PEOPLE
    cv2.putText(dashboard, f"Total People: {total_people}",
                (20, h-20),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)

    # 🚨 FIRE ALERT
    if fire_detected_global:
        cv2.putText(dashboard, "🔥 FIRE DETECTED",
                    (20, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0,0,255), 3)

    # 🧭 EXIT TEXT
    # 🧠 OVERRIDE LOGIC (YOUR CONDITION)
    if total_people >= 5:
        exit_text = "← USE EXIT B"
        best_exit = EXITS[1]   # force exit B
    else:
        if best_exit == EXITS[0]:
            exit_text = "USE EXIT A →"
        else:
            exit_text = "← USE EXIT B"

    cv2.putText(dashboard, exit_text,
                (20, 150),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0,255,0), 3)

    # 🎯 ARROWS
    for (grid_pos, (px, py)) in global_people:

        pr, pc = grid_pos
        er, ec = best_exit

        dx = ec - pc
        dy = er - pr

        cv2.arrowedLine(dashboard,
                        (px, py),
                        (px + int(dx * 20), py + int(dy * 20)),
                        (0,255,0), 3)

    # 📊 EXIT LOAD
    cv2.putText(dashboard, f"Exit Load: {exit_load}",
                (20, 190),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)

    # 🔊 VOICE ALERT
    if fire_detected_global and time.time() - last_alert > 5:

        msg = "Move to Exit A" if best_exit == EXITS[0] else "Move to Exit B"

        engine.say(msg)
        engine.runAndWait()
        last_alert = time.time()

    cv2.imshow("SAFEX AI GROUP SYSTEM", dashboard)

    if cv2.waitKey(1) == 27:
        break

cam_manager.release_all()
cv2.destroyAllWindows()