import pytest

import ifcopenshell.api.material
import ifcopenshell.api.root
import ifcopenshell.api.type

from invarianza_idraulica.core import coefficients


def test_the_material_declares_the_coefficient(ifc_file, add_area, give_material, declare):
    slab = add_area("IfcSlab", 10, 10)
    declare(give_material(slab, "Asfalto"), 0.9)
    found = coefficients.coefficient(slab)
    assert found.value == pytest.approx(0.9)
    assert found.source == coefficients.MATERIAL
    assert found.label == "Asfalto"


def test_the_type_prevails_over_the_material(ifc_file, add_area, give_material, declare):
    slab = add_area("IfcSlab", 10, 10)
    declare(give_material(slab, "Asfalto"), 0.9)
    slab_type = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSlabType", name="Tipo")
    ifcopenshell.api.type.assign_type(ifc_file, related_objects=[slab], relating_type=slab_type)
    declare(slab_type, 0.6)
    found = coefficients.coefficient(slab)
    assert found.value == pytest.approx(0.6)
    assert found.source == coefficients.TYPE


def test_the_instance_prevails_over_the_type(ifc_file, add_area, declare):
    slab = add_area("IfcSlab", 10, 10)
    slab_type = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSlabType", name="Tipo")
    ifcopenshell.api.type.assign_type(ifc_file, related_objects=[slab], relating_type=slab_type)
    declare(slab_type, 0.6)
    declare(slab, 0.2)
    found = coefficients.coefficient(slab)
    assert found.value == pytest.approx(0.2)
    assert found.source == coefficients.INSTANCE


def test_nothing_declared_is_impermeable(ifc_file, add_area):
    """Il valore convenzionale della DGR 2948/2009 per tetti, strade e piazzali,
    non un massimo teorico."""
    slab = add_area("IfcSlab", 10, 10)
    found = coefficients.coefficient(slab)
    assert found.value == pytest.approx(0.9)
    assert found.source == coefficients.PREDEFINED
    assert found.label == coefficients.NO_MATERIAL


def test_the_coefficient_can_be_written_on_a_material(ifc_file, add_area, give_material):
    slab = add_area("IfcSlab", 10, 10)
    coefficients.declare(ifc_file, give_material(slab, "Ghiaia"), 0.6)
    found = coefficients.coefficient(slab)
    assert found.value == pytest.approx(0.6)
    assert found.source == coefficients.MATERIAL


def test_the_coefficient_can_be_written_on_a_type(ifc_file, add_area):
    slab = add_area("IfcSlab", 10, 10)
    slab_type = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSlabType", name="Tipo")
    ifcopenshell.api.type.assign_type(ifc_file, related_objects=[slab], relating_type=slab_type)
    coefficients.declare(ifc_file, slab_type, 0.6)
    assert coefficients.coefficient(slab).source == coefficients.TYPE


def test_the_coefficient_can_be_written_on_an_instance(ifc_file, add_area):
    slab = add_area("IfcSlab", 10, 10)
    coefficients.declare(ifc_file, slab, 0.2)
    found = coefficients.coefficient(slab)
    assert found.value == pytest.approx(0.2)
    assert found.source == coefficients.INSTANCE


def test_writing_twice_leaves_one_property_set(ifc_file, add_area):
    """Due pset omonimi sullo stesso elemento e il lettore ne trova uno a caso."""
    slab = add_area("IfcSlab", 10, 10)
    coefficients.declare(ifc_file, slab, 0.2)
    coefficients.declare(ifc_file, slab, 0.9)
    named = [
        pset
        for pset in ifc_file.by_type("IfcPropertySet")
        if pset.Name == coefficients.PSET_NAME
    ]
    assert len(named) == 1
    assert coefficients.coefficient(slab).value == pytest.approx(0.9)


def test_the_written_value_is_a_real(ifc_file, add_area, give_material):
    """IfcReal: un coefficiente è un numero puro, non un conteggio né una misura."""
    slab = add_area("IfcSlab", 10, 10)
    coefficients.declare(ifc_file, slab, 0.6)
    pset = ifc_file.by_type("IfcPropertySet")[0]
    assert pset.HasProperties[0].NominalValue.is_a() == "IfcReal"


def _layer_set(ifc_file, element, *materials):
    layers = [ifc_file.create_entity("IfcMaterialLayer", Material=m, LayerThickness=0.1) for m in materials]
    layer_set = ifc_file.create_entity("IfcMaterialLayerSet", MaterialLayers=layers)
    ifcopenshell.api.material.assign_material(
        ifc_file, products=[element], type="IfcMaterialLayerSet", material=layer_set
    )
    return layer_set


def test_materials_that_disagree_withhold_the_value(ifc_file, add_area, declare):
    slab = add_area("IfcSlab", 10, 10)
    finish = ifc_file.create_entity("IfcMaterial", Name="Betonelle")
    screed = ifc_file.create_entity("IfcMaterial", Name="Massetto")
    _layer_set(ifc_file, slab, finish, screed)
    # Nessuno dei due vale quanto il default, altrimenti il caso ambiguo non si
    # distinguerebbe da quello in cui il lettore ha scelto uno dei materiali.
    declare(finish, 0.6)
    declare(screed, 0.2)
    found = coefficients.coefficient(slab)
    assert found.value == pytest.approx(coefficients.DEFAULT)
    assert found.source == coefficients.AMBIGUOUS


def test_materials_that_agree_are_not_ambiguous(ifc_file, add_area, declare):
    slab = add_area("IfcSlab", 10, 10)
    finish = ifc_file.create_entity("IfcMaterial", Name="Betonelle")
    screed = ifc_file.create_entity("IfcMaterial", Name="Massetto")
    _layer_set(ifc_file, slab, finish, screed)
    declare(finish, 0.6)
    declare(screed, 0.6)
    found = coefficients.coefficient(slab)
    assert found.value == pytest.approx(0.6)
    assert found.source == coefficients.MATERIAL
    assert found.label == "Betonelle + Massetto"
