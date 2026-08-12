# Bonsai Salad — daylight_ventilation tool

"""Which IfcSpaces the check is about.

A balcony, a loggia, a portico or a terrace is outdoor space that happens to be
modelled as a room. It has no daylight ratio of its own, and a window onto it
faces outside rather than another room. IFC4 models it properly as an
IfcExternalSpatialElement — never considered here, since only IfcSpace ever is —
but an IfcSpace marked EXTERNAL is common, legitimate, and what this project's
files carry. A parking bay is the same kind of thing, and GFA is not a room at
all: it overlays a floor for an area take-off.

The rule names what is not a room rather than admitting only INTERNAL. A project
that carries its own classification in USERDEFINED, or an exporter that leaves
the type unset, still has rooms; admitting only INTERNAL would drop them from the
check without a word on screen, which is the one failure this tool is built to
avoid. A room that the regulation asks nothing of — a garage, a cellar — is
already covered by setting its requirement to 0, where the table still shows it.
"""

NOT_ROOMS = ("EXTERNAL", "PARKING", "GFA")


def is_room(space):
    """Whether the space is a room rather than outdoor space, a parking bay or a
    floor-area overlay. IFC2X3 says INTERNAL or EXTERNAL in
    InteriorOrExteriorSpace, which IFC4 replaced with PredefinedType."""
    kind = getattr(space, "PredefinedType", None) or getattr(space, "InteriorOrExteriorSpace", None)
    return kind not in NOT_ROOMS


def rooms(ifc_file):
    """Every room in the file, in file order."""
    return [space for space in ifc_file.by_type("IfcSpace") if is_room(space)]
