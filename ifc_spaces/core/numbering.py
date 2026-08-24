# Bonsai Salad — ifc_spaces tool

"""What each room ends up being called: one counter climbing the storeys, and
inside a storey a walk from the largest room to the nearest one after it.

Nothing here knows of Blender: the operator writes what this plans, and only
the Name.
"""

import math
from collections import namedtuple

import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.shape
import ifcopenshell.util.unit

MINIMUM_DIGITS = 2

# kept and clashes are what the run leaves as it found: the named rooms the
# option spared, and the rooms it renames none of that already carry a name this
# numbering hands out.
Plan = namedtuple("Plan", ("assignments", "unplaceable", "storeys", "kept", "clashes"))

_Room = namedtuple("_Room", ("space", "area", "x", "y"))


def settings():
    geom_settings = ifcopenshell.geom.settings()
    geom_settings.set("use-world-coords", True)
    return geom_settings


def plan(ifc_file, spaces, prefix="", rename_named=True):
    """The new name of each of the given rooms, in numbering order.

    All or nothing: a single room without a storey or without readable geometry
    leaves no assignment at all, so the caller has nothing to write. It holds for
    a named room the option spares too — it stays a stop of the chain, and a stop
    needs a storey and a centroid like any other room.
    """
    geom_settings = settings()
    unplaceable, storeys = [], {}
    for space in spaces:
        storey = _storey(ifc_file, space)
        room = _measure(ifc_file, space, geom_settings)
        if storey is None or room is None:
            unplaceable.append(space)
        else:
            storeys.setdefault(storey, []).append(room)
    if unplaceable:
        return Plan([], unplaceable, 0, [], [])

    chained = [room for storey in sorted(storeys, key=_height) for room in _chain(storeys[storey])]
    renaming, kept = [], []
    for room in chained:
        if rename_named or not _is_named(room.space):
            renaming.append(room.space)
        else:
            kept.append(room.space)
    digits = max(MINIMUM_DIGITS, len(str(len(renaming))))
    assignments = [(space, f"{prefix}{number:0{digits}d}") for number, space in enumerate(renaming, start=1)]

    numbered = set(renaming)
    storey_count = sum(1 for rooms in storeys.values() if any(room.space in numbered for room in rooms))
    names = {name for _, name in assignments}
    clashes = [space for space in ifc_file.by_type("IfcSpace") if space not in numbered and space.Name in names]
    return Plan(assignments, [], storey_count, kept, clashes)


def _chain(rooms):
    """One storey in walking order: the largest room first, then whichever
    unvisited room lies nearest the one just numbered."""
    remaining = sorted(rooms, key=lambda room: (-room.area,) + _tie(room))
    chain = [remaining.pop(0)]
    while remaining:
        current = chain[-1]
        nearest = min(remaining, key=lambda room: (math.dist((current.x, current.y), (room.x, room.y)),) + _tie(room))
        remaining.remove(nearest)
        chain.append(nearest)
    return chain


def _tie(room):
    """Two rooms the same distance away are still ordered, whatever order the
    selection arrived in."""
    return (room.x, room.y, room.space.GlobalId)


def _measure(ifc_file, space, geom_settings):
    """The room's area and the XY centre of its bounding box, or None where the
    geometry cannot be read.

    The centre of the box, not the average of the vertices: a denser
    tessellation down one side would drag that one, and the chain would follow
    the mesh rather than the plan.
    """
    try:
        shape = ifcopenshell.geom.create_shape(geom_settings, space)
    except RuntimeError:
        return None
    geometry = shape.geometry
    if not len(ifcopenshell.util.shape.get_vertices(geometry)):
        return None
    centroid = ifcopenshell.util.shape.get_bbox_centroid(geometry)
    return _Room(space, _area(ifc_file, space, geometry), float(centroid[0]), float(centroid[1]))


def _area(ifc_file, space, geometry):
    """The take-off already in the file where there is one, the footprint
    otherwise, both in project units.

    The area only ever serves to compare one room with another, but the kernel
    answers in metres whatever the file's LENGTHUNIT while a stored quantity is
    in project units: unconverted, a millimetre file would compare the two a
    million apart.
    """
    quantities = ifcopenshell.util.element.get_pset(space, "Qto_SpaceBaseQuantities", should_inherit=False)
    if quantities and quantities.get("NetFloorArea") is not None:
        return float(quantities["NetFloorArea"])
    scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
    return float(ifcopenshell.util.shape.get_footprint_area(geometry)) / scale**2


def _is_named(space):
    """A name of nothing but spaces is no name."""
    return bool((space.Name or "").strip())


def _storey(ifc_file, space):
    """The storey containing a room: normally reached through Decomposes, or —
    for a schema-invalid file that relates the space by containment instead,
    WR31 forbids it but an imported file may still do it — by walking
    IfcRelContainedInSpatialStructure directly.

    get_container also walks Decomposes, via get_parent, but only succeeds
    through ContainedInStructure, an inverse no spatial element carries, so it
    climbs past the storey and returns None; get_parent's ifc_class filter
    stops as soon as an ancestor of that class turns up.
    """
    storey = ifcopenshell.util.element.get_parent(space, ifc_class="IfcBuildingStorey")
    if storey is not None:
        return storey
    for rel in ifc_file.by_type("IfcRelContainedInSpatialStructure"):
        if space in rel.RelatedElements and rel.RelatingStructure.is_a("IfcBuildingStorey"):
            return rel.RelatingStructure
    return None


def _height(storey):
    """Where a storey sits, by the key Bonsai orders its own container tree with:
    the Z of the placement, and the Elevation attribute only for a storey that
    has no placement.

    Elevation is optional in every schema — real files leave it null on every
    storey, which read alone makes them all equal and the numbering start from
    whichever floor the selection happened to present first. The name settles two
    storeys standing at the same height."""
    return (ifcopenshell.util.placement.get_storey_elevation(storey), storey.Name or "")
