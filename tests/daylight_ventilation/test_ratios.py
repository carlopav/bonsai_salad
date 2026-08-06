import pytest

import ifcopenshell.api.pset
import ifcopenshell.util.element

from daylight_ventilation.core import ratios

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
    assert ratios.is_overridden(window) is False


def test_an_override_wins_over_the_clear_opening(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    pset = ifc_file.by_id(
        ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"]
    )
    ifcopenshell.api.pset.edit_pset(
        ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.9)}
    )
    assert ratios.contribution(window) == pytest.approx((1.8, 0.9))
    assert ratios.is_overridden(window) is True


def test_recomputing_leaves_the_override_alone(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    ratios.write_clear_opening(ifc_file, window, 1.8)
    pset = ifc_file.by_id(
        ifcopenshell.util.element.get_pset(window, ratios.FILLING_PSET, should_inherit=False)["id"]
    )
    ifcopenshell.api.pset.edit_pset(
        ifc_file, pset=pset, properties={ratios.AIR: ifc_file.createIfcAreaMeasure(0.9)}
    )
    ratios.write_clear_opening(ifc_file, window, 2.4)
    assert ratios.contribution(window) == pytest.approx((2.4, 0.9))


def test_a_filling_with_no_pset_contributes_nothing(ifc_file, add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    assert ratios.contribution(window) == pytest.approx((0.0, 0.0))
