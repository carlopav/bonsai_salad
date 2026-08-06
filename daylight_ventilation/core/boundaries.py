# Bonsai Salad — daylight_ventilation tool

"""The space a window or a door serves, as IfcRelSpaceBoundary.

A boundary already in the file is the association, whoever wrote it — this tool,
or an export from another authoring tool, 1st level or 2nd. One is only ever
created where none exists, and only `refresh` removes one, on an explicit click.
"""

import ifcopenshell.api.boundary
import ifcopenshell.api.root

EXTERNAL = "EXTERNAL"
INTERNAL = "INTERNAL"
PHYSICAL = "PHYSICAL"


def of(filling):
    """The filling's boundaries towards a room. IfcRelSpaceBoundary also accepts
    an IfcExternalSpatialElement as its relating space, which is not a room and
    has no floor to take a ratio over."""
    return [
        boundary
        for boundary in filling.ProvidesBoundaries or []
        if boundary.RelatingSpace and boundary.RelatingSpace.is_a("IfcSpace")
    ]


def rooms_of(filling):
    return [boundary.RelatingSpace for boundary in of(filling)]


def serves(space):
    """The fillings that light and ventilate a room: the ones whose boundary to
    it is EXTERNAL. The other EXTERNAL_* values are not sources of daylight."""
    found = [
        boundary.RelatedBuildingElement
        for boundary in space.BoundedBy or []
        if boundary.InternalOrExternalBoundary == EXTERNAL and boundary.RelatedBuildingElement
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
    """The proposals whose filling is bound to rooms the probe did not find, and
    to none that it did.

    Only a disjoint pair is a disagreement. A different RelatingSpace is a
    geometric fact and the boundary is simply wrong; a different
    InternalOrExternalBoundary is a regulatory judgement and belongs to the
    user, so a loggia corrected by hand — one boundary kept out of two — still
    shares a room with the probe and is never reported.
    """
    found = []
    for proposal in proposals:
        bound = set(rooms_of(proposal.filling))
        if bound and proposal.rooms and not bound & set(proposal.rooms):
            found.append(proposal)
    return found


def refresh(ifc_file, proposals):
    """Removes each filling's boundaries and writes the computed ones. The only
    thing in the tool that deletes, and only ever on an explicit click."""
    for proposal in proposals:
        for boundary in of(proposal.filling):
            ifcopenshell.api.boundary.remove_boundary(ifc_file, boundary=boundary)
        for room in proposal.rooms:
            add(ifc_file, room, proposal.filling, proposal.external)
    return len(proposals)
