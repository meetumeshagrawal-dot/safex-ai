import cv2
import numpy as np   # ✅ FIX ADDED

def create_grid():
    grid = [[0 for _ in range(10)] for _ in range(10)]

    # walls
    for i in range(3,7):
        grid[5][i] = 1

    return grid


def mark_fire(grid, fire_pos):
    r, c = fire_pos

    for i in range(-1,2):
        for j in range(-1,2):
            nr, nc = r+i, c+j
            if 0 <= nr < 10 and 0 <= nc < 10:
                grid[nr][nc] = 2

    return grid


# 🗺️ DRAW MINI MAP
def draw_map(grid, people, fire_pos, path):
    size = 300
    cell = size // 10

    img = 255 * np.ones((size, size, 3), dtype="uint8")

    for r in range(10):
        for c in range(10):
            color = (255,255,255)

            if grid[r][c] == 1:
                color = (0,0,0)  # wall
            elif grid[r][c] == 2:
                color = (0,0,255)  # fire

            cv2.rectangle(img, (c*cell, r*cell),
                          ((c+1)*cell, (r+1)*cell), color, -1)

    # exits
    for (er,ec) in [(0,9),(9,9)]:
        cv2.rectangle(img, (ec*cell, er*cell),
                      ((ec+1)*cell,(er+1)*cell),(0,255,0),-1)

    # people
    for (pr,pc) in people:
        cv2.circle(img, (pc*cell+cell//2, pr*cell+cell//2),
                   5, (255,0,0), -1)

    # path
    if path:
        for (r,c) in path:
            cv2.circle(img, (c*cell+cell//2, r*cell+cell//2),
                       3, (0,255,255), -1)

    return img