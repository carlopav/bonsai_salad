import json

import pytest

import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.type

from ifc_cleanup import opening_dimensions


@pytest.fixture
def add_opening_type(ifc_file, unit_scale):
    """A door/window type carrying the BBIM pset Bonsai's parametric editor
    writes: one 'Data' property holding a JSON blob in project units."""

    def add(ifc_class="IfcDoorType", width=0.9, height=2.15, data=None, pset_name=None):
        element_type = ifcopenshell.api.root.create_entity(ifc_file, ifc_class=ifc_class, name="Type")
        if pset_name is None:
            pset_name = f"BBIM_{ifc_class[3:-4]}"
        if data is None:
            data = json.dumps({"overall_width": width / unit_scale, "overall_height": height / unit_scale})
        if data is not False:
            pset = ifcopenshell.api.pset.add_pset(ifc_file, product=element_type, name=pset_name)
            ifcopenshell.api.pset.edit_pset(
                ifc_file, pset=pset, properties={"Data": ifc_file.createIfcText(data)}
            )
        return element_type

    return add


@pytest.fixture
def add_opening(ifc_file, unit_scale):
    """A door/window occurrence, optionally of a type, with the stale
    dimensions it was last written with (metres, None to leave them unset)."""

    def add(ifc_class="IfcDoor", element_type=None, width=0.8, height=2.0):
        element = ifcopenshell.api.root.create_entity(ifc_file, ifc_class=ifc_class, name="Opening")
        element.OverallWidth = width / unit_scale if width is not None else None
        element.OverallHeight = height / unit_scale if height is not None else None
        if element_type is not None:
            ifcopenshell.api.type.assign_type(
                ifc_file, related_objects=[element], relating_type=element_type, should_map_representations=False
            )
        return element

    return add


@pytest.mark.parametrize("unit_prefix", [None, "MILLI"], indirect=True)
def test_reads_the_dimensions_off_the_type(add_opening_type, add_opening, unit_scale):
    element = add_opening(element_type=add_opening_type())

    assert opening_dimensions.type_dimensions(element) == pytest.approx((0.9 / unit_scale, 2.15 / unit_scale))


def test_reads_a_window_from_its_own_pset(add_opening_type, add_opening):
    element_type = add_opening_type("IfcWindowType", width=1.6, height=0.8)
    element = add_opening("IfcWindow", element_type)

    assert opening_dimensions.type_dimensions(element) == pytest.approx((1.6, 0.8))


@pytest.mark.parametrize("unit_prefix", [None, "MILLI"], indirect=True)
def test_writes_the_type_dimensions_onto_the_occurrence(add_opening_type, add_opening, unit_scale):
    element = add_opening(element_type=add_opening_type())

    assert opening_dimensions.sync_dimensions(element, unit_scale=unit_scale) == "synced"
    assert element.OverallWidth == pytest.approx(0.9 / unit_scale)
    assert element.OverallHeight == pytest.approx(2.15 / unit_scale)


def test_an_occurrence_already_in_sync_is_left_alone(add_opening_type, add_opening):
    element = add_opening(element_type=add_opening_type(), width=0.9, height=2.15)

    assert opening_dimensions.sync_dimensions(element) == "unchanged"


def test_unset_dimensions_count_as_out_of_sync(add_opening_type, add_opening):
    element = add_opening(element_type=add_opening_type(), width=None, height=None)

    assert opening_dimensions.sync_dimensions(element) == "synced"
    assert element.OverallWidth == pytest.approx(0.9)


@pytest.mark.parametrize("unit_prefix", [None, "MILLI"], indirect=True)
def test_the_correction_records_the_net_passage(add_opening_type, add_opening, unit_scale):
    element = add_opening(element_type=add_opening_type())

    outcome = opening_dimensions.sync_dimensions(element, -0.10, -0.05, unit_scale=unit_scale)

    assert outcome == "synced"
    assert element.OverallWidth == pytest.approx(0.80 / unit_scale)
    assert element.OverallHeight == pytest.approx(2.10 / unit_scale)


def test_a_correction_matching_the_stale_value_still_counts_as_synced(add_opening_type, add_opening):
    """The occurrence happens to already hold the corrected value: nothing to
    write, and the run must not claim otherwise."""
    element = add_opening(element_type=add_opening_type(), width=0.8, height=2.10)

    assert opening_dimensions.sync_dimensions(element, -0.10, -0.05) == "unchanged"


def test_a_correction_that_wipes_out_the_opening_is_refused(add_opening_type, add_opening):
    element = add_opening(element_type=add_opening_type())

    assert opening_dimensions.sync_dimensions(element, -0.9) == "invalid"
    assert element.OverallWidth == pytest.approx(0.8)
    assert element.OverallHeight == pytest.approx(2.0)


def test_an_untyped_occurrence_is_left_alone(add_opening):
    element = add_opening()

    assert opening_dimensions.type_dimensions(element) is None
    assert opening_dimensions.sync_dimensions(element) == "no_type_data"
    assert element.OverallWidth == pytest.approx(0.8)


def test_a_type_without_the_bbim_pset_is_left_alone(add_opening_type, add_opening):
    """Types imported from other authoring tools carry no parametric data."""
    element = add_opening(element_type=add_opening_type(data=False))

    assert opening_dimensions.sync_dimensions(element) == "no_type_data"


def test_a_pset_of_another_name_is_not_read(add_opening_type, add_opening):
    element = add_opening(element_type=add_opening_type(pset_name="BBIM_Window"))

    assert opening_dimensions.type_dimensions(element) is None


@pytest.mark.parametrize(
    "data",
    ["not json at all", "{}", '{"overall_width": 0.9}', '{"overall_width": null, "overall_height": 2.15}'],
)
def test_unusable_type_data_is_left_alone(add_opening_type, add_opening, data):
    element = add_opening(element_type=add_opening_type(data=data))

    assert opening_dimensions.type_dimensions(element) is None
    assert opening_dimensions.sync_dimensions(element) == "no_type_data"
