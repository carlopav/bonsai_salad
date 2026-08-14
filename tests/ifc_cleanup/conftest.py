import numpy as np
import pytest

import ifcopenshell
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.util.representation
import ifcopenshell.util.shape_builder
import ifcopenshell.util.unit

UNIT_KINDS = (
    ("LENGTHUNIT", "METRE"),
    ("AREAUNIT", "SQUARE_METRE"),
    ("VOLUMEUNIT", "CUBIC_METRE"),
)


def build_file(schema="IFC4", prefix=None):
    ifc_file = ifcopenshell.file(schema=schema)
    ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcProject", name="Test")
    units = [ifc_file.createIfcSIUnit(None, kind, prefix, name) for kind, name in UNIT_KINDS]
    units.append(ifc_file.createIfcSIUnit(None, "PLANEANGLEUNIT", None, "RADIAN"))
    ifcopenshell.api.unit.assign_unit(ifc_file, units=units)
    model = ifcopenshell.api.context.add_context(ifc_file, context_type="Model")
    ifcopenshell.api.context.add_context(
        ifc_file, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model
    )
    return ifc_file


@pytest.fixture
def unit_prefix(request):
    """Metres unless a test parametrises it indirectly with an SI prefix."""
    return getattr(request, "param", None)


@pytest.fixture
def ifc_file(unit_prefix):
    return build_file(prefix=unit_prefix)


@pytest.fixture
def unit_scale(ifc_file):
    return ifcopenshell.util.unit.calculate_unit_scale(ifc_file)


def placement(x=0.0, y=0.0, z=0.0, angle=0.0):
    """A 4x4 in metres: translation plus a rotation about Z, the way a Blender
    object's matrix_world reaches the tool."""
    matrix = np.eye(4)
    matrix[:3, :3] = [
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ]
    matrix[:3, 3] = (x, y, z)
    return matrix


@pytest.fixture
def add_element(ifc_file, unit_scale):
    """A solid element as an extruded rectangle: any IFC class, placed by a
    matrix in metres. Outline given in metres, written in project units."""
    builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
    body = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")

    def add(ifc_class="IfcWall", name=None, matrix=None, width=4.0, depth=0.3, height=3.0):
        element = ifcopenshell.api.root.create_entity(ifc_file, ifc_class=ifc_class, name=name)
        w, d, h = width / unit_scale, depth / unit_scale, height / unit_scale
        profile = builder.profile(builder.polyline([(0.0, 0.0), (w, 0.0), (w, d), (0.0, d)], closed=True))
        representation = builder.get_representation(body, [builder.extrude(profile, magnitude=h)])
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=element, representation=representation)
        if matrix is not None:
            ifcopenshell.api.geometry.edit_object_placement(ifc_file, product=element, matrix=matrix, is_si=True)
        return element

    return add


@pytest.fixture
def body_of(ifc_file):
    def get(element):
        return ifcopenshell.util.representation.get_representation(element, "Model", "Body", "MODEL_VIEW")

    return get


@pytest.fixture
def add_clip(ifc_file, unit_scale, body_of):
    """A clipping plane on the element's first body item, through the canonical
    IfcOpenShell path. location/normal in metres and in the element's local
    frame; the normal points at the material being removed."""

    def add(element, location, normal=(0.0, 0.0, 1.0)):
        representation = body_of(element)
        item = representation.Items[0]
        result = ifcopenshell.api.geometry.clip_solid(
            ifc_file,
            item=item,
            location=[c / unit_scale for c in location],
            normal=list(normal),
            element=element,
        )
        representation.Items = [result if i == item else i for i in representation.Items]
        return result

    return add


@pytest.fixture
def add_polygonal_clip(ifc_file, unit_scale, body_of):
    """A bounded clip: an IfcPolygonalBoundedHalfSpace, whose Position frame
    carries the boundary polygon and must travel with the plane."""

    def add(element, location, size=1.0):
        representation = body_of(element)
        item = representation.Items[0]
        origin = ifc_file.createIfcCartesianPoint(tuple(c / unit_scale for c in location))
        s = size / unit_scale
        half_space = ifc_file.createIfcPolygonalBoundedHalfSpace(
            BaseSurface=ifc_file.createIfcPlane(ifc_file.createIfcAxis2Placement3D(origin, None, None)),
            AgreementFlag=False,
            Position=ifc_file.createIfcAxis2Placement3D(origin, None, None),
            PolygonalBoundary=ifc_file.createIfcPolyline(
                [
                    ifc_file.createIfcCartesianPoint((0.0, 0.0)),
                    ifc_file.createIfcCartesianPoint((s, 0.0)),
                    ifc_file.createIfcCartesianPoint((s, s)),
                    ifc_file.createIfcCartesianPoint((0.0, 0.0)),
                ]
            ),
        )
        (result,) = ifcopenshell.api.geometry.add_boolean(ifc_file, item, [half_space], "DIFFERENCE")
        return result

    return add


@pytest.fixture
def booleans_of():
    """The boolean chain of a representation, outermost first — stands in for
    Bonsai's tool.Model.get_booleans, which the operator uses."""

    def get(representation):
        found = []
        items = list(representation.Items)
        while items:
            item = items.pop()
            if item.is_a("IfcBooleanResult"):
                found.append(item)
                items.append(item.FirstOperand)
        return found

    return get
