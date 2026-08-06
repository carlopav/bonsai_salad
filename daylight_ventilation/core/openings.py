# Bonsai Salad — daylight_ventilation tool

"""Geometry of the openings that light and ventilate a room: the clear opening
each one offers, and which room lies on either side of it.

Everything here works on triangulated bodies in world coordinates and knows
nothing of Blender.
"""

import numpy as np

UP = np.array([0.0, 0.0, 1.0])


def _face_normals(triangles):
    """Outward normals from the winding, unnormalised — only their direction is
    ever used."""
    return np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])


def section_area(triangles, z):
    """Area of the slice of a closed solid at the plane z, in world units.

    Each triangle crossing the plane yields one segment of the section's
    boundary. Oriented so the material stays on its left, every segment carries
    a shoelace term of its own, and their sum is the signed area of all the
    loops at once — which is why the loops never have to be walked. An inner
    shell, wound inwards, subtracts itself.
    """
    heights = triangles[:, :, 2] - z
    below = heights < 0
    count = below.sum(axis=1)
    crossing = (count == 1) | (count == 2)
    if not crossing.any():
        return 0.0

    tris, dist = triangles[crossing], heights[crossing]
    points, cuts = [], []
    for first in range(3):
        second = (first + 1) % 3
        a, b = tris[:, first], tris[:, second]
        da, db = dist[:, first], dist[:, second]
        span = db - da
        # The zero at da + ratio * span, not at da alone.
        ratio = np.where(span == 0, 0.0, -da / np.where(span == 0, 1.0, span))
        points.append(a + ratio[:, None] * (b - a))
        cuts.append((da < 0) != (db < 0))

    # Exactly two of the three edges cross; stable argsort puts them first.
    points = np.stack(points, axis=1)
    order = np.argsort(~np.stack(cuts, axis=1), axis=1, kind="stable")[:, :2]
    start = np.take_along_axis(points, order[:, 0, None, None], axis=1)[:, 0]
    end = np.take_along_axis(points, order[:, 1, None, None], axis=1)[:, 0]

    # Walking with the material on the left means walking along UP x normal.
    forward = np.cross(UP, _face_normals(tris))
    flip = ((end - start) * forward).sum(axis=1) < 0
    tail = np.where(flip[:, None], end, start)
    head = np.where(flip[:, None], start, end)
    return float(0.5 * np.sum(tail[:, 0] * head[:, 1] - head[:, 0] * tail[:, 1]))
