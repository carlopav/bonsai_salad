import pytest

import ifcopenshell.api.material
import ifcopenshell.api.root

from invarianza_idraulica.core import coefficients, surfaces


def zone(add_area, width=20, depth=10):
    return add_area("IfcSpatialZone", width, depth, height=0.1, name="Ambito di intervento")


def test_one_surface_covering_the_whole_lot(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    paving = add_area("IfcSlab", 20, 10)
    declare(give_material(paving, "Asfalto"), 0.9)
    measured = surfaces.measure(ifc_file, ambito)
    assert measured.total == pytest.approx(200.0)
    assert [(row.label, row.coefficient, row.area) for row in measured.rows] == [
        ("Asfalto", pytest.approx(0.9), pytest.approx(200.0))
    ]
    assert measured.impermeable == pytest.approx(180.0)
    assert measured.mean_coefficient == pytest.approx(0.9)


def test_two_surfaces_side_by_side_weigh_the_mean(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    paving = add_area("IfcSlab", 10, 10)
    declare(give_material(paving, "Asfalto"), 0.9)
    lawn = add_area("IfcSlab", 10, 10, x=10.0)
    declare(give_material(lawn, "Prato"), 0.2)
    measured = surfaces.measure(ifc_file, ambito)
    assert {row.label: row.area for row in measured.rows} == {
        "Asfalto": pytest.approx(100.0),
        "Prato": pytest.approx(100.0),
    }
    assert measured.impermeable == pytest.approx(110.0)
    assert measured.mean_coefficient == pytest.approx(0.55)


def test_the_higher_surface_wins_and_the_total_does_not_inflate(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    paving = add_area("IfcSlab", 20, 10, z=0.0)
    declare(give_material(paving, "Asfalto"), 0.9)
    deck = add_area("IfcSlab", 10, 10, z=4.0)
    declare(give_material(deck, "Legno"), 0.6)
    measured = surfaces.measure(ifc_file, ambito)
    assert sum(row.area for row in measured.rows) == pytest.approx(200.0)
    assert {row.label: row.area for row in measured.rows} == {
        "Legno": pytest.approx(100.0),
        "Asfalto": pytest.approx(100.0),
    }


def test_a_surface_that_overhangs_is_clipped_to_the_lot(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    paving = add_area("IfcSlab", 40, 10, x=-10.0)
    declare(give_material(paving, "Asfalto"), 0.9)
    measured = surfaces.measure(ifc_file, ambito)
    assert measured.rows[0].area == pytest.approx(200.0)
    assert sum(row.area for row in measured.rows) == pytest.approx(200.0)


def test_what_nothing_covers_is_declared_impermeable(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    paving = add_area("IfcSlab", 10, 10)
    declare(give_material(paving, "Asfalto"), 0.9)
    measured = surfaces.measure(ifc_file, ambito)
    residual = [row for row in measured.rows if row.label == surfaces.RESIDUAL]
    assert len(residual) == 1
    assert residual[0].area == pytest.approx(100.0)
    assert residual[0].coefficient == pytest.approx(coefficients.DEFAULT)
    assert measured.impermeable == pytest.approx(180.0)


def test_the_terrain_stays_under_whatever_its_height(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    terrain = add_area("IfcSite", 20, 10, z=0.0, height=40.0)
    declare(give_material(terrain, "Terreno"), 0.1)
    roof = add_area("IfcSlab", 10, 10, z=3.0)
    declare(give_material(roof, "Guaina"), 0.9)
    measured = surfaces.measure(ifc_file, ambito)
    assert {row.label: row.area for row in measured.rows} == {
        "Guaina": pytest.approx(100.0),
        "Terreno": pytest.approx(100.0),
    }


def test_a_roof_wins_over_a_higher_slab(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    slab = add_area("IfcSlab", 20, 10, z=10.0)
    declare(give_material(slab, "Grigliato"), 0.6)
    roof = add_area("IfcRoof", 10, 10, z=1.0)
    declare(give_material(roof, "Guaina"), 0.9)
    measured = surfaces.measure(ifc_file, ambito)
    assert {row.label: row.area for row in measured.rows} == {
        "Guaina": pytest.approx(100.0),
        "Grigliato": pytest.approx(100.0),
    }


def test_solar_devices_win_over_the_roof(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    roof = add_area("IfcRoof", 20, 10, z=6.0)
    declare(give_material(roof, "Guaina"), 0.9)
    panels = add_area("IfcSolarDevice", 8, 10, z=0.0)
    declare(give_material(panels, "Moduli"), 0.3)
    measured = surfaces.measure(ifc_file, ambito)
    assert {row.label: row.area for row in measured.rows} == {
        "Moduli": pytest.approx(80.0),
        "Guaina": pytest.approx(120.0),
    }


def test_furniture_is_ignored(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    paving = add_area("IfcSlab", 20, 10)
    declare(give_material(paving, "Asfalto"), 0.9)
    add_area("IfcFurniture", 5, 5, z=1.0)
    measured = surfaces.measure(ifc_file, ambito)
    assert [row.label for row in measured.rows] == ["Asfalto"]


def test_the_zone_itself_is_not_one_of_its_surfaces(ifc_file, add_area, give_material, declare):
    ambito = zone(add_area)
    paving = add_area("IfcSlab", 20, 10)
    declare(give_material(paving, "Asfalto"), 0.9)
    measured = surfaces.measure(ifc_file, ambito)
    assert [row.label for row in measured.rows] == ["Asfalto"]


def test_disagreeing_materials_are_reported(ifc_file, add_area, declare):
    ambito = zone(add_area)
    slab = add_area("IfcSlab", 20, 10)
    finish = ifc_file.create_entity("IfcMaterial", Name="Betonelle")
    screed = ifc_file.create_entity("IfcMaterial", Name="Massetto")
    layers = [ifc_file.create_entity("IfcMaterialLayer", Material=m, LayerThickness=0.1) for m in (finish, screed)]
    layer_set = ifc_file.create_entity("IfcMaterialLayerSet", MaterialLayers=layers)
    ifcopenshell.api.material.assign_material(
        ifc_file, products=[slab], type="IfcMaterialLayerSet", material=layer_set
    )
    declare(finish, 0.6)
    declare(screed, 0.2)
    measured = surfaces.measure(ifc_file, ambito)
    assert measured.ambiguous == [slab]
    assert measured.rows[0].coefficient == pytest.approx(coefficients.DEFAULT)
    assert measured.rows[0].source == coefficients.AMBIGUOUS


def test_a_zone_without_a_body_is_not_a_lot(ifc_file):
    ambito = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSpatialZone", name="Vuoto")
    with pytest.raises(surfaces.NoBoundary):
        surfaces.measure(ifc_file, ambito)
