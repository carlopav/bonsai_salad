import numpy as np
import pytest

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.guid
import ifcopenshell.util.representation
import ifcopenshell.util.shape_builder

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


def placement(x=0.0, y=0.0, z=0.0):
    """A translation in project units: is_si=False keeps it on the same scale as
    the outlines the shape builder writes."""
    matrix = np.eye(4)
    matrix[:3, 3] = (x, y, z)
    return matrix


@pytest.fixture
def add_storey(ifc_file):
    """A storey standing either on its Elevation attribute, on the Z of a
    placement, or on both: a real file often carries only the second."""

    def add(name, elevation=None, z=None):
        storey = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuildingStorey", name=name)
        storey.Elevation = elevation
        if z is not None:
            ifcopenshell.api.geometry.edit_object_placement(
                ifc_file, product=storey, matrix=placement(z=z), is_si=False
            )
        return storey

    return add


@pytest.fixture
def add_building(ifc_file):
    """A building the given storeys belong to."""

    def add(name, storeys):
        building = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcBuilding", name=name)
        ifcopenshell.api.aggregate.assign_object(ifc_file, products=storeys, relating_object=building)
        return building

    return add


@pytest.fixture
def add_space(ifc_file):
    """A room as an extruded rectangle, placed by its lower left corner and
    aggregated into a storey."""
    builder = ifcopenshell.util.shape_builder.ShapeBuilder(ifc_file)
    body = ifcopenshell.util.representation.get_context(ifc_file, "Model", "Body", "MODEL_VIEW")

    def add(name, width, depth, x=0.0, y=0.0, storey=None, height=3.0, long_name=None, predefined_type=None):
        space = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSpace", name=name)
        space.LongName = long_name
        if predefined_type is not None:
            space.PredefinedType = predefined_type
        outline = [(0.0, 0.0), (width, 0.0), (width, depth), (0.0, depth)]
        profile = builder.profile(builder.polyline(outline, closed=True))
        representation = builder.get_representation(body, [builder.extrude(profile, magnitude=height)])
        ifcopenshell.api.geometry.assign_representation(ifc_file, product=space, representation=representation)
        ifcopenshell.api.geometry.edit_object_placement(ifc_file, product=space, matrix=placement(x, y), is_si=False)
        if storey is not None:
            ifcopenshell.api.aggregate.assign_object(ifc_file, products=[space], relating_object=storey)
        return space

    return add


@pytest.fixture
def add_bodiless_space(ifc_file):
    """A room with no representation at all: nothing to take a centroid from."""

    def add(name, storey=None):
        space = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcSpace", name=name)
        if storey is not None:
            ifcopenshell.api.aggregate.assign_object(ifc_file, products=[space], relating_object=storey)
        return space

    return add


@pytest.fixture
def contain(ifc_file):
    """Relates a room to its storey by containment, the way WR31 forbids and
    imported files do anyway: written by hand, the API refuses it."""

    def do(space, storey):
        return ifc_file.createIfcRelContainedInSpatialStructure(
            ifcopenshell.guid.new(), None, None, None, [space], storey
        )

    return do


@pytest.fixture
def write_net_floor_area(ifc_file):
    """The take-off a room may already carry, in project units."""

    def write(space, area):
        qto = ifcopenshell.api.pset.add_qto(ifc_file, product=space, name="Qto_SpaceBaseQuantities")
        ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto, properties={"NetFloorArea": float(area)})

    return write


@pytest.fixture
def write_quantities(ifc_file):
    """Qto_SpaceBaseQuantities as Bonsai's take-off leaves it, one quantity at a
    time: a room may carry the area without the volume, or neither."""

    def write(space, area=None, volume=None):
        properties = {}
        if area is not None:
            properties["NetFloorArea"] = float(area)
        if volume is not None:
            properties["NetVolume"] = float(volume)
        qto = ifcopenshell.api.pset.add_qto(ifc_file, product=space, name="Qto_SpaceBaseQuantities")
        ifcopenshell.api.pset.edit_qto(ifc_file, qto=qto, properties=properties)

    return write


@pytest.fixture
def write_daylight_results(ifc_file):
    """The pset the daylight_ventilation tool leaves on a room. Written here by
    hand: this tool only ever reads it, and a test must be able to leave any of
    its properties out."""

    def write(
        space,
        daylight=None,
        air=None,
        minimum=None,
        daylight_requirement=None,
        air_requirement=None,
        verified=None,
    ):
        properties = {}
        if minimum is not None:
            properties["Superficie minima aeroilluminante"] = ifc_file.createIfcAreaMeasure(float(minimum))
        if daylight is not None:
            properties["Superficie illuminante"] = ifc_file.createIfcAreaMeasure(float(daylight))
        if air is not None:
            properties["Superficie aerante"] = ifc_file.createIfcAreaMeasure(float(air))
        if daylight_requirement is not None:
            properties["Requisito illuminazione"] = ifc_file.createIfcRatioMeasure(float(daylight_requirement))
        if air_requirement is not None:
            properties["Requisito aerazione"] = ifc_file.createIfcRatioMeasure(float(air_requirement))
        if verified is not None:
            properties["Verificato"] = ifc_file.createIfcBoolean(bool(verified))
        pset = ifcopenshell.api.pset.add_pset(ifc_file, product=space, name="Requisiti aeroilluminanti")
        ifcopenshell.api.pset.edit_pset(ifc_file, pset=pset, properties=properties)

    return write
