import ifcopenshell
import ifcopenshell.guid

from daylight_ventilation.core import spaces


def test_a_plain_space_is_a_room(add_space):
    assert spaces.is_room(add_space("A", width=4.0, depth=3.0)) is True


def test_a_space_marked_external_is_outdoors(add_space):
    loggia = add_space("Loggia", width=4.0, depth=3.0, predefined_type="EXTERNAL")
    assert spaces.is_room(loggia) is False


def test_only_the_rooms_are_listed(ifc_file, add_space):
    room = add_space("A", width=4.0, depth=3.0)
    add_space("Loggia", width=4.0, depth=3.0, predefined_type="EXTERNAL")
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
