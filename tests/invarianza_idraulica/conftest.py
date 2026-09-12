import numpy as np
import pytest

import ifcopenshell
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.material
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.type
import ifcopenshell.api.unit
import ifcopenshell.util.representation
import ifcopenshell.util.shape_builder

UNIT_KINDS = (
    ("LENGTHUNIT", "METRE"),
    ("AREAUNIT", "SQUARE_METRE"),
    ("VOLUMEUNIT", "CUBIC_METRE"),
)


@pytest.fixture
def ifc_file():
    """Un file in metri: create_shape restituisce metri SI comunque, ma un
    fixture in millimetri renderebbe illeggibili le aree attese nei test."""
    ifc_file = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcProject", name="Test")
    units = [ifc_file.createIfcSIUnit(None, kind, None, name) for kind, name in UNIT_KINDS]
    units.append(ifc_file.createIfcSIUnit(None, "PLANEANGLEUNIT", None, "RADIAN"))
    ifcopenshell.api.unit.assign_unit(ifc_file, units=units)
    model = ifcopenshell.api.context.add_context(ifc_file, context_type="Model")
    ifcopenshell.api.context.add_context(
        ifc_file, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model
    )
    return ifc_file


@pytest.fixture
def add_area(ifc_file):
    """Una lastra orizzontale, posata per l'angolo in basso a sinistra: in
    pianta è un rettangolo width x depth all'origine (x, y), spessa height a
    partire dalla quota z."""
    builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
    body = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")

    def add(ifc_class, width, depth, x=0.0, y=0.0, z=0.0, height=0.3, name=None, predefined_type=None):
        element = ifcopenshell.api.root.create_entity(ifc_file, ifc_class=ifc_class, name=name)
        if predefined_type is not None:
            element.PredefinedType = predefined_type
        outline = [(0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth)]
        profile = builder.profile(builder.polyline(outline, closed=True))
        representation = builder.get_representation(body, [builder.extrude(profile, magnitude=height)])
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=element, representation=representation)
        matrix = np.eye(4)
        matrix[:3, 3] = (x, y, z)
        ifcopenshell.api.geometry.edit_object_placement(ifc_file, product=element, matrix=matrix)
        return element

    return add


@pytest.fixture
def declare(ifc_file):
    """Scrive il coefficiente di deflusso su un'entità qualunque: elemento,
    tipo o materiale. add_pset su un IfcMaterial produce un
    IfcMaterialProperties, che get_psets rilegge."""

    def do(definition, value):
        pset = ifcopenshell.api.pset.add_pset(ifc_file, product=definition, name="Invarianza idraulica")
        ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties={"Coefficiente di deflusso": value})
        return pset

    return do


@pytest.fixture
def give_material(ifc_file):
    """Assegna a un elemento un materiale nuovo, creato col nome dato."""

    def do(element, name):
        material = ifc_file.create_entity("IfcMaterial", Name=name)
        ifcopenshell.api.material.assign_material(
            ifc_file, products=[element], type="IfcMaterial", material=material
        )
        return material

    return do
