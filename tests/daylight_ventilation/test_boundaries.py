import pytest

import ifcopenshell.guid

from daylight_ventilation.core import boundaries, openings

from .conftest import placement


@pytest.fixture
def window_in_a_wall(add_box, add_tapered_opening, add_space, fill):
    """A room behind a wall with one window in it, plus a second room far away
    that a test can move the window's boundary to."""

    def build():
        wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
        room = add_space("A", width=4.0, depth=3.0, matrix=placement(y=0.3))
        other = add_space("B", width=4.0, depth=3.0, matrix=placement(x=100.0))
        opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
        window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
        fill(wall, opening, window)
        return window, room, other, wall

    return build


def test_an_external_window_gets_one_external_boundary(ifc_file, window_in_a_wall):
    window, room, _, _ = window_in_a_wall()
    assert boundaries.write_missing(ifc_file, openings.proposals(ifc_file)) == 1
    (boundary,) = boundaries.of(window)
    assert boundary.RelatingSpace == room
    assert boundary.RelatedBuildingElement == window
    assert boundary.PhysicalOrVirtualBoundary == "PHYSICAL"
    assert boundary.InternalOrExternalBoundary == "EXTERNAL"
    assert boundary.ConnectionGeometry is None


def test_a_window_between_two_rooms_gets_two_internal_boundaries(
    ifc_file, add_box, add_tapered_opening, add_space, fill
):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    behind = add_space("A", width=4.0, depth=3.0, matrix=placement(y=0.3))
    front = add_space("B", width=4.0, depth=3.0, matrix=placement(y=-3.0))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)
    boundaries.write_missing(ifc_file, openings.proposals(ifc_file))
    written = boundaries.of(window)
    assert {b.RelatingSpace for b in written} == {behind, front}
    assert {b.InternalOrExternalBoundary for b in written} == {"INTERNAL"}


def test_an_orphan_window_gets_none(ifc_file, add_box, add_tapered_opening, fill):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)
    assert boundaries.write_missing(ifc_file, openings.proposals(ifc_file)) == 0
    assert boundaries.of(window) == []


def test_an_existing_boundary_is_left_untouched(ifc_file, window_in_a_wall):
    window, _, other, _ = window_in_a_wall()
    existing = boundaries.add(ifc_file, other, window, external=False)
    assert boundaries.write_missing(ifc_file, openings.proposals(ifc_file)) == 0
    assert boundaries.of(window) == [existing]
    assert existing.RelatingSpace == other
    assert existing.InternalOrExternalBoundary == "INTERNAL"


def test_a_boundary_naming_another_room_disagrees(ifc_file, window_in_a_wall):
    window, _, other, _ = window_in_a_wall()
    boundaries.add(ifc_file, other, window, external=True)
    disagreeing = boundaries.disagreeing(openings.proposals(ifc_file))
    assert [proposal.filling for proposal in disagreeing] == [window]


def test_a_hand_corrected_loggia_does_not_disagree(ifc_file, add_box, add_tapered_opening, add_space, fill):
    """The probe finds two rooms; the user kept only one boundary and made it
    EXTERNAL. The sets still overlap, so nothing is reported."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    living = add_space("A", width=4.0, depth=3.0, matrix=placement(y=0.3))
    add_space("Loggia", width=4.0, depth=3.0, matrix=placement(y=-3.0))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)
    boundaries.add(ifc_file, living, window, external=True)
    assert boundaries.disagreeing(openings.proposals(ifc_file)) == []


def test_refresh_replaces_a_disagreeing_boundary(ifc_file, window_in_a_wall):
    window, room, other, _ = window_in_a_wall()
    stale = boundaries.add(ifc_file, other, window, external=True)
    # Captured before refresh: a removed entity's STEP id can be reused by a
    # later create, so comparing the stale python handle itself afterwards is
    # unreliable — GlobalId is the entity's durable identity.
    stale_guid = stale.GlobalId
    proposals = openings.proposals(ifc_file)
    assert boundaries.refresh(ifc_file, boundaries.disagreeing(proposals)) == (1, 0)
    (boundary,) = boundaries.of(window)
    assert boundary.GlobalId != stale_guid
    assert boundary.RelatingSpace == room
    assert boundary.InternalOrExternalBoundary == "EXTERNAL"


def test_refresh_leaves_an_imported_boundary_alone(ifc_file, window_in_a_wall):
    """A 2nd level boundary carries a contact surface and a pairing with the
    boundary on the other side, neither of which the probe could write back."""
    window, _, other, _ = window_in_a_wall()
    imported = ifc_file.create_entity(
        "IfcRelSpaceBoundary2ndLevel",
        GlobalId=ifcopenshell.guid.new(),
        RelatingSpace=other,
        RelatedBuildingElement=window,
        PhysicalOrVirtualBoundary="PHYSICAL",
        InternalOrExternalBoundary="EXTERNAL",
    )
    guid = imported.GlobalId
    proposals = openings.proposals(ifc_file)
    assert boundaries.refresh(ifc_file, boundaries.disagreeing(proposals)) == (0, 1)
    assert [boundary.GlobalId for boundary in boundaries.of(window)] == [guid]


def test_refresh_leaves_a_boundary_with_a_contact_surface_alone(ifc_file, window_in_a_wall):
    window, _, other, _ = window_in_a_wall()
    imported = boundaries.add(ifc_file, other, window, external=True)
    imported.ConnectionGeometry = ifc_file.create_entity(
        "IfcConnectionSurfaceGeometry",
        SurfaceOnRelatingElement=ifc_file.create_entity(
            "IfcCurveBoundedPlane",
            BasisSurface=ifc_file.create_entity(
                "IfcPlane",
                Position=ifc_file.create_entity(
                    "IfcAxis2Placement3D", ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0))
                ),
            ),
            OuterBoundary=ifc_file.createIfcPolyline(
                [ifc_file.createIfcCartesianPoint(point) for point in ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 0.0))]
            ),
            InnerBoundaries=[],
        ),
    )
    proposals = openings.proposals(ifc_file)
    assert boundaries.refresh(ifc_file, boundaries.disagreeing(proposals)) == (0, 1)
    assert boundaries.of(window) == [imported]


def test_serves_lists_only_external_boundaries(ifc_file, window_in_a_wall):
    window, room, other, _ = window_in_a_wall()
    boundaries.write_missing(ifc_file, openings.proposals(ifc_file))
    boundaries.add(ifc_file, other, window, external=False)
    assert boundaries.serves(room) == [window]
    assert boundaries.serves(other) == []


def test_serves_ignores_the_wall_of_a_2nd_level_boundary_set(ifc_file, window_in_a_wall):
    """What every Revit or ArchiCAD export writes: the room is bounded by its
    walls too, and a wall is not a filling whose clear opening could be
    measured."""
    window, room, _, wall = window_in_a_wall()
    boundaries.write_missing(ifc_file, openings.proposals(ifc_file))
    boundaries.add(ifc_file, room, wall, external=True)
    assert boundaries.serves(room) == [window]
