# Bonsai Salad — daylight_ventilation tool

"""Geometry of the openings that light and ventilate a room: the clear opening
each one offers, and which room lies on either side of it.

Everything here works on triangulated bodies in world coordinates and knows
nothing of Blender.
"""

from collections import namedtuple

import numpy as np

import ifcopenshell.geom
import ifcopenshell.util.placement
import ifcopenshell.util.shape

from . import spaces

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
    """The smallest section of the opening taken across the host's thickness, in
    SI metres — the hole you see looking at the wall head-on — or None when
    either body is missing.

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
    smallest = min(section_area(void, low + fraction * (high - low)) for fraction in SAMPLES)
    # A negative section is a void wound inwards: not a measurement, and not an
    # area to store either.
    return None if smallest < 0 else smallest


# How far past the host's face a probe reaches. Short enough not to leave a
# small room, long enough to clear a plaster layer — every distance is tried in
# turn and the first that lands in a room decides that side.
PROBES = (0.05, 0.15, 0.30, 0.60)


def _incommensurate_ray(*squares):
    """A unit direction built from `sqrt(squares)`: for distinct squarefree p,
    q, `1, sqrt(p), sqrt(q)` are Q-linearly independent, so no rational
    architectural delta can tie two of the ray's axis-crossings together — down
    to the rounding of those roots to the nearest float."""
    ray = np.sqrt(np.array(squares, dtype=float))
    return ray / np.linalg.norm(ray)


# Tried in turn until one crosses the body without grazing an edge.
RAYS = (
    _incommensurate_ray(1, 2, 3),
    _incommensurate_ray(5, 1, 7),
    _incommensurate_ray(11, 13, 1),
)

# How close a crossing may sit to a triangle's edge before it is a tie rather
# than a clean hit.
GRAZE = 1e-9


def _cast(body, point, ray):
    origin = body[:, 0]
    edge1 = body[:, 1] - origin
    edge2 = body[:, 2] - origin
    across = np.cross(ray, edge2)
    determinant = (edge1 * across).sum(axis=1)
    parallel = np.abs(determinant) < 1e-12
    scale = np.where(parallel, 0.0, 1.0 / np.where(parallel, 1.0, determinant))
    offset = point - origin
    u = (offset * across).sum(axis=1) * scale
    along = np.cross(offset, edge1)
    v = (ray * along).sum(axis=1) * scale
    distance = (edge2 * along).sum(axis=1) * scale
    forward = ~parallel & (distance > 1e-9)
    grazing = forward & ((np.abs(u) < GRAZE) | (np.abs(v) < GRAZE) | (np.abs(u + v - 1.0) < GRAZE))
    hit = forward & (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0)
    return hit, bool(grazing.any())


def contains(body, point):
    """Whether a closed body encloses a point, by the parity of the crossings of
    a ray leaving it. A ray that grazes a triangle's edge is untrustworthy — it
    can land on the seam shared by two triangles and count that one crossing
    twice — so it is retried along another direction rather than trusted."""
    for ray in RAYS:
        hit, grazed = _cast(body, point, ray)
        if not grazed:
            return bool(hit.sum() % 2)
    raise ValueError("every probe direction grazed the body's boundary")


def _room_at(point, spaces):
    for space, body in spaces.items():
        if body is not None and contains(body, point):
            return space
    return None


def probe(opening, host, spaces, geom_settings):
    """(the rooms on either side of the opening, whether it is external).

    External means one side landed in a room and the other in none: what the
    wall separates the room from is either not modelled or is outdoor space, and
    either way it is outside.
    """
    axis = thickness_axis(host)
    void = triangles(opening, geom_settings)
    wall = triangles(host, geom_settings)
    if axis is None or void is None or wall is None:
        return [], False
    centre = void.reshape(-1, 3).mean(axis=0)
    points = wall.reshape(-1, 3) @ axis
    half = (points.max() - points.min()) / 2
    middle = centre - axis * float(centre @ axis - (points.max() + points.min()) / 2)

    sides = []
    for sign in (1.0, -1.0):
        found = None
        for distance in PROBES:
            found = _room_at(middle + axis * sign * (half + distance), spaces)
            if found is not None:
                break
        sides.append(found)
    rooms = [room for room in sides if room is not None]
    # dict.fromkeys keeps the order and drops a room found on both sides, which
    # happens when a wall doubles back into the same space.
    return list(dict.fromkeys(rooms)), len(rooms) == 1


FILLING_CLASSES = ("IfcWindow", "IfcDoor")

Proposal = namedtuple("Proposal", ("filling", "opening", "host", "rooms", "external", "area"))
# area is None when clear_opening_area could not measure the void — not the same as a real zero.


def fillings(ifc_file):
    """Every window and door in the file, in file order."""
    found = []
    for ifc_class in FILLING_CLASSES:
        found.extend(ifc_file.by_type(ifc_class))
    return found


def host_of(filling):
    """(the opening the filling fills, the element that opening voids), either
    of them None when the chain is broken."""
    for rel in filling.FillsVoids:
        opening = rel.RelatingOpeningElement
        for void in opening.VoidsElements:
            return opening, void.RelatingBuildingElement
    return None, None


def _spaces(ifc_file, geom_settings):
    """The bodies a probe may land in: rooms only, so a window onto a loggia has
    nothing on its far side and reads as external."""
    return {space: triangles(space, geom_settings) for space in spaces.rooms(ifc_file)}


def proposals(ifc_file, geom_settings=None):
    """What the geometry says about every filling: the rooms it borders, whether
    it faces outside, and the clear opening it offers, in SI metres.

    An opening shared by several fillings splits its clear opening equally among
    them — counting the same hole once per filling would double the light. A
    filling whose probe cannot be answered — every ray grazing a room's boundary
    — comes back as an orphan rather than aborting every other filling in the
    file.
    """
    geom_settings = geom_settings or settings()
    spaces = _spaces(ifc_file, geom_settings)
    found = []
    for filling in fillings(ifc_file):
        opening, host = host_of(filling)
        if opening is None or host is None:
            continue
        try:
            rooms, external = probe(opening, host, spaces, geom_settings)
        except ValueError:
            rooms, external = [], False
        area = clear_opening_area(opening, host, geom_settings)
        share = len(opening.HasFillings) or 1
        found.append(Proposal(filling, opening, host, rooms, external, None if area is None else area / share))
    return found
