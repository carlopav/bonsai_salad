import pytest

import ifcopenshell.api.aggregate
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.placement

from daylight_ventilation.core import boundaries, ratios

from .conftest import placement


@pytest.fixture
def project(ifc_file, add_box, add_tapered_opening, add_space, fill):
    """One room, one window onto the outside, one orphan window in a free
    standing wall."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    room = add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3), long_name="Soggiorno")
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)

    lonely_wall = add_box("IfcWall", length=2.0, thickness=0.3, height=3.0, matrix=placement(x=50.0))
    lonely_opening = add_tapered_opening(near=0.8, far=0.8, height=1.0, depth=0.3, matrix=placement(x=51.0, z=0.9))
    lonely_window = add_box("IfcWindow", length=0.8, thickness=0.1, height=1.0, matrix=placement(x=50.6, z=0.9))
    fill(lonely_wall, lonely_opening, lonely_window)
    return room, window, lonely_window


def test_quantify_reports_what_it_did(ifc_file, project):
    room, window, orphan = project
    summary = ratios.quantify(ifc_file)
    assert summary.boundaries_written == 1
    assert summary.orphans == [orphan]
    assert summary.unmeasurable == []
    assert summary.disagreeing == []
    (row,) = summary.rows
    assert row.space == room
    assert row.daylight_ratio == pytest.approx(0.15, rel=1e-6)
    assert row.verified is True


@pytest.mark.parametrize("unit_prefix", [None, "MILLI"], indirect=True)
def test_a_room_reads_the_same_whatever_the_project_unit(
    ifc_file, unit_prefix, add_box, add_tapered_opening, add_space, fill
):
    """The metric room's millimetre twin: every area a million times larger,
    every ratio identical. Nothing measured may reach a pset in SI metres."""
    # Profiles and point lists are authored in project units; the placements
    # edit_object_placement takes are SI whatever the file says (is_si=True).
    scale = 1000.0 if unit_prefix == "MILLI" else 1.0
    wall = add_box("IfcWall", length=4.0 * scale, thickness=0.3 * scale, height=3.0 * scale)
    add_space("A1", width=4.0 * scale, depth=3.0 * scale, height=3.0 * scale, matrix=placement(y=0.3))
    opening = add_tapered_opening(
        near=1.2 * scale, far=1.2 * scale, height=1.5 * scale, depth=0.3 * scale, matrix=placement(x=2.0, z=0.9)
    )
    window = add_box(
        "IfcWindow",
        length=1.2 * scale,
        thickness=0.1 * scale,
        height=1.5 * scale,
        matrix=placement(x=1.4, z=0.9),
    )
    fill(wall, opening, window)
    (row,) = ratios.quantify(ifc_file).rows
    assert row.net == pytest.approx(12.0 * scale**2, rel=1e-6)
    assert row.daylight == pytest.approx(1.8 * scale**2, rel=1e-6)
    assert row.daylight_ratio == pytest.approx(0.15, rel=1e-6)
    assert row.verified is True
    pset = ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)
    assert pset[ratios.CLEAR] == pytest.approx(1.8 * scale**2, rel=1e-6)


def test_the_file_itself_says_whether_it_was_ever_quantified(ifc_file, project):
    """What gates the export: a fact of the file, so a button that invalidates
    the panel's cache cannot take the export with it. Preparing an override is
    such a button, and leaves the answer alone."""
    _, window, _ = project
    assert ratios.is_quantified(ifc_file) is False
    ratios.quantify(ifc_file)
    assert ratios.is_quantified(ifc_file) is True
    ratios.prepare_overrides(ifc_file, window)
    assert ratios.is_quantified(ifc_file) is True


def test_a_second_run_changes_nothing(ifc_file, project):
    ratios.quantify(ifc_file)
    before_counts = len(ifc_file.by_type("IfcRelSpaceBoundary")), len(ifc_file.by_type("IfcPropertySet"))
    before = ifc_file.to_string()
    summary = ratios.quantify(ifc_file)
    assert summary.boundaries_written == 0
    assert (len(ifc_file.by_type("IfcRelSpaceBoundary")), len(ifc_file.by_type("IfcPropertySet"))) == before_counts
    assert ifc_file.to_string() == before


def test_a_second_run_leaves_an_override_alone(ifc_file, project):
    _, window, _ = project
    ratios.quantify(ifc_file)
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.6)})
    (row,) = ratios.quantify(ifc_file).rows
    assert row.air == pytest.approx(0.6)
    assert row.daylight == pytest.approx(1.8, rel=1e-6)


def test_a_second_run_leaves_a_prepared_override_alone(ifc_file, project):
    """The whole point of preparing one: the user edits the value the button put
    there, and the next Calcola reads it instead of what it measured."""
    _, window, _ = project
    ratios.quantify(ifc_file)
    assert ratios.prepare_overrides(ifc_file, window) == ratios.PREPARED
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.6)})
    (row,) = ratios.quantify(ifc_file).rows
    assert row.clear == pytest.approx(1.8, rel=1e-6)
    assert row.daylight == pytest.approx(1.8, rel=1e-6)
    assert row.air == pytest.approx(0.6)


def test_preparing_an_unmeasured_filling_leaves_its_room_without_a_verdict(
    ifc_file, add_box, add_tapered_opening, add_space, fill
):
    """Preparing is not measuring: the void still cannot be read, so the room
    must not start claiming a verdict."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    room = add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3), long_name="Camera")
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, y=5.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, y=5.0, z=0.9))
    fill(wall, opening, window)
    ratios.quantify(ifc_file)
    assert ratios.prepare_overrides(ifc_file, window) == ratios.UNMEASURED
    (row,) = ratios.quantify(ifc_file).rows
    assert row.unmeasured_fillings == 1
    assert ratios.VERIFIED not in ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)


def test_a_second_run_leaves_an_edited_requirement_alone(ifc_file, project):
    room, _, _ = project
    ratios.quantify(ifc_file)
    ratios.write_requirements(ifc_file, room, 0.2, 0.2)
    (row,) = ratios.quantify(ifc_file).rows
    assert row.daylight_requirement == pytest.approx(0.2)
    assert row.verified is False


def test_rooms_are_grouped_by_storey(ifc_file, project, add_space):
    room, _, _ = project
    storey = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name="Piano terra")
    ifcopenshell.api.aggregate.assign_object(ifc_file, products=[room], relating_object=storey)
    loose = add_space("A2", width=2.0, depth=1.0, long_name="Ripostiglio")
    rows = ratios.measure_spaces(ifc_file, [room, loose])
    assert [label for label, _ in ratios.sections(ifc_file, rows)] == ["Piano terra", "(nessun piano)"]


def test_quantify_reports_an_unmeasured_void_without_raising(ifc_file, add_box, add_tapered_opening, add_space, fill):
    """The opening and its filling sit clear of the wall's 0 to 0.3 span along
    the thickness axis, so clear_opening_area returns None."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3), long_name="Soggiorno")
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, y=5.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, y=5.0, z=0.9))
    fill(wall, opening, window)
    summary = ratios.quantify(ifc_file)
    assert summary.unmeasurable == [window]
    pset = ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False) or {}
    assert ratios.CLEAR not in pset


def test_the_room_behind_an_unmeasured_void_gets_no_verdict(ifc_file, add_box, add_tapered_opening, add_space, fill):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    dark_room = add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3), long_name="Camera")
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, y=5.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, y=5.0, z=0.9))
    fill(wall, opening, window)

    lit_wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0, matrix=placement(x=20.0))
    lit_room = add_space("A2", width=4.0, depth=3.0, matrix=placement(x=20.0, y=0.3), long_name="Soggiorno")
    lit_opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=22.0, z=0.9))
    lit_window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=21.4, z=0.9))
    fill(lit_wall, lit_opening, lit_window)

    ratios.quantify(ifc_file)
    dark_pset = ifcopenshell.util.element.get_pset(dark_room, ratios.SPACE_PSET, should_inherit=False)
    lit_pset = ifcopenshell.util.element.get_pset(lit_room, ratios.SPACE_PSET, should_inherit=False)
    assert ratios.VERIFIED not in dark_pset
    assert lit_pset[ratios.VERIFIED] is True


def test_a_void_that_stops_being_measurable_loses_its_clear_opening(
    ifc_file, add_box, add_tapered_opening, add_space, fill
):
    """A measurement left standing after the model outran it reads as a correct
    answer: the room passes, the panel counts no room without a verdict, and the
    schedule prints the stale number as what the geometry measured."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    room = add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3), long_name="Camera")
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)
    ratios.quantify(ifc_file)
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.6)})

    # The void moves clear of its host along the wall's thickness: nothing to
    # measure any more.
    for element in (opening, window):
        matrix = placement(y=5.0) @ ifcopenshell.util.placement.get_local_placement(element.ObjectPlacement)
        ifcopenshell.api.geometry.edit_object_placement(ifc_file, product=element, matrix=matrix)

    summary = ratios.quantify(ifc_file)
    assert summary.unmeasurable == [window]
    pset = ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)
    assert ratios.CLEAR not in pset
    assert pset[ratios.AIR] == pytest.approx(0.6)
    (row,) = summary.rows
    assert row.clear == pytest.approx(0.0)
    assert row.unmeasured_fillings == 1
    assert ratios.VERIFIED not in ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)


def test_typing_both_overrides_gives_an_unmeasurable_room_its_verdict(
    ifc_file, add_box, add_tapered_opening, add_space, fill
):
    """The whole path the README promises, end to end: the void cannot be
    measured, the user types both areas by hand, and the next run gives the room
    a real verdict instead of leaving it stuck without one."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    room = add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3), long_name="Camera")
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, y=5.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, y=5.0, z=0.9))
    fill(wall, opening, window)
    (row,) = ratios.quantify(ifc_file).rows
    assert row.unmeasured_fillings == 1
    assert ratios.VERIFIED not in ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)

    pset = ifcopenshell.api.pset.add_pset(ifc_file, product=window, name=ratios.FILLING_PSET)
    ifcopenshell.api.pset.edit_pset(
        ifc_file,
        pset=pset,
        properties={
            ratios.DAYLIGHT: ifc_file.createIfcAreaMeasure(1.8),
            ratios.AIR: ifc_file.createIfcAreaMeasure(1.8),
        },
    )
    (row,) = ratios.quantify(ifc_file).rows
    assert row.daylight == pytest.approx(1.8)
    assert row.unmeasured_fillings == 0
    assert row.verified is True
    assert ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)[ratios.VERIFIED] is True


def test_a_wall_boundary_leaves_every_room_its_verdict(ifc_file, project):
    """What a Revit or ArchiCAD export writes: the room is bounded by its wall
    as well as by its window. The wall carries no clear opening, and counting it
    as an unmeasured filling would strip the verdict from every room."""
    room, _, _ = project
    wall = ifc_file.by_type("IfcWall")[0]
    boundaries.add(ifc_file, room, wall, external=True)
    (row,) = ratios.quantify(ifc_file).rows
    assert row.unmeasured_fillings == 0
    assert row.verified is True
    pset = ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)
    assert pset[ratios.VERIFIED] is True


def test_storeys_are_ordered_by_elevation_not_creation(ifc_file, add_space):
    """The upper storey is created first, so file order would put it first too."""
    upper = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name="Primo piano")
    upper.Elevation = 3.0
    lower = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name="Piano terra")
    lower.Elevation = 0.0
    room_upper = add_space("A1", width=2.0, depth=1.0, long_name="Camera")
    room_lower = add_space("A2", width=2.0, depth=1.0, matrix=placement(x=10.0), long_name="Soggiorno")
    ifcopenshell.api.aggregate.assign_object(ifc_file, products=[room_upper], relating_object=upper)
    ifcopenshell.api.aggregate.assign_object(ifc_file, products=[room_lower], relating_object=lower)
    rows = ratios.measure_spaces(ifc_file, [room_upper, room_lower])
    assert [label for label, _ in ratios.sections(ifc_file, rows)] == ["Piano terra", "Primo piano"]


def test_a_space_attached_by_containment_still_finds_its_storey(ifc_file, add_space):
    """Schema-invalid — WR31 forbids relating an IfcSpace this way — but an
    imported file may still do it, and the fallback is cheap."""
    storey = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name="Piano terra")
    room = add_space("A1", width=2.0, depth=1.0, long_name="Camera")
    ifc_file.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=ifcopenshell.guid.new(),
        RelatedElements=[room],
        RelatingStructure=storey,
    )
    rows = ratios.measure_spaces(ifc_file, [room])
    assert [label for label, _ in ratios.sections(ifc_file, rows)] == ["Piano terra"]


def test_a_space_contained_in_a_building_is_not_given_it_as_a_storey(ifc_file, add_space):
    """The same fallback, one level up: a building is not a storey heading."""
    building = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuilding", name="Edificio")
    room = add_space("A1", width=2.0, depth=1.0, long_name="Camera")
    ifc_file.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=ifcopenshell.guid.new(),
        RelatedElements=[room],
        RelatingStructure=building,
    )
    rows = ratios.measure_spaces(ifc_file, [room])
    assert [label for label, _ in ratios.sections(ifc_file, rows)] == [ratios.NO_STOREY]
