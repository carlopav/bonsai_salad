# Bonsai Salad — daylight_ventilation tool

"""Which IfcSpaces the check is about.

A balcony, a loggia, a portico or a terrace is outdoor space that happens to be
modelled as a room. It has no daylight ratio of its own, and a window onto it
faces outside rather than another room. IFC4 models it properly as an
IfcExternalSpatialElement — never considered here, since only IfcSpace ever is —
but an IfcSpace marked EXTERNAL is common, legitimate, and what this project's
files carry.
"""

OUTDOORS = "EXTERNAL"


def is_room(space):
    """Whether the space is a room rather than outdoor space. IFC2X3 says the
    same thing in InteriorOrExteriorSpace, which IFC4 replaced with
    PredefinedType."""
    kind = getattr(space, "PredefinedType", None) or getattr(space, "InteriorOrExteriorSpace", None)
    return kind != OUTDOORS


def rooms(ifc_file):
    """Every room in the file, in file order."""
    return [space for space in ifc_file.by_type("IfcSpace") if is_room(space)]
