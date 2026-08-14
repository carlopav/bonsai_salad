"""
clipping — pure IFC core of copy_clipping_planes (no bpy, no bonsai).

Reads the half-space clipping planes cutting a solid, re-expresses them in
another element's local frame so they keep the same world position, and
applies them there.

Matrices are 4x4 in metres (a Blender matrix_world, or the change of frame
between two of them); IFC coordinates are written in project units.
"""

import numpy as np
import ifcopenshell.api.geometry
import ifcopenshell.util.element
import ifcopenshell.util.shape_builder


def half_space_clips(booleans):
    """The half-space operands among a boolean chain, in the order they were
    applied (innermost first). `booleans` comes outermost first, the way
    Bonsai's tool.Model.get_booleans returns it."""
    return [b.SecondOperand for b in reversed(booleans) if b.SecondOperand.is_a("IfcHalfSpaceSolid")]


# --------------------------------------------------------------------------
# Moving a clip into another element's frame


def half_space_placements(half_space):
    """The frames the half space hangs on: the base surface's, plus the
    boundary frame of a polygonal bounded one."""
    placements = []
    position = getattr(half_space.BaseSurface, "Position", None)
    if position is not None:
        placements.append(position)
    if half_space.is_a("IfcPolygonalBoundedHalfSpace"):
        placements.append(half_space.Position)
    return placements


def as_coordinates(vector):
    """Plain floats: ifcopenshell rejects an aggregate of numpy scalars."""
    return tuple(float(c) for c in vector)


def rotated(matrix, direction, default):
    """A placement axis turned by matrix's rotation, normalised. An absent
    direction falls back to its implicit default."""
    ratios = direction.DirectionRatios if direction else default
    turned = matrix[:3, :3] @ np.array(ratios, dtype=float)
    return as_coordinates(turned / np.linalg.norm(turned))


def transform_half_space(ifc_file, half_space, matrix, unit_scale):
    """A copy of half_space with its frames moved by matrix, so the plane keeps
    the world position it had on the source element. The copy owns its
    geometry: removing it later leaves the original clip untouched.

    Axis and RefDirection are written out explicitly — their implicit defaults
    (+Z, +X) stop holding as soon as the frame is turned. A point shared by two
    frames is moved once, so it stays shared and correct."""
    copy = ifcopenshell.util.element.copy_deep(ifc_file, half_space)
    placements = half_space_placements(copy)
    for point in {p.Location.id(): p.Location for p in placements}.values():
        location = np.array(point.Coordinates, dtype=float) * unit_scale
        point.Coordinates = as_coordinates((matrix[:3, :3] @ location + matrix[:3, 3]) / unit_scale)
    for placement in placements:
        placement.Axis = ifc_file.createIfcDirection(rotated(matrix, placement.Axis, (0.0, 0.0, 1.0)))
        placement.RefDirection = ifc_file.createIfcDirection(rotated(matrix, placement.RefDirection, (1.0, 0.0, 0.0)))
    return copy


# --------------------------------------------------------------------------
# Building a clip from a plane


def upward(normal):
    """The same plane normal, never pointing down. A half space normal points at
    the material being removed, so an upward one cuts away what sits above the
    plane; a vertical face, having no side above, is left as it is."""
    vector = np.array(normal, dtype=float)
    vector /= np.linalg.norm(vector)
    return as_coordinates(-vector if vector[2] < 0 else vector)


def half_space_from_plane(ifc_file, location, normal, unit_scale):
    """A half space on the plane through location, cutting away the side the
    normal points at. Both in metres, in whatever frame the caller then hands to
    clip_representation — pass the target's inverse placement there and these
    are world coordinates.

    The X axis is arbitrary (the plane is unbounded) but must not be parallel to
    the normal: picked the way ifcopenshell.util.data.Clipping picks it."""
    reference = (0.0, 1.0, 0.0) if abs(np.array(normal, dtype=float)[2]) > 0.99 else (0.0, 0.0, 1.0)
    x_axis = np.cross(normal, reference)
    builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
    placement = builder.create_axis2_placement_3d(
        as_coordinates(np.array(location, dtype=float) / unit_scale),
        as_coordinates(normal),
        as_coordinates(x_axis / np.linalg.norm(x_axis)),
    )
    return ifc_file.createIfcHalfSpaceSolid(ifc_file.createIfcPlane(placement), False)


def discard_half_space(ifc_file, half_space):
    """Drops a half space that only existed to be copied onto the targets."""
    ifcopenshell.util.element.remove_deep2(ifc_file, half_space)


# --------------------------------------------------------------------------
# Applying the clips to another element

# What ifcopenshell.api.geometry.add_boolean accepts as an operand.
BOOLEAN_OPERANDS = (
    "IfcBooleanResult",
    "IfcCsgPrimitive3D",
    "IfcHalfSpaceSolid",
    "IfcSolidModel",
    "IfcTessellatedFaceSet",
)


def is_boolean_operand(item):
    return any(item.is_a(ifc_class) for ifc_class in BOOLEAN_OPERANDS)


def has_mapped_geometry(representation):
    """True when the representation borrows its geometry from a type: clipping
    it would clip every instance sharing the map."""
    return any(item.is_a("IfcMappedItem") for item in representation.Items)


def representation_type(representation):
    """ "Clipping" while every item is a half-space clip of a swept solid, "CSG"
    as soon as a general boolean is in play (IFC4 4.1.4.2)."""
    if all(item.is_a("IfcBooleanClippingResult") for item in representation.Items):
        return "Clipping"
    return "CSG"


def clip_representation(ifc_file, representation, half_spaces, matrix, unit_scale):
    """Cuts every solid item of the representation with its own copy of each
    half space, moved into the representation's frame by matrix. Items that
    cannot carry a boolean (curves, mapped items, surface models) are left
    alone and cost nothing. Returns the booleans created."""
    booleans = []
    for item in list(representation.Items):
        if not is_boolean_operand(item):
            continue
        copies = [transform_half_space(ifc_file, half_space, matrix, unit_scale) for half_space in half_spaces]
        booleans += ifcopenshell.api.geometry.add_boolean(ifc_file, item, copies, "DIFFERENCE")
    if booleans:
        representation.RepresentationType = representation_type(representation)
    return booleans
