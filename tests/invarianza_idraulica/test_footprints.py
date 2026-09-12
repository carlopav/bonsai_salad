import pytest

import ifcopenshell.api.root

from invarianza_idraulica.core import footprints


def test_a_slab_projects_to_its_rectangle(ifc_file, add_area):
    slab = add_area("IfcSlab", 10, 8)
    found = footprints.footprint(slab)
    assert found.polygon.area == pytest.approx(80.0)
    assert found.polygon.is_valid


def test_the_top_is_the_highest_point_of_the_body(ifc_file, add_area):
    slab = add_area("IfcSlab", 10, 8, z=6.0, height=0.3)
    assert footprints.footprint(slab).top == pytest.approx(6.3)


def test_the_footprint_is_in_world_coordinates(ifc_file, add_area):
    slab = add_area("IfcSlab", 10, 8, x=100.0, y=50.0)
    assert footprints.footprint(slab).polygon.bounds == pytest.approx((100.0, 50.0, 110.0, 58.0))


def test_areas_are_square_metres_whatever_the_project_unit(ifc_file, add_area):
    """create_shape converte in metri SI: un file in millimetri darebbe le
    stesse aree, e il tool non deve riconvertirle."""
    slab = add_area("IfcSlab", 10, 8)
    assert footprints.footprint(slab).polygon.area == pytest.approx(80.0)


def test_an_element_without_a_body_has_no_footprint(ifc_file):
    slab = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSlab", name="Vuoto")
    assert footprints.footprint(slab) is None
    assert footprints.has_body(slab) is False


def test_an_element_with_a_body_is_recognised(ifc_file, add_area):
    assert footprints.has_body(add_area("IfcSlab", 1, 1)) is True
