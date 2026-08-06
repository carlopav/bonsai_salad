# Bonsai Salad — daylight_ventilation tool

"""Geometry of the openings that light and ventilate a room: the clear opening
each one offers, and which room lies on either side of it.

Everything here works on triangulated bodies in world coordinates and knows
nothing of Blender.
"""

import numpy as np

import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.shape

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


# Where the opening is sliced across the host's thickness. The ends are sampled
# close to the faces but never on them: a plane through a face meets it edge on
# and reports nothing.
SAMPLES = np.linspace(0.02, 0.98, 13)


def settings():
    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set("use-world-coords", True)
    return geom_settings


def triangles(element, geom_settings):
    """(N, 3, 3) of the element's body in world coordinates, or None when it has
    no usable one."""
    try:
        # The shape has to outlive its geometry: read off a temporary and the
        # vertex buffers come back empty.
        shape = ifcopenshell.geom.create_shape(geom_settings, element)
    except RuntimeError:
        return None
    geometry = shape.geometry
    vertices = ifcopenshell.util.shape.get_vertices(geometry)
    faces = ifcopenshell.util.shape.get_faces(geometry)
    if not len(faces):
        return None
    return vertices[faces]


def thickness_axis(host):
    """The unit world direction of the host's smallest local extent: a wall's Y,
    a slab's or a roof's Z, without a case per class."""
    local = ifcopenshell.geom.settings()
    local.set("use-world-coords", False)
    body = triangles(host, local)
    if body is None:
        return None
    points = body.reshape(-1, 3)
    extent = points.max(axis=0) - points.min(axis=0)
    matrix = ifcopenshell.util.placement.get_local_placement(host.ObjectPlacement)
    direction = matrix[:3, int(np.argmin(extent))]
    return direction / np.linalg.norm(direction)


def _rotation_to_up(axis):
    """A rotation taking `axis` to +Z, so the sections become planes of constant
    z and the shoelace runs in XY."""
    axis = axis / np.linalg.norm(axis)
    if abs(float(axis @ UP)) > 1.0 - 1e-9:
        return np.eye(3) if axis[2] > 0 else np.diag([1.0, -1.0, -1.0])
    right = np.cross(axis, UP)
    right /= np.linalg.norm(right)
    return np.stack([right, np.cross(axis, right), axis])


def clear_opening_area(opening, host, geom_settings):
    """The smallest section of the opening taken across the host's thickness —
    the hole you see looking at the wall head-on — or None when either body is
    missing.

    Only the span the opening shares with the host is sampled: a void that
    overshoots the faces, or that is cut for a sill outside them, cannot inflate
    the number.
    """
    axis = thickness_axis(host)
    void = triangles(opening, geom_settings)
    wall = triangles(host, geom_settings)
    if axis is None or void is None or wall is None:
        return None
    rotation = _rotation_to_up(axis)
    void = void @ rotation.T
    wall = wall @ rotation.T
    low = max(float(void[:, :, 2].min()), float(wall[:, :, 2].min()))
    high = min(float(void[:, :, 2].max()), float(wall[:, :, 2].max()))
    if high <= low:
        return None
    areas = [section_area(void, low + fraction * (high - low)) for fraction in SAMPLES]
    return min(areas)
