import pytest

import ifcopenshell.api.aggregate
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.util.element

from daylight_ventilation.core import ratios

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
    assert summary.unmeasured == []
    assert summary.disagreeing == []
    (row,) = summary.rows
    assert row.space == room
    assert row.daylight_ratio == pytest.approx(0.15, rel=1e-6)
    assert row.verified is True


def test_a_second_run_changes_nothing(ifc_file, project):
    ratios.quantify(ifc_file)
    before = len(ifc_file.by_type("IfcRelSpaceBoundary")), len(ifc_file.by_type("IfcPropertySet"))
    summary = ratios.quantify(ifc_file)
    assert summary.boundaries_written == 0
    assert (len(ifc_file.by_type("IfcRelSpaceBoundary")), len(ifc_file.by_type("IfcPropertySet"))) == before


def test_a_second_run_leaves_an_override_alone(ifc_file, project):
    _, window, _ = project
    ratios.quantify(ifc_file)
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.6)})
    (row,) = ratios.quantify(ifc_file).rows
    assert row.air == pytest.approx(0.6)
    assert row.daylight == pytest.approx(1.8, rel=1e-6)


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
    assert summary.unmeasured == [window]


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
