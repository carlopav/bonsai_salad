import sys
import types

import numpy as np
import pytest

import ifcopenshell
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.util.representation
import ifcopenshell.util.shape_builder

# A metric project: the quantities come back in project units, and metres keep
# the expected numbers readable.
SI_UNITS = (
    ("LENGTHUNIT", "METRE"),
    ("AREAUNIT", "SQUARE_METRE"),
    ("VOLUMEUNIT", "CUBIC_METRE"),
    ("PLANEANGLEUNIT", "RADIAN"),
)


def _stub_ifc5d():
    """ifc5d ships with Bonsai, not with the test environment. measure_zones
    borrows one thing from it, the SI to project unit converter, and the
    fixtures build metric projects where that conversion is the identity: a
    stub keeps the pipeline testable without pretending to reimplement it."""
    try:
        import ifc5d.qto  # noqa: F401

        return
    except ImportError:
        pass

    qto = types.ModuleType("ifc5d.qto")

    class SI2ProjectUnitConverter:
        def __init__(self, ifc_file):
            self.ifc_file = ifc_file

        def convert(self, value, measure):
            return value

    qto.SI2ProjectUnitConverter = SI2ProjectUnitConverter
    package = types.ModuleType("ifc5d")
    package.qto = qto
    sys.modules["ifc5d"], sys.modules["ifc5d.qto"] = package, qto


_stub_ifc5d()


def build_file(schema="IFC4"):
    ifc_file = ifcopenshell.file(schema=schema)
    ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcProject", name="Test")
    ifcopenshell.api.unit.assign_unit(
        ifc_file, units=[ifc_file.createIfcSIUnit(None, kind, None, name) for kind, name in SI_UNITS]
    )
    model = ifcopenshell.api.context.add_context(ifc_file, context_type="Model")
    ifcopenshell.api.context.add_context(
        ifc_file, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model
    )
    return ifc_file


@pytest.fixture
def ifc_file():
    return build_file()


# A 10 x 5 x 3 box as a face set, wound outwards. Indices are 1 based, the way
# IfcPolygonalFaceSet counts them.
BOX_POINTS = [
    (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 5.0, 0.0), (0.0, 5.0, 0.0),
    (0.0, 0.0, 3.0), (10.0, 0.0, 3.0), (10.0, 5.0, 3.0), (0.0, 5.0, 3.0),
]
BOX_FACES = [
    [1, 4, 3, 2],  # bottom
    [5, 6, 7, 8],  # top
    [1, 2, 6, 5],  # front
    [2, 3, 7, 6],  # right
    [3, 4, 8, 7],  # back
    [4, 1, 5, 8],  # left
]


@pytest.fixture
def add_tessellated_zone(ifc_file):
    """Adds a spatial zone whose body is a polygonal face set, so a test can
    hand it a mesh that is deliberately not a solid."""
    body = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")

    def add(name, points=BOX_POINTS, faces=BOX_FACES):
        zone = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSpatialZone", name=name)
        face_set = ifc_file.createIfcPolygonalFaceSet(
            ifc_file.createIfcCartesianPointList3D([tuple(p) for p in points]),
            None,
            [ifc_file.createIfcIndexedPolygonalFace(list(face)) for face in faces],
        )
        representation = ifc_file.createIfcShapeRepresentation(body, "Body", "Tessellation", [face_set])
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=zone, representation=representation)
        ifcopenshell.api.geometry.edit_object_placement(ifc_file, product=zone, matrix=np.eye(4))
        return zone

    return add


@pytest.fixture
def add_zone(ifc_file):
    """Adds a spatial zone extruding the given closed outline."""
    builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
    body = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")

    def add(name, outline, height, elevation=0.0, object_type=None):
        zone = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSpatialZone", name=name)
        zone.ObjectType = object_type
        profile = builder.profile(builder.polyline(outline, closed=True))
        representation = builder.get_representation(body, [builder.extrude(profile, magnitude=height)])
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=zone, representation=representation)
        matrix = np.eye(4)
        matrix[2, 3] = elevation
        ifcopenshell.api.geometry.edit_object_placement(ifc_file, product=zone, matrix=matrix)
        return zone

    return add
