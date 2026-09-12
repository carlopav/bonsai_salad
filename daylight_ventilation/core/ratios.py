# Bonsai Salad — daylight_ventilation tool

"""The rule half of the check: what each filling contributes, what each room
requires, and whether it passes.

Every quantity exists twice, as a computed property the calculation always
rewrites and an override property only the user writes. An absent override means
the computed value applies — so a recalculation cannot destroy a correction, and
no tolerance comparison is needed to guess which is which.
"""

from collections import namedtuple

import ifcopenshell.api.pset
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.shape
import ifcopenshell.util.unit

from . import boundaries, openings, spaces

FILLING_PSET = "Superfici aeroilluminanti"
CLEAR = "Luce architettonica"
DAYLIGHT = "Superficie illuminante"
AIR = "Superficie aerante"

SPACE_PSET = "Requisiti aeroilluminanti"
DAYLIGHT_REQUIREMENT = "Requisito illuminazione"
AIR_REQUIREMENT = "Requisito aerazione"
DAYLIGHT_RATIO = "Rapporto illuminazione"
AIR_RATIO = "Rapporto aerazione"
MINIMUM = "Superficie minima aeroilluminante"
VERIFIED = "Verificato"

DEFAULT_REQUIREMENT = 0.125
NOT_REQUIRED = 0.0


def _pset(element, name):
    return ifcopenshell.util.element.get_pset(element, name, should_inherit=False) or {}


def _pset_entity(ifc_file, element, name):
    existing = _pset(element, name)
    if existing:
        return ifc_file.by_id(existing["id"])
    return ifcopenshell.api.pset.add_pset(ifc_file, product=element, name=name)


def area_converter(ifc_file):
    """SI to project units. The geometry kernel answers in metres whatever the
    file's LENGTHUNIT, while a stored area — ours or Qto_SpaceBaseQuantities' —
    is in project units, so a millimetre project would otherwise mix the two a
    million apart."""
    from ifc5d.qto import SI2ProjectUnitConverter

    return SI2ProjectUnitConverter(ifc_file)


def write_clear_opening(ifc_file, filling, area):
    """Stores the measured clear opening, given in SI metres, leaving both
    overrides untouched.

    The measure is explicit: a pset of our own has no buildingSMART template,
    and without one an Italian name would be read as a length.
    """
    ifcopenshell.api.pset.edit_pset(
        ifc_file,
        pset=_pset_entity(ifc_file, filling, FILLING_PSET),
        properties={
            CLEAR: ifc_file.createIfcAreaMeasure(area_converter(ifc_file).convert(float(area), "IfcAreaMeasure"))
        },
    )


def remove_clear_opening(ifc_file, filling):
    """Drops a measurement the geometry can no longer make, so nothing counts,
    prints or passes on it.

    Only that one property: purging the set would take the overrides with it,
    and a typed area is the user's. An IfcPropertySet holds at least one
    property, so one left with nothing but the measurement goes with it.
    """
    pset = _pset(filling, FILLING_PSET)
    if CLEAR not in pset:
        return
    entity = ifc_file.by_id(pset["id"])
    if len(entity.HasProperties) == 1:
        ifcopenshell.api.pset.remove_pset(ifc_file, product=filling, pset=entity)
        return
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=entity, properties={CLEAR: None}, should_purge=True)


def clear_opening(filling):
    """What the geometry measured, in project units, zero where nothing was."""
    return float(_pset(filling, FILLING_PSET).get(CLEAR) or 0.0)


def contribution(filling):
    """(daylight, air) as the filling enters the count: the override where the
    user set one, the clear opening everywhere else."""
    pset = _pset(filling, FILLING_PSET)
    clear = float(pset.get(CLEAR) or 0.0)
    daylight = pset.get(DAYLIGHT)
    air = pset.get(AIR)
    return (clear if daylight is None else float(daylight), clear if air is None else float(air))


def differs_from_measured(filling):
    """Whether what counts for the room departs from what was measured, without
    saying who caused it: an area typed by hand, or an override the model outran
    when the void moved. Preparing one writes the measured value itself, so the
    presence of an override says only that the user meant to edit it; both
    numbers come from the same stored one, so comparing them exactly needs no
    tolerance."""
    clear = clear_opening(filling)
    return any(area != clear for area in contribution(filling))


def is_measured(filling):
    """Whether the clear opening was actually measured — an absent property is
    not the same as a measured zero, and must not be counted as one."""
    return CLEAR in _pset(filling, FILLING_PSET)


def has_known_area(filling):
    """Whether both areas the room counts are known: measured, or typed by hand
    where nothing could measure them. An override is a known area — it is the
    way out of a void the geometry cannot read — but one alone leaves the other
    side unknown."""
    pset = _pset(filling, FILLING_PSET)
    if CLEAR in pset:
        return True
    return pset.get(DAYLIGHT) is not None and pset.get(AIR) is not None


def is_quantified(ifc_file):
    """Whether the calculation has ever run on this file: one room carrying a
    computed ratio says so.

    Read from the file, not from a cache any button invalidates. A room's ratio
    is written on every run whatever the geometry could measure, so — unlike a
    filling's clear opening — it cannot be taken away by every void becoming
    unreadable. The requirement beside it would not do: the user can set that
    without ever calculating.
    """
    return any(DAYLIGHT_RATIO in _pset(space, SPACE_PSET) for space in spaces.rooms(ifc_file))


PREPARED, ALREADY_SET, UNMEASURED = "prepared", "already_set", "unmeasured"


def prepare_overrides(ifc_file, filling):
    """Writes the clear opening into whichever of the two overrides is absent, so
    there is something to edit instead of a property to type from nothing.

    An override already there is never touched — preparing one must not revert a
    correction — and a filling nobody measured has nothing to copy. Says which of
    the three happened. The value comes from a pset and goes back to one, both in
    project units, so nothing is converted.
    """
    if not is_measured(filling):
        return UNMEASURED
    pset = _pset(filling, FILLING_PSET)
    properties = {
        name: ifc_file.createIfcAreaMeasure(float(pset[CLEAR])) for name in (DAYLIGHT, AIR) if pset.get(name) is None
    }
    if not properties:
        return ALREADY_SET
    ifcopenshell.api.pset.edit_pset(ifc_file, pset=_pset_entity(ifc_file, filling, FILLING_PSET), properties=properties)
    return PREPARED


Row = namedtuple(
    "Row",
    (
        "space",
        "identification",
        "name",
        "net",
        "clear",
        "daylight",
        "air",
        "unmeasured_fillings",
        "daylight_ratio",
        "air_ratio",
        "daylight_requirement",
        "air_requirement",
        "verified",
    ),
)


def requirements(space):
    """(daylight, air) the room has to reach, an eighth where the user has set
    nothing. Zero means the room is exempt."""
    pset = _pset(space, SPACE_PSET)
    daylight = pset.get(DAYLIGHT_REQUIREMENT)
    air = pset.get(AIR_REQUIREMENT)
    return (
        DEFAULT_REQUIREMENT if daylight is None else float(daylight),
        DEFAULT_REQUIREMENT if air is None else float(air),
    )


def write_requirements(ifc_file, space, daylight, air):
    """Sets what the room has to reach, and drops the stored verdict and the
    area it was taken over: both were reached against the requirement being
    replaced, and nothing recomputes them until the next run. Left there, the
    file would carry a Verificato against a requirement it plainly fails, and a
    schedule column would head a bar that is no longer in force."""
    ifcopenshell.api.pset.edit_pset(
        ifc_file,
        pset=_pset_entity(ifc_file, space, SPACE_PSET),
        properties={
            DAYLIGHT_REQUIREMENT: ifc_file.createIfcRatioMeasure(float(daylight)),
            AIR_REQUIREMENT: ifc_file.createIfcRatioMeasure(float(air)),
            MINIMUM: None,
            VERIFIED: None,
        },
        should_purge=True,
    )


def net_floor_area(ifc_file, space, geom_settings=None):
    """The room's floor in project units: the take-off already in the file where
    there is one, its footprint otherwise. Correcting the area means correcting
    the Qto — one truth in the model."""
    quantities = ifcopenshell.util.element.get_pset(space, "Qto_SpaceBaseQuantities", should_inherit=False)
    if quantities and quantities.get("NetFloorArea") is not None:
        return float(quantities["NetFloorArea"])
    try:
        shape = ifcopenshell.geom.create_shape(geom_settings or openings.settings(), space)
    except RuntimeError:
        return 0.0
    area = float(ifcopenshell.util.shape.get_footprint_area(shape.geometry))
    return area_converter(ifc_file).convert(area, "IfcAreaMeasure")


def _ratio(area, net):
    return area / net if net else 0.0


def _passes(ratio, requirement):
    return requirement <= NOT_REQUIRED or ratio >= requirement


def measure_spaces(ifc_file, spaces, geom_settings=None):
    """One Row per room, in the order given, every area in project units."""
    geom_settings = geom_settings or openings.settings()
    rows = []
    for space in spaces:
        served = boundaries.serves(space)
        clear = sum(clear_opening(filling) for filling in served)
        daylight = sum(contribution(filling)[0] for filling in served)
        air = sum(contribution(filling)[1] for filling in served)
        unmeasured_fillings = sum(not has_known_area(filling) for filling in served)
        net = net_floor_area(ifc_file, space, geom_settings)
        daylight_ratio, air_ratio = _ratio(daylight, net), _ratio(air, net)
        daylight_requirement, air_requirement = requirements(space)
        rows.append(
            Row(
                space,
                space.Name or "",
                space.LongName or "",
                net,
                clear,
                daylight,
                air,
                unmeasured_fillings,
                daylight_ratio,
                air_ratio,
                daylight_requirement,
                air_requirement,
                _passes(daylight_ratio, daylight_requirement) and _passes(air_ratio, air_requirement),
            )
        )
    return rows


def _minimum(row):
    """The area the room has to reach, so a schedule reads it as a column
    instead of recomputing it from the ratio.

    Only where one area answers for the room: where illuminazione and aerazione
    ask for different fractions there are two, and one property cannot hold
    them — half the truth in a column read as the whole is worse than an empty
    cell. A room the rule asks nothing of owes no area at all."""
    daylight, air = row.daylight_requirement, row.air_requirement
    if daylight != air or daylight <= NOT_REQUIRED:
        return None
    return row.net * daylight


def write_space_results(ifc_file, row):
    """The computed half of the room's pset. The two requirements are seeded on
    the way, so a room the user never touched still says what it was checked
    against. A filling whose area nobody knows withholds the verdict rather than
    let the room claim one it has not earned."""
    minimum = _minimum(row)
    properties = {
        DAYLIGHT: ifc_file.createIfcAreaMeasure(row.daylight),
        AIR: ifc_file.createIfcAreaMeasure(row.air),
        DAYLIGHT_RATIO: ifc_file.createIfcRatioMeasure(row.daylight_ratio),
        AIR_RATIO: ifc_file.createIfcRatioMeasure(row.air_ratio),
        MINIMUM: None if minimum is None else ifc_file.createIfcAreaMeasure(minimum),
        VERIFIED: None if row.unmeasured_fillings > 0 else ifc_file.createIfcBoolean(bool(row.verified)),
    }
    existing = _pset(row.space, SPACE_PSET)
    if DAYLIGHT_REQUIREMENT not in existing:
        properties[DAYLIGHT_REQUIREMENT] = ifc_file.createIfcRatioMeasure(row.daylight_requirement)
    if AIR_REQUIREMENT not in existing:
        properties[AIR_REQUIREMENT] = ifc_file.createIfcRatioMeasure(row.air_requirement)
    ifcopenshell.api.pset.edit_pset(
        ifc_file, pset=_pset_entity(ifc_file, row.space, SPACE_PSET), properties=properties, should_purge=True
    )


def headers(ifc_file):
    """The table's first row, the areas carrying the project's unit."""
    unit = ifcopenshell.util.unit.get_project_unit(ifc_file, "AREAUNIT")
    symbol = ifcopenshell.util.unit.get_unit_symbol(unit) if unit else ""
    if symbol and symbol[-1] == "2":
        symbol = symbol[:-1] + "²"

    def area(label):
        return f"{label} ({symbol})" if symbol else label

    return [
        "Identificativo",
        "Nome",
        area("Superficie netta"),
        area("Requisito illuminazione / aerazione"),
        area("Superficie illuminante"),
        area("Superficie aerante"),
        "Verificato",
    ]


NO_STOREY = "(nessun piano)"

# unmeasurable is the fillings bound to a room that this run did not measure —
# including one that fills no void at all, which never reaches the proposals; a
# Row's unmeasured_fillings are the ones it serves whose two counted areas are
# not both known, by measurement or by override. Two populations, and a room can
# lose its verdict to the second without the first holding anything.
Summary = namedtuple("Summary", ("rows", "boundaries_written", "orphans", "unmeasurable", "disagreeing"))


def quantify(ifc_file, geom_settings=None):
    """One pass over the file: clear openings measured, missing boundaries
    written, results stored on the rooms.

    Nothing already in the file is overwritten — not a boundary, not an
    override, not a requirement — so running it twice leaves the second run with
    nothing to do. A filling this run did not measure carries no measurement
    afterwards — whether the void could not be read or the filling fills none at
    all, which drops it out of the proposals entirely. A stale area would count,
    print and pass, and a filling nothing reported would withhold its room's
    verdict with nowhere for the user to look. One rule answers both.
    """
    geom_settings = geom_settings or openings.settings()
    proposals = openings.proposals(ifc_file, geom_settings)
    written = boundaries.write_missing(ifc_file, proposals)
    measured = {proposal.filling for proposal in proposals if proposal.area is not None}
    for proposal in proposals:
        if proposal.area is not None:
            write_clear_opening(ifc_file, proposal.filling, proposal.area)
    unmeasurable = []
    for filling in openings.fillings(ifc_file):
        if filling in measured:
            continue
        remove_clear_opening(ifc_file, filling)
        # Reported only where it can withhold a room's verdict: a filling
        # bounding no room is an orphan, which is its own bucket.
        if any(spaces.is_room(space) for space in boundaries.rooms_of(filling)):
            unmeasurable.append(filling)
    rows = measure_spaces(ifc_file, spaces.rooms(ifc_file), geom_settings)
    for row in rows:
        write_space_results(ifc_file, row)
    return Summary(
        rows,
        written,
        [proposal.filling for proposal in proposals if not proposal.rooms],
        unmeasurable,
        [proposal.filling for proposal in boundaries.disagreeing(proposals)],
    )


def _storey(ifc_file, space):
    """The storey containing a room: normally reached through Decomposes, or —
    for a schema-invalid file that relates the space by containment instead,
    WR31 forbids it but an imported file may still do it — by walking
    IfcRelContainedInSpatialStructure directly.

    get_container also walks Decomposes, via get_parent, but only succeeds
    through ContainedInStructure, an inverse no spatial element carries, so it
    climbs past the storey and returns None; get_parent's ifc_class filter
    stops as soon as an ancestor of that class turns up.
    """
    storey = ifcopenshell.util.element.get_parent(space, ifc_class="IfcBuildingStorey")
    if storey is not None:
        return storey
    for rel in ifc_file.by_type("IfcRelContainedInSpatialStructure"):
        if space in rel.RelatedElements and rel.RelatingStructure.is_a("IfcBuildingStorey"):
            return rel.RelatingStructure
    return None


def _elevation(storey):
    value = getattr(storey, "Elevation", None)
    return (value is None, value)


def sections(ifc_file, rows):
    """The rows grouped by the storey that contains each room, in building
    order by elevation — storeys with none last — and the rooms outside one,
    last of all."""
    grouped = {}
    for row in rows:
        grouped.setdefault(_storey(ifc_file, row.space), []).append(row)
    labelled = [
        (storey.Name or storey.LongName or "", grouped[storey])
        for storey in sorted((key for key in grouped if key), key=_elevation)
    ]
    if None in grouped:
        labelled.append((NO_STOREY, grouped[None]))
    return labelled
