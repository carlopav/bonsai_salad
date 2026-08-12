# Bonsai Salad — daylight_ventilation tool

"""The space a window or a door serves, as IfcRelSpaceBoundary.

A boundary already in the file is the association, whoever wrote it — this tool,
or an export from another authoring tool, 1st level or 2nd. One is only ever
created where none exists, and only `refresh` removes one, on an explicit click.
"""

import ifcopenshell.api.boundary
import ifcopenshell.api.root

from . import openings, spaces

EXTERNAL = "EXTERNAL"
INTERNAL = "INTERNAL"
PHYSICAL = "PHYSICAL"


def of(filling):
    """The filling's boundaries towards a room. IfcRelSpaceBoundary also accepts
    an IfcExternalSpatialElement as its relating space, which is not a room and
    has no floor to take a ratio over."""
    return [boundary for boundary in filling.ProvidesBoundaries if boundary.RelatingSpace.is_a("IfcSpace")]


def rooms_of(filling):
    return [boundary.RelatingSpace for boundary in of(filling)]


def _is_filling(element):
    return any(element.is_a(ifc_class) for ifc_class in openings.FILLING_CLASSES)


def serves(space):
    """The fillings that light and ventilate a room: the windows and doors whose
    boundary to it is EXTERNAL. The other EXTERNAL_* values are not sources of
    daylight.

    A 2nd level boundary set — what an export from another authoring tool
    writes — bounds the room by its walls and slabs as well, and those are not
    fillings whose clear opening could ever be measured."""
    found = [
        boundary.RelatedBuildingElement
        for boundary in space.BoundedBy
        if boundary.InternalOrExternalBoundary == EXTERNAL
        and boundary.RelatedBuildingElement
        and _is_filling(boundary.RelatedBuildingElement)
    ]
    return list(dict.fromkeys(found))


def add(ifc_file, space, filling, external):
    """A boundary with no connection geometry: the attribute is optional, and
    the contact surface is not something the probe knows."""
    boundary = ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcRelSpaceBoundary")
    ifcopenshell.api.boundary.edit_attributes(
        ifc_file,
        entity=boundary,
        relating_space=space,
        related_building_element=filling,
        physical_or_virtual=PHYSICAL,
        internal_or_external=EXTERNAL if external else INTERNAL,
    )
    return boundary


def write_missing(ifc_file, proposals):
    """Creates the boundaries of the fillings that have none. Returns how many
    were created."""
    written = 0
    for proposal in proposals:
        if of(proposal.filling):
            continue
        for room in proposal.rooms:
            add(ifc_file, room, proposal.filling, proposal.external)
            written += 1
    return written


def disagreeing(proposals):
    """The proposals whose filling is bound to rooms the probe did not find and
    to none that it did, or to a space the check does not treat as a room.

    A different RelatingSpace is a geometric fact and the boundary is simply
    wrong; a different InternalOrExternalBoundary is a regulatory judgement and
    belongs to the user, so a loggia corrected by hand — one boundary kept out of
    two — still shares a room with the probe and is never reported. A boundary to
    a space that is not a room is neither: it was written when the tool took a
    balcony or a parking bay for one, it stands between the window and the only
    room it serves, and only refresh can replace it.

    Both arms need a room from the probe to put in the boundary's place: refresh
    removes what it reports, and must never leave a filling with nothing.
    """
    found = []
    for proposal in proposals:
        bound = set(rooms_of(proposal.filling))
        if not bound or not proposal.rooms:
            continue
        if not bound & set(proposal.rooms) or not all(spaces.is_room(space) for space in bound):
            found.append(proposal)
    return found


def is_authored_elsewhere(boundary):
    """Whether the boundary holds more than this tool could ever write back: a
    contact surface, or the 2nd level pairing with the boundary on the other
    side. Removing one destroys information the probe cannot recreate."""
    return boundary.ConnectionGeometry is not None or boundary.is_a("IfcRelSpaceBoundary2ndLevel")


def refresh(ifc_file, proposals):
    """Removes each filling's boundaries and writes the computed ones. The only
    thing in the tool that deletes a boundary, and only ever on an explicit
    click.

    A filling with even one boundary authored elsewhere is left untouched
    whole: replacing the rest of its boundaries would assert the new room
    beside the old one rather than in its place. Returns (updated, kept)."""
    updated, kept = 0, 0
    for proposal in proposals:
        existing = of(proposal.filling)
        if any(is_authored_elsewhere(boundary) for boundary in existing):
            kept += 1
            continue
        for boundary in existing:
            ifcopenshell.api.boundary.remove_boundary(ifc_file, boundary=boundary)
        for room in proposal.rooms:
            add(ifc_file, room, proposal.filling, proposal.external)
        updated += 1
    return updated, kept
