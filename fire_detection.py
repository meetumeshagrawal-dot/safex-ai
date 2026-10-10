from ultralytics import YOLO
import cv2

model = YOLO("best.pt")

def detect_fire(frame):
    results = model(frame, stream=True)

    fire_detected = False
    fire_center = None
    fire_size = 0

    for r in results:
        for box in r.boxes:
            conf = float(box.conf[0])
            cls = int(box.cls[0])

            if conf < 0.25:
                continue

            x1, y1, x2, y2 = map(int, box.xyxy[0])
            label = model.names[cls]

            if label.lower() in ["fire", "smoke"]:
                fire_detected = True
                fire_center = ((x1 + x2)//2, (y1 + y2)//2)
                fire_size = (x2 - x1) * (y2 - y1)

                cv2.rectangle(frame, (x1,y1), (x2,y2), (0,0,255), 2)
                cv2.putText(frame, f"{label} {conf:.2f}",
                            (x1, y1-10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,0,255), 2)

    return fire_detected, fire_center, fire_size