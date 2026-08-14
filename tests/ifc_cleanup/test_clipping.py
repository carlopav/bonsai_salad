import numpy as np
import pytest

import ifcopenshell.api.geometry
import ifcopenshell.util.placement

from ifc_cleanup import clipping

from .conftest import placement


def world_frame(axis_placement, matrix, unit_scale):
    """The 4x4 of an IfcAxis2Placement3D in world metres, given the frame the
    element it belongs to sits in."""
    local = ifcopenshell.util.placement.get_axis2placement(axis_placement)
    local[:3, 3] *= unit_scale
    return matrix @ local


def block(ifc_file):
    """A CSG primitive: a valid boolean operand that is not a swept solid."""
    origin = ifc_file.createIfcAxis2Placement3D(ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)))
    return ifc_file.createIfcBlock(origin, 1.0, 1.0, 1.0)


def test_only_the_half_space_operands_count_as_clips(ifc_file, add_element, add_clip, body_of, booleans_of):
    wall = add_element(name="W")
    add_clip(wall, location=(0.0, 0.0, 2.5))
    body = body_of(wall)
    # A solid subtraction in the same chain: a boolean, but not a clipping plane.
    (result,) = ifcopenshell.api.geometry.add_boolean(ifc_file, body.Items[0], [block(ifc_file)], "DIFFERENCE")

    clips = clipping.half_space_clips(booleans_of(body))

    assert [c.is_a() for c in clips] == ["IfcHalfSpaceSolid"]
    assert result.SecondOperand not in clips


def test_the_clips_come_back_in_the_order_they_were_applied(add_element, add_clip, body_of, booleans_of, unit_scale):
    wall = add_element(name="W")
    add_clip(wall, location=(0.0, 0.0, 2.0))
    add_clip(wall, location=(0.0, 0.0, 2.5))

    clips = clipping.half_space_clips(booleans_of(body_of(wall)))

    heights = [c.BaseSurface.Position.Location.Coordinates[2] * unit_scale for c in clips]
    assert heights == pytest.approx([2.0, 2.5])


@pytest.mark.parametrize("unit_prefix", [None, "MILLI"], indirect=True)
def test_a_copied_plane_lands_on_the_same_world_position(
    ifc_file, add_element, add_clip, body_of, booleans_of, unit_scale
):
    source_matrix = placement(x=1.0, y=2.0, angle=np.pi / 6)
    target_matrix = placement(x=7.0, y=-3.0, z=3.0, angle=np.pi / 2)
    source = add_element(name="A", matrix=source_matrix)
    add_element(name="B", matrix=target_matrix)
    add_clip(source, location=(0.0, 0.0, 2.5), normal=(0.4, 0.0, 0.9))
    (clip,) = clipping.half_space_clips(booleans_of(body_of(source)))

    copy = clipping.transform_half_space(ifc_file, clip, np.linalg.inv(target_matrix) @ source_matrix, unit_scale)

    assert world_frame(copy.BaseSurface.Position, target_matrix, unit_scale) == pytest.approx(
        world_frame(clip.BaseSurface.Position, source_matrix, unit_scale)
    )


def test_the_copy_owns_its_geometry_and_leaves_the_source_alone(
    ifc_file, add_element, add_clip, body_of, booleans_of, unit_scale
):
    source = add_element(name="A")
    add_clip(source, location=(0.0, 0.0, 2.5))
    (clip,) = clipping.half_space_clips(booleans_of(body_of(source)))
    before = clip.BaseSurface.Position.Location.Coordinates

    copy = clipping.transform_half_space(ifc_file, clip, placement(z=1.0), unit_scale)

    assert copy != clip
    assert copy.BaseSurface != clip.BaseSurface
    assert clip.BaseSurface.Position.Location.Coordinates == before


def test_a_bounded_clip_carries_its_boundary_frame_along(
    ifc_file, add_element, add_polygonal_clip, body_of, booleans_of, unit_scale
):
    source_matrix = placement(x=1.0, y=2.0)
    target_matrix = placement(x=7.0, angle=np.pi / 2)
    source = add_element(name="A", matrix=source_matrix)
    add_polygonal_clip(source, location=(0.0, 0.0, 2.5))
    (clip,) = clipping.half_space_clips(booleans_of(body_of(source)))

    copy = clipping.transform_half_space(ifc_file, clip, np.linalg.inv(target_matrix) @ source_matrix, unit_scale)

    assert copy.is_a("IfcPolygonalBoundedHalfSpace")
    assert world_frame(copy.Position, target_matrix, unit_scale) == pytest.approx(
        world_frame(clip.Position, source_matrix, unit_scale)
    )
    assert copy.PolygonalBoundary != clip.PolygonalBoundary


# --------------------------------------------------------------------------
# Applying the clips to another element


def test_the_target_takes_the_same_planes_in_its_own_frame(
    ifc_file, add_element, add_clip, body_of, booleans_of, unit_scale
):
    source_matrix = placement(x=1.0, y=2.0)
    target_matrix = placement(x=7.0, z=3.0, angle=np.pi / 2)
    source = add_element(name="A", matrix=source_matrix)
    target = add_element(name="B", matrix=target_matrix)
    add_clip(source, location=(0.0, 0.0, 2.5), normal=(0.4, 0.0, 0.9))
    (clip,) = clipping.half_space_clips(booleans_of(body_of(source)))
    body = body_of(target)

    booleans = clipping.clip_representation(
        ifc_file, body, [clip], np.linalg.inv(target_matrix) @ source_matrix, unit_scale
    )

    assert [b.is_a() for b in booleans] == ["IfcBooleanClippingResult"]
    assert body.Items == (booleans[0],)
    (copied,) = clipping.half_space_clips(booleans_of(body))
    assert world_frame(copied.BaseSurface.Position, target_matrix, unit_scale) == pytest.approx(
        world_frame(clip.BaseSurface.Position, source_matrix, unit_scale)
    )


def test_every_solid_item_of_the_target_is_clipped(ifc_file, add_element, add_clip, body_of, booleans_of, unit_scale):
    source = add_element(name="A")
    add_clip(source, location=(0.0, 0.0, 2.5))
    (clip,) = clipping.half_space_clips(booleans_of(body_of(source)))
    target = add_element(name="B")
    body = body_of(target)
    body.Items = list(body.Items) + [block(ifc_file)]

    booleans = clipping.clip_representation(ifc_file, body, [clip], np.eye(4), unit_scale)

    assert len(booleans) == 2
    assert len({b.SecondOperand.id() for b in booleans}) == 2  # a clip per item, never shared
    assert set(body.Items) == set(booleans)


@pytest.mark.parametrize("items,expected", [(None, "Clipping"), ("block", "CSG")], ids=["swept solid", "csg primitive"])
def test_the_representation_type_follows_what_the_boolean_produced(
    ifc_file, add_element, add_clip, body_of, booleans_of, unit_scale, items, expected
):
    source = add_element(name="A")
    add_clip(source, location=(0.0, 0.0, 2.5))
    (clip,) = clipping.half_space_clips(booleans_of(body_of(source)))
    body = body_of(add_element(name="B"))
    if items == "block":
        body.Items = [block(ifc_file)]

    clipping.clip_representation(ifc_file, body, [clip], np.eye(4), unit_scale)

    assert body.RepresentationType == expected


def test_geometry_that_cannot_take_a_boolean_is_left_alone(
    ifc_file, add_element, add_clip, body_of, booleans_of, unit_scale
):
    source = add_element(name="A")
    add_clip(source, location=(0.0, 0.0, 2.5))
    (clip,) = clipping.half_space_clips(booleans_of(body_of(source)))
    curve = ifc_file.createIfcPolyline([ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0))] * 2)
    annotation = ifc_file.createIfcShapeRepresentation(body_of(source).ContextOfItems, "Body", "Curve3D", [curve])
    before = len(ifc_file.by_type("IfcHalfSpaceSolid"))

    assert clipping.clip_representation(ifc_file, annotation, [clip], np.eye(4), unit_scale) == []
    assert annotation.Items == (curve,)
    assert len(ifc_file.by_type("IfcHalfSpaceSolid")) == before  # no orphan copies left behind


def test_geometry_borrowed_from_the_type_is_recognised(ifc_file, add_element, body_of):
    element = add_element(name="A")
    body = body_of(element)
    mapped = ifc_file.createIfcShapeRepresentation(
        body.ContextOfItems,
        "Body",
        "MappedRepresentation",
        [
            ifc_file.createIfcMappedItem(
                ifc_file.createIfcRepresentationMap(
                    ifc_file.createIfcAxis2Placement3D(ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0))), body
                ),
                ifc_file.createIfcCartesianTransformationOperator3D(
                    None, None, ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)), 1.0, None
                ),
            )
        ],
    )

    assert clipping.has_mapped_geometry(mapped)
    assert not clipping.has_mapped_geometry(body)
