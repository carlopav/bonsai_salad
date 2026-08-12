import pytest

import ifcopenshell.api.pset
import ifcopenshell.util.element

from daylight_ventilation.core import boundaries, openings, ratios

from .conftest import placement


def test_the_clear_opening_is_written_to_the_pset(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    pset = ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)
    assert pset[ratios.CLEAR] == pytest.approx(1.8)


def test_without_an_override_the_clear_opening_counts_twice(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    assert ratios.contribution(window) == pytest.approx((1.8, 1.8))
    assert ratios.differs_from_measured(window) is False


def test_an_override_wins_over_the_clear_opening(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.9)})
    assert ratios.contribution(window) == pytest.approx((1.8, 0.9))
    assert ratios.differs_from_measured(window) is True


def test_recomputing_leaves_the_override_alone(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.9)})
    ratios.write_clear_opening(ifc_file, window, 2.4)
    assert ratios.contribution(window) == pytest.approx((2.4, 0.9))


def test_preparing_fills_both_overrides_from_the_clear_opening(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    assert ratios.prepare_overrides(ifc_file, window) == ratios.PREPARED
    pset = ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)
    assert pset[ratios.DAYLIGHT] == pytest.approx(1.8)
    assert pset[ratios.AIR] == pytest.approx(1.8)


def test_a_prepared_override_does_not_differ_from_the_measurement(ifc_file, add_box):
    """The panel marks a filling whose counted area is not the measured one:
    pressing the button changes no number, so there is nothing to mark yet."""
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    ratios.prepare_overrides(ifc_file, window)
    assert ratios.differs_from_measured(window) is False


def test_editing_a_prepared_override_makes_it_differ(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    ratios.prepare_overrides(ifc_file, window)
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.9)})
    assert ratios.differs_from_measured(window) is True


def test_preparing_never_overwrites_an_override(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.9)})
    ratios.prepare_overrides(ifc_file, window)
    assert ratios.contribution(window) == pytest.approx((1.8, 0.9))


def test_preparing_a_filling_with_both_overrides_does_nothing(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    ratios.prepare_overrides(ifc_file, window)
    assert ratios.prepare_overrides(ifc_file, window) == ratios.ALREADY_SET


def test_preparing_an_unmeasured_filling_does_nothing(ifc_file, add_box):
    """No clear opening to copy, and preparing must not invent one: the filling
    stays unmeasured, so the room it serves still withholds its verdict."""
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    assert ratios.prepare_overrides(ifc_file, window) == ratios.UNMEASURED
    assert ratios.is_measured(window) is False
    assert ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False) is None


@pytest.mark.parametrize("unit_prefix", ["MILLI"], indirect=True)
def test_a_prepared_override_stays_in_project_units(ifc_file, add_box):
    """The clear opening is copied from a pset into a pset: converting it a
    second time would divide a millimetre project's override by a million."""
    window = add_box("IfcWindow", length=1200.0, thickness=100.0, height=1500.0)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    ratios.prepare_overrides(ifc_file, window)
    assert ratios.contribution(window) == pytest.approx((1.8e6, 1.8e6))


def test_a_filling_with_no_pset_contributes_nothing(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    assert ratios.contribution(window) == pytest.approx((0.0, 0.0))


def test_a_clear_opening_of_zero_is_measured(ifc_file, add_box):
    """is_measured asks whether the property is there, not whether it is
    truthy: a window that really does open onto nothing has been measured, and
    must not withhold its room's verdict."""
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    assert ratios.is_measured(window) is False
    ratios.write_clear_opening(ifc_file, window, 0.0)
    assert ratios.is_measured(window) is True
    assert ratios.contribution(window) == pytest.approx((0.0, 0.0))


def test_removing_the_measurement_keeps_both_overrides(ifc_file, add_box):
    """What the geometry can no longer measure goes; what the user typed stays.
    A blanket purge would take the areas the tool exists to protect."""
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    ratios.prepare_overrides(ifc_file, window)
    ratios.remove_clear_opening(ifc_file, window)
    pset = ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)
    assert ratios.CLEAR not in pset
    assert pset[ratios.DAYLIGHT] == pytest.approx(1.8)
    assert pset[ratios.AIR] == pytest.approx(1.8)
    assert ratios.is_measured(window) is False


def test_removing_the_only_measurement_takes_the_property_set_with_it(ifc_file, add_box):
    """IfcPropertySet.HasProperties is a SET [1:?]: an emptied one would be
    invalid, so the set goes with the last property in it."""
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    ratios.remove_clear_opening(ifc_file, window)
    assert ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False) is None


def test_removing_a_measurement_that_is_not_there_writes_nothing(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.remove_clear_opening(ifc_file, window)
    assert ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False) is None


def test_a_measured_filling_has_a_known_area(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    assert ratios.has_known_area(window) is False
    ratios.write_clear_opening(ifc_file, window, 1.8)
    assert ratios.has_known_area(window) is True


def test_one_override_alone_leaves_the_other_side_unknown(ifc_file, add_box):
    """Typing the ventilation area says nothing about the lighting one: half the
    count is still missing, and the room must stay without a verdict."""
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    pset = ifcopenshell.api.pset.add_pset(ifc_file, product=window, name=ratios.FILLING_PSET)
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(1.8)})
    assert ratios.has_known_area(window) is False


def test_both_overrides_make_an_unmeasurable_filling_known(ifc_file, add_box):
    """The way out the README promises: nothing can measure the void, so the
    user types both areas and the filling stops withholding the room's verdict.
    An override is a known area."""
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    pset = ifcopenshell.api.pset.add_pset(ifc_file, product=window, name=ratios.FILLING_PSET)
    ifcopenshell.api.pset.edit_pset(
        ifc_file,
        pset=pset,
        properties={
            ratios.DAYLIGHT: ifc_file.createIfcAreaMeasure(1.8),
            ratios.AIR: ifc_file.createIfcAreaMeasure(1.8),
        },
    )
    assert ratios.is_measured(window) is False
    assert ratios.has_known_area(window) is True


@pytest.fixture
def lit_room(ifc_file, add_box, add_tapered_opening, add_space, fill):
    """A 4 x 3 room behind a wall, with one 1.2 x 1.5 window: 12 m2 of floor and
    1.8 m2 of clear opening, a ratio of 0.15."""

    def build(long_name="Soggiorno"):
        wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
        room = add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3), long_name=long_name)
        opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
        window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
        fill(wall, opening, window)
        proposals = openings.proposals(ifc_file)
        boundaries.write_missing(ifc_file, proposals)
        for proposal in proposals:
            ratios.write_clear_opening(ifc_file, proposal.filling, proposal.area)
        return room, window

    return build


def test_requirements_default_to_an_eighth(ifc_file, add_space):
    space = add_space("A1", width=4.0, depth=3.0)
    assert ratios.requirements(space) == pytest.approx((0.125, 0.125))


def test_requirements_are_read_back(ifc_file, add_space):
    space = add_space("A1", width=4.0, depth=3.0)
    ratios.write_requirements(ifc_file, space, 0.125, 0.0)
    assert ratios.requirements(space) == pytest.approx((0.125, 0.0))


def test_setting_a_requirement_drops_the_stored_verdict(ifc_file, lit_room):
    """The verdict was reached against the requirement being replaced, and
    nothing recomputes it until the next run: leaving it would publish a
    Verificato against a requirement the room plainly fails."""
    room, _ = lit_room()
    (row,) = ratios.measure_spaces(ifc_file, [room])
    ratios.write_space_results(ifc_file, row)
    assert ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)[ratios.VERIFIED] is True
    ratios.write_requirements(ifc_file, room, 0.9, 0.125)
    pset = ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)
    assert ratios.VERIFIED not in pset
    assert pset[ratios.DAYLIGHT_REQUIREMENT] == pytest.approx(0.9)
    assert pset[ratios.DAYLIGHT_RATIO] == pytest.approx(0.15, rel=1e-6)


def test_the_net_floor_area_comes_from_the_geometry(ifc_file, add_space):
    space = add_space("A1", width=4.0, depth=3.0)
    assert ratios.net_floor_area(ifc_file, space) == pytest.approx(12.0, rel=1e-6)


def test_the_qto_wins_over_the_geometry(ifc_file, add_space):
    import ifcopenshell.api.pset

    space = add_space("A1", width=4.0, depth=3.0)
    qto = ifcopenshell.api.pset.add_qto(ifc_file, product=space, name="Qto_SpaceBaseQuantities")
    ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto, properties={"NetFloorArea": ifc_file.createIfcAreaMeasure(10.0)})
    assert ratios.net_floor_area(ifc_file, space) == pytest.approx(10.0)


@pytest.mark.parametrize("unit_prefix", ["MILLI"], indirect=True)
def test_the_qto_and_the_geometry_agree_in_millimetres(ifc_file, add_space):
    """The geometry kernel answers in SI metres, the Qto is in project units:
    unconverted, the two rooms would differ by a factor of a million."""
    measured = add_space("A1", width=4000.0, depth=3000.0, height=3000.0)
    declared = add_space("A2", width=4000.0, depth=3000.0, height=3000.0, matrix=placement(x=10.0))
    qto = ifcopenshell.api.pset.add_qto(ifc_file, product=declared, name="Qto_SpaceBaseQuantities")
    ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto, properties={"NetFloorArea": ifc_file.createIfcAreaMeasure(12e6)})
    assert ratios.net_floor_area(ifc_file, measured) == pytest.approx(12e6, rel=1e-6)
    assert ratios.net_floor_area(ifc_file, declared) == pytest.approx(12e6)


def test_a_lit_room_passes(ifc_file, lit_room):
    room, _ = lit_room()
    (row,) = ratios.measure_spaces(ifc_file, [room])
    assert row.identification == "A1"
    assert row.name == "Soggiorno"
    assert row.net == pytest.approx(12.0, rel=1e-6)
    assert row.daylight == pytest.approx(1.8, rel=1e-6)
    assert row.daylight_ratio == pytest.approx(0.15, rel=1e-6)
    assert row.verified is True


def test_the_row_carries_the_clear_opening_beside_the_counted_areas(ifc_file, lit_room):
    room, window = lit_room()
    (row,) = ratios.measure_spaces(ifc_file, [room])
    assert row.clear == pytest.approx(1.8, rel=1e-6)
    pset = ifc_file.by_id(ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"])
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.9)})
    (row,) = ratios.measure_spaces(ifc_file, [room])
    assert row.clear == pytest.approx(1.8, rel=1e-6)
    assert row.air == pytest.approx(0.9)


def test_a_prepared_override_survives_a_recalculation(ifc_file, lit_room):
    """What the button trades away: once prepared, the two areas are the user's,
    and a remeasured clear opening no longer reaches the room's count."""
    room, window = lit_room()
    ratios.prepare_overrides(ifc_file, window)
    ratios.write_clear_opening(ifc_file, window, 3.0)
    (row,) = ratios.measure_spaces(ifc_file, [room])
    assert row.clear == pytest.approx(3.0, rel=1e-6)
    assert row.daylight == pytest.approx(1.8, rel=1e-6)
    assert row.air == pytest.approx(1.8, rel=1e-6)


def test_the_headers_name_seven_columns_in_order(ifc_file):
    assert ratios.headers(ifc_file) == [
        "Identificativo",
        "Nome",
        "Superficie netta (m²)",
        "Requisito illuminazione / aerazione (m²)",
        "Superficie illuminante (m²)",
        "Superficie aerante (m²)",
        "Verificato",
    ]


def test_a_dark_room_fails(ifc_file, lit_room, add_space):
    room, _ = lit_room()
    ratios.write_requirements(ifc_file, room, 0.2, 0.125)
    (row,) = ratios.measure_spaces(ifc_file, [room])
    assert row.verified is False


def test_a_room_that_requires_nothing_always_passes(ifc_file, add_space):
    room = add_space("A2", width=2.0, depth=1.0, long_name="Ripostiglio")
    ratios.write_requirements(ifc_file, room, 0.0, 0.0)
    (row,) = ratios.measure_spaces(ifc_file, [room])
    assert row.daylight == pytest.approx(0.0)
    assert row.verified is True


def test_the_results_land_on_the_space(ifc_file, lit_room):
    import ifcopenshell.util.element

    room, _ = lit_room()
    (row,) = ratios.measure_spaces(ifc_file, [room])
    ratios.write_space_results(ifc_file, row)
    pset = ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)
    assert pset[ratios.DAYLIGHT_RATIO] == pytest.approx(0.15, rel=1e-6)
    assert pset[ratios.VERIFIED] is True
    assert pset[ratios.DAYLIGHT_REQUIREMENT] == pytest.approx(0.125)


def test_an_unmeasured_filling_is_counted(ifc_file, add_box, add_tapered_opening, add_space, fill):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    room = add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)
    proposals = openings.proposals(ifc_file)
    boundaries.write_missing(ifc_file, proposals)
    # write_clear_opening deliberately never called: this is what the pipeline
    # leaves behind for a filling whose area came back None.
    (row,) = ratios.measure_spaces(ifc_file, [room])
    assert row.unmeasured_fillings == 1


def test_an_unmeasured_room_gets_no_verdict(ifc_file, add_box, add_tapered_opening, add_space, fill):
    import ifcopenshell.util.element

    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    room = add_space("A1", width=4.0, depth=3.0, matrix=placement(y=0.3))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)
    proposals = openings.proposals(ifc_file)
    boundaries.write_missing(ifc_file, proposals)
    (row,) = ratios.measure_spaces(ifc_file, [room])
    ratios.write_space_results(ifc_file, row)
    pset = ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)
    assert ratios.VERIFIED not in pset


def test_a_fully_measured_room_still_gets_a_verdict(ifc_file, lit_room):
    import ifcopenshell.util.element

    room, _ = lit_room()
    (row,) = ratios.measure_spaces(ifc_file, [room])
    assert row.unmeasured_fillings == 0
    ratios.write_space_results(ifc_file, row)
    pset = ifcopenshell.util.element.get_pset(room, ratios.SPACE_PSET, should_inherit=False)
    assert pset[ratios.VERIFIED] is True
