import cv2
from config import CAMERA_SOURCES

class CameraManager:
    def __init__(self):
        self.caps = []
        self.init_cameras()

    def init_cameras(self):
        for src in CAMERA_SOURCES:

            cap = cv2.VideoCapture(src)

            # 🔥 reduce lag
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            # 🔥 reduce resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            self.caps.append(cap)

    def read_frames(self):
        frames = []

        for cap in self.caps:

            # 🔥 drop old frames
            for _ in range(3):
                cap.grab()

            ret, frame = cap.read()

            if not ret or frame is None:
                frames.append(None)
                continue

            frame = cv2.flip(frame, 1)
            frame = cv2.resize(frame, (640, 480))

            frames.append(frame)

        return frames

    def release_all(self):
        for cap in self.caps:
            cap.release()