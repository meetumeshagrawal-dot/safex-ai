import cv2

def detect_smoke(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (21,21), 0)

    diff = cv2.absdiff(gray, blur)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

    smoke_pixels = cv2.countNonZero(thresh)

    return smoke_pixels > 35000, thresh