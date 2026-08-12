import ifcopenshell
import ifcopenshell.guid

from daylight_ventilation.core import spaces


def test_a_plain_space_is_a_room(add_space):
    assert spaces.is_room(add_space("A", width=4.0, depth=3.0)) is True


def test_a_space_marked_external_is_outdoors(add_space):
    loggia = add_space("Loggia", width=4.0, depth=3.0, predefined_type="EXTERNAL")
    assert spaces.is_room(loggia) is False


def test_a_parking_bay_is_not_a_room(add_space):
    assert spaces.is_room(add_space("Posto auto", width=5.0, depth=2.5, predefined_type="PARKING")) is False


def test_a_gfa_space_is_not_a_room(add_space):
    """GFA overlays a floor for an area take-off; it encloses nothing and would
    count the storey's windows a second time."""
    assert spaces.is_room(add_space("Piano", width=4.0, depth=3.0, predefined_type="GFA")) is False


def test_a_space_of_any_other_type_is_a_room(add_space):
    """The rule excludes rather than admits: a project that puts its own
    classification in USERDEFINED, or an exporter that leaves the type unset,
    still has rooms, and dropping them from the check says nothing on screen."""
    for kind in ("INTERNAL", "USERDEFINED", "SPACE", "NOTDEFINED", None):
        assert spaces.is_room(add_space(f"A-{kind}", width=4.0, depth=3.0, predefined_type=kind)) is True


def test_only_the_rooms_are_listed(ifc_file, add_space):
    room = add_space("A", width=4.0, depth=3.0)
    add_space("Loggia", width=4.0, depth=3.0, predefined_type="EXTERNAL")
    add_space("Posto auto", width=5.0, depth=2.5, predefined_type="PARKING")
    assert spaces.rooms(ifc_file) == [room]


def test_an_ifc2x3_space_says_it_in_interior_or_exterior_space():
    """The attribute IFC4 replaced with PredefinedType. Reading PredefinedType on
    an IFC2X3 entity raises rather than returning nothing."""
    ifc_file = ifcopenshell.file(schema="IFC2X3")
    balcony = ifc_file.create_entity(
        "IfcSpace", GlobalId=ifcopenshell.guid.new(), CompositionType="ELEMENT", InteriorOrExteriorSpace="EXTERNAL"
    )
    room = ifc_file.create_entity(
        "IfcSpace", GlobalId=ifcopenshell.guid.new(), CompositionType="ELEMENT", InteriorOrExteriorSpace="INTERNAL"
    )
    assert spaces.is_room(balcony) is False
    assert spaces.is_room(room) is True
