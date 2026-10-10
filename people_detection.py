from ultralytics import YOLO

model = YOLO("yolov8n.pt")

def detect_people(frame):
    results = model(frame, imgsz=320, conf=0.3, verbose=False)

    people = []

    for r in results:
        for box in r.boxes:

            cls = int(box.cls[0])
            conf = float(box.conf[0])

            if cls != 0:
                continue

            x1, y1, x2, y2 = map(int, box.xyxy[0])

            width = x2 - x1
            height = y2 - y1
            area = width * height

            # ✅ 1. MIN SIZE (ignore tiny detections)
            if height < 40 or width < 20:
                continue

            # ✅ 2. AREA FILTER (VERY IMPORTANT)
            if area < 2000:
                continue

            # ✅ 3. ASPECT RATIO (balanced)
            aspect_ratio = height / max(width, 1)
            if aspect_ratio < 0.8 or aspect_ratio > 3.5:
                continue

            # 🔥 4. CENTER CHECK (remove edge junk like hanging clothes)
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            h_frame, w_frame, _ = frame.shape

            if cx < 0.1 * w_frame or cx > 0.9 * w_frame:
                continue

            if cy < 0.1 * h_frame or cy > 0.9 * h_frame:
                continue

            people.append({
                "box": (x1, y1, x2, y2),
                "center": (cx, cy),
                "conf": conf
            })

    return people