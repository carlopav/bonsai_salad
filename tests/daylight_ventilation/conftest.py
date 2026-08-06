import numpy as np
import pytest

import ifcopenshell
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import ifcopenshell.util.representation
import ifcopenshell.util.shape_builder

SI_UNITS = (
    ("LENGTHUNIT", "METRE"),
    ("AREAUNIT", "SQUARE_METRE"),
    ("VOLUMEUNIT", "CUBIC_METRE"),
    ("PLANEANGLEUNIT", "RADIAN"),
)


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


def placement(x=0.0, y=0.0, z=0.0, angle=0.0):
    """A 4x4 placement: a rotation about Z, then a translation."""
    matrix = np.eye(4)
    cos, sin = np.cos(angle), np.sin(angle)
    matrix[:2, :2] = [[cos, -sin], [sin, cos]]
    matrix[:3, 3] = (x, y, z)
    return matrix


@pytest.fixture
def add_box(ifc_file):
    """A box of the given IFC class, extruded from a rectangle in its own local
    frame: local X is the length, local Y the thickness, local Z the height."""
    builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
    body = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")

    def add(ifc_class, length, thickness, height, matrix=None, name=None):
        element = ifcopenshell.api.root.create_entity(ifc_file, ifc_class=ifc_class, name=name)
        outline = [(0.0, 0.0), (length, 0.0), (length, thickness), (0.0, thickness)]
        profile = builder.profile(builder.polyline(outline, closed=True))
        representation = builder.get_representation(body, [builder.extrude(profile, magnitude=height)])
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=element, representation=representation)
        ifcopenshell.api.geometry.edit_object_placement(
            ifc_file, product=element, matrix=placement() if matrix is None else matrix
        )
        return element

    return add


@pytest.fixture
def add_tapered_opening(ifc_file):
    """An opening whose section narrows along its own local Y: a face set built
    from two rectangles, the near one `near` wide and the far one `far` wide,
    both `height` tall, spanning `depth` along Y from the local origin."""
    body = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")

    def add(near, far, height, depth, matrix=None):
        element = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcOpeningElement")
        points = [
            (-near / 2, 0.0, 0.0),
            (near / 2, 0.0, 0.0),
            (near / 2, 0.0, height),
            (-near / 2, 0.0, height),
            (-far / 2, depth, 0.0),
            (far / 2, depth, 0.0),
            (far / 2, depth, height),
            (-far / 2, depth, height),
        ]
        # Reversed relative to the brief's listing: as originally ordered every
        # face's normal points into the solid, so section_area's outward-winding
        # convention read the whole void with its sign flipped.
        faces = [
            [2, 3, 4, 1],
            [8, 7, 6, 5],
            [5, 6, 2, 1],
            [6, 7, 3, 2],
            [7, 8, 4, 3],
            [8, 5, 1, 4],
        ]
        face_set = ifc_file.createIfcPolygonalFaceSet(
            ifc_file.createIfcCartesianPointList3D([tuple(map(float, p)) for p in points]),
            None,
            [ifc_file.createIfcIndexedPolygonalFace(list(face)) for face in faces],
        )
        representation = ifc_file.createIfcShapeRepresentation(body, "Body", "Tessellation", [face_set])
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=element, representation=representation)
        ifcopenshell.api.geometry.edit_object_placement(
            ifc_file, product=element, matrix=placement() if matrix is None else matrix
        )
        return element

    return add


@pytest.fixture
def add_space(ifc_file):
    """A room as an extruded rectangle, placed by its lower left corner."""
    builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
    body = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")

    def add(name, width, depth, height=3.0, matrix=None, long_name=None):
        space = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSpace", name=name)
        space.LongName = long_name
        outline = [(0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth)]
        profile = builder.profile(builder.polyline(outline, closed=True))
        representation = builder.get_representation(body, [builder.extrude(profile, magnitude=height)])
        ifcopenshell.api.geometry.assign_representation(
            ifc_file, product=space, representation=representation
        )
        ifcopenshell.api.geometry.edit_object_placement(
            ifc_file, product=space, matrix=placement() if matrix is None else matrix
        )
        return space

    return add


@pytest.fixture
def fill(ifc_file):
    """Voids a host with an opening and fills the opening with a filling."""

    def do(host, opening, filling):
        ifcopenshell.api.feature.add_feature(ifc_file, feature=opening, element=host)
        ifcopenshell.api.feature.add_filling(ifc_file, opening=opening, element=filling)

    return do
