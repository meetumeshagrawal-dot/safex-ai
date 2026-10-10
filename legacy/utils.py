def map_to_grid(x, y, width, height):
    grid_x = int(x / width * 10)
    grid_y = int(y / height * 10)
    return (grid_y, grid_x)


# 🔁 REMOVE DUPLICATE PEOPLE (MULTI-CAMERA FIX)
def remove_duplicate_people(global_people, distance_thresh=50):
    unique_people = []

    for person in global_people:
        (grid_pos, (px, py)) = person

        is_duplicate = False

        for u in unique_people:
            (_, (ux, uy)) = u

            dist = ((px - ux)**2 + (py - uy)**2) ** 0.5

            if dist < distance_thresh:
                is_duplicate = True
                break

        if not is_duplicate:
            unique_people.append(person)

    return unique_people