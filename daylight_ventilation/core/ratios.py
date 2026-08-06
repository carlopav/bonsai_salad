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

from . import boundaries, openings

FILLING_PSET = "Superfici aeroilluminanti"
CLEAR = "Luce architettonica"
DAYLIGHT = "Superficie illuminante"
AIR = "Superficie aerante"

SPACE_PSET = "Requisiti aeroilluminanti"
DAYLIGHT_REQUIREMENT = "Requisito illuminazione"
AIR_REQUIREMENT = "Requisito aerazione"
DAYLIGHT_RATIO = "Rapporto illuminazione"
AIR_RATIO = "Rapporto aerazione"
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


def write_clear_opening(ifc_file, filling, area):
    """Stores the measured clear opening, leaving both overrides untouched.

    The measure is explicit: a pset of our own has no buildingSMART template,
    and without one an Italian name would be read as a length.
    """
    ifcopenshell.api.pset.edit_pset(
        ifc_file,
        pset=_pset_entity(ifc_file, filling, FILLING_PSET),
        properties={CLEAR: ifc_file.createIfcAreaMeasure(float(area))},
    )


def contribution(filling):
    """(daylight, air) as the filling enters the count: the override where the
    user set one, the clear opening everywhere else."""
    pset = _pset(filling, FILLING_PSET)
    clear = float(pset.get(CLEAR) or 0.0)
    daylight = pset.get(DAYLIGHT)
    air = pset.get(AIR)
    return (clear if daylight is None else float(daylight), clear if air is None else float(air))


def is_overridden(filling):
    pset = _pset(filling, FILLING_PSET)
    return pset.get(DAYLIGHT) is not None or pset.get(AIR) is not None


def is_measured(filling):
    """Whether the clear opening was actually measured — an absent property is
    not the same as a measured zero, and must not be counted as one."""
    return CLEAR in _pset(filling, FILLING_PSET)


Row = namedtuple(
    "Row",
    (
        "space",
        "identification",
        "name",
        "net",
        "daylight",
        "air",
        "unmeasured",
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
    ifcopenshell.api.pset.edit_pset(
        ifc_file,
        pset=_pset_entity(ifc_file, space, SPACE_PSET),
        properties={
            DAYLIGHT_REQUIREMENT: ifc_file.createIfcRatioMeasure(float(daylight)),
            AIR_REQUIREMENT: ifc_file.createIfcRatioMeasure(float(air)),
        },
    )


def net_floor_area(space, geom_settings=None):
    """The room's floor: the take-off already in the file where there is one, its
    footprint otherwise. Correcting the area means correcting the Qto — one
    truth in the model."""
    quantities = ifcopenshell.util.element.get_pset(space, "Qto_SpaceBaseQuantities", should_inherit=False)
    if quantities and quantities.get("NetFloorArea") is not None:
        return float(quantities["NetFloorArea"])
    try:
        shape = ifcopenshell.geom.create_shape(geom_settings or openings.settings(), space)
    except RuntimeError:
        return 0.0
    return float(ifcopenshell.util.shape.get_footprint_area(shape.geometry))


def _ratio(area, net):
    return area / net if net else 0.0


def _passes(ratio, requirement):
    return requirement <= NOT_REQUIRED or ratio >= requirement


def measure_spaces(ifc_file, spaces, geom_settings=None):
    """One Row per room, in the order given."""
    geom_settings = geom_settings or openings.settings()
    rows = []
    for space in spaces:
        served = boundaries.serves(space)
        daylight = sum(contribution(filling)[0] for filling in served)
        air = sum(contribution(filling)[1] for filling in served)
        unmeasured = sum(not is_measured(filling) for filling in served)
        net = net_floor_area(space, geom_settings)
        daylight_ratio, air_ratio = _ratio(daylight, net), _ratio(air, net)
        daylight_requirement, air_requirement = requirements(space)
        rows.append(
            Row(
                space,
                space.Name or "",
                space.LongName or "",
                net,
                daylight,
                air,
                unmeasured,
                daylight_ratio,
                air_ratio,
                daylight_requirement,
                air_requirement,
                _passes(daylight_ratio, daylight_requirement) and _passes(air_ratio, air_requirement),
            )
        )
    return rows


def write_space_results(ifc_file, row):
    """The computed half of the room's pset. The two requirements are seeded on
    the way, so a room the user never touched still says what it was checked
    against. An unmeasured filling withholds the verdict rather than let the
    room claim one it has not earned."""
    properties = {
        DAYLIGHT: ifc_file.createIfcAreaMeasure(row.daylight),
        AIR: ifc_file.createIfcAreaMeasure(row.air),
        DAYLIGHT_RATIO: ifc_file.createIfcRatioMeasure(row.daylight_ratio),
        AIR_RATIO: ifc_file.createIfcRatioMeasure(row.air_ratio),
        VERIFIED: None if row.unmeasured > 0 else ifc_file.createIfcBoolean(bool(row.verified)),
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
        area("Superficie aerante"),
        area("Superficie illuminante"),
        "Rapporto aerazione",
        "Requisito aerazione",
        "Rapporto illuminazione",
        "Requisito illuminazione",
        "Verificato",
    ]
