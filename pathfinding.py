from collections import deque

def find_safe_path(grid, start, exits):
    rows, cols = len(grid), len(grid[0])
    visited = set()
    queue = deque([(start, [])])

    directions = [(1,0),(-1,0),(0,1),(0,-1)]

    while queue:
        (r,c), path = queue.popleft()

        if (r,c) in exits:
            return path + [(r,c)]

        for dr,dc in directions:
            nr,nc = r+dr, c+dc

            if (0 <= nr < rows and 0 <= nc < cols and
                (nr,nc) not in visited):

                # avoid walls (1) and fire (2)
                if grid[nr][nc] != 0:
                    continue

                visited.add((nr,nc))
                queue.append(((nr,nc), path+[(r,c)]))

    return None