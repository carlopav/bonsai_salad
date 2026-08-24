# Bonsai Salad — ifc_spaces tool

"""What the room schedule asks of the model, and which rooms each table covers.

Nothing is measured or computed here. Every column is a selector query IfcCsv
resolves against the room, so the table is only ever a reading of the IFC: the
areas and the volume come from Qto_SpaceBaseQuantities, the daylight and
ventilation columns from the "Requisiti aeroilluminanti" set the
daylight_ventilation tool writes — the minimum area included, which that tool
stores precisely so a schedule does not have to work it out from the ratio.

A property the model does not carry answers None, which the export prints as its
null marker: the room keeps its line, and the cell says nobody measured it
rather than a zero nobody meant.
"""

from collections import namedtuple

import ifcopenshell.util.element
import ifcopenshell.util.unit

QTO = "Qto_SpaceBaseQuantities"
DAYLIGHT_PSET = "Requisiti aeroilluminanti"

# A key holding spaces has to be quoted: the selector grammar reads an
# unquoted one up to the first space and refuses the rest.
def _property(pset, name):
    return f'"{pset}"."{name}"'


AREA, VOLUME, TEXT = "AREAUNIT", "VOLUMEUNIT", None

# Where a cell's text sits across its column. Left is the absence of a rule
# rather than a rule of its own, which is how a spreadsheet stores it too.
LEFT, CENTER, RIGHT = None, "center", "end"

# (query, heading, the unit the heading carries, how wide the column is drawn in
# millimetres, where its text sits). The order is the table's, and the widths add
# up to the 190 mm the table is given on a sheet: the long name takes what the
# measured columns do not need, and the aeroilluminante minimum is wider than its
# neighbours because its heading is twice as long and has to wrap in the same two
# lines.
#
# A measure is read against the ones above it, so it is aligned right. A code and
# a verdict are neither read down a column nor sentences, so they sit in the
# middle; only the long name starts where its column starts.
COLUMNS_WITH_UNITS = (
    ("Name", "Codice", TEXT, 15.0, CENTER),
    ("LongName", "Nome esteso", TEXT, 52.0, LEFT),
    (_property(QTO, "NetFloorArea"), "Superficie utile", AREA, 19.0, RIGHT),
    (_property(QTO, "NetVolume"), "Volume", VOLUME, 19.0, RIGHT),
    (
        _property(DAYLIGHT_PSET, "Superficie minima aeroilluminante"),
        "Superficie minima ill.ne / aerazione",
        AREA,
        27.0,
        RIGHT,
    ),
    (_property(DAYLIGHT_PSET, "Superficie illuminante"), "Superficie illuminante", AREA, 21.0, RIGHT),
    (_property(DAYLIGHT_PSET, "Superficie aerante"), "Superficie aerante", AREA, 21.0, RIGHT),
    (_property(DAYLIGHT_PSET, "Verificato"), "Verificato", TEXT, 16.0, CENTER),
)

COLUMNS = tuple((query, heading) for query, heading, _, _, _ in COLUMNS_WITH_UNITS)

# (width, alignment) per column, for whoever styles the sheet.
LAYOUT = tuple((width, alignment) for _, _, _, width, alignment in COLUMNS_WITH_UNITS)

# Two decimals and the Italian comma, in the only shape the format grammar
# reaches it: round() pads to two decimals but keeps the point, and number()
# would take the padding off again through a float, printing a whole metre as
# 20,0 beside a 48,62. So the padded string is cut in two and rejoined on a
# comma.
_ROUNDED = "round({{value}}, 0.01)"
NUMBER_FORMAT = f'concat(substr({_ROUNDED}, 0, -3), ",", substr({_ROUNDED}, -2))'
FORMATTING = [{"name": query, "format": NUMBER_FORMAT} for query, _, unit, _, _ in COLUMNS_WITH_UNITS if unit]

# Natural order on the code, so A2 comes before A10 rather than after it.
SORT = [{"name": COLUMNS[0][0], "order": "ASC"}]

# What a cell says when the model has no answer for it: a room nobody took off,
# or one the aeroilluminante check never reached.
NULL = "—"
YES, NO = "sì", "no"

# A balcony, a loggia or a terrace is outdoor space that happens to be modelled
# as a room; a parking bay is the same kind of thing, and GFA is a floor-area
# overlay rather than a room at all. The rule names what is not a room rather
# than admitting only INTERNAL: a file that classifies in USERDEFINED, or an
# exporter that leaves the type unset, still has rooms, and dropping them from
# the schedule without a word is the one failure worth avoiding.
NOT_ROOMS = ("EXTERNAL", "PARKING", "GFA")

Grouping = namedtuple("Grouping", ("storeys", "orphans"))


def is_room(space):
    """Whether the space is a room rather than outdoor space, a parking bay or a
    floor-area overlay. IFC2X3 says INTERNAL or EXTERNAL in
    InteriorOrExteriorSpace, which IFC4 replaced with PredefinedType."""
    kind = getattr(space, "PredefinedType", None) or getattr(space, "InteriorOrExteriorSpace", None)
    return kind not in NOT_ROOMS


def rooms(ifc_file):
    """Every room in the file, in file order."""
    return [space for space in ifc_file.by_type("IfcSpace") if is_room(space)]


def _symbol(ifc_file, kind, exponent):
    """The project's unit, its trailing exponent raised: get_unit_symbol writes
    a square metre m2, which reads as a unit of its own in a header."""
    unit = ifcopenshell.util.unit.get_project_unit(ifc_file, kind)
    symbol = ifcopenshell.util.unit.get_unit_symbol(unit) if unit else ""
    if symbol and symbol[-1] == exponent:
        return symbol[:-1] + {"2": "²", "3": "³"}[exponent]
    return symbol


def columns(ifc_file):
    """(query, heading) for each column, the headings of the measured ones
    carrying the project's unit."""
    symbols = {AREA: _symbol(ifc_file, AREA, "2"), VOLUME: _symbol(ifc_file, VOLUME, "3")}
    return [
        (query, f"{heading} ({symbols[unit]})" if unit and symbols[unit] else heading)
        for query, heading, unit, _, _ in COLUMNS_WITH_UNITS
    ]


def storey_of(ifc_file, space):
    """The storey containing a room: normally reached through Decomposes, or —
    for a schema-invalid file that relates the space by containment instead,
    WR31 forbids it but an imported file may still do it — by walking
    IfcRelContainedInSpatialStructure directly.

    get_container also walks Decomposes, via get_parent, but only succeeds
    through ContainedInStructure, an inverse no spatial element carries, so it
    climbs past the storey and returns None.
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


def by_storey(ifc_file):
    """The rooms of the file grouped by the storey that contains them, in
    building order by elevation — storeys without one last — and the rooms
    outside a storey apart: one file per storey has nowhere to write them.

    The rooms of a storey stay in file order: the table is sorted on its own
    first column when it is written."""
    grouped = {}
    for space in rooms(ifc_file):
        grouped.setdefault(storey_of(ifc_file, space), []).append(space)
    storeys = [(storey, grouped[storey]) for storey in sorted((key for key in grouped if key), key=_elevation)]
    return Grouping(storeys, grouped.get(None, []))


UNNAMED = "Piano"


def _name(element, fallback=""):
    return (getattr(element, "Name", None) or getattr(element, "LongName", None) or fallback).strip() or fallback


def labels(storeys):
    """What to call each storey, one label per storey and never two the same:
    the schedule is one file per storey, and a project with a Piano terra in
    every building would otherwise write them all over each other.

    A storey keeps its own name where that already tells it apart; the ones
    that clash take their building's name, and any still clashing are
    numbered."""
    names = [_name(storey, UNNAMED) for storey in storeys]
    qualified = [
        f"{_name(ifcopenshell.util.element.get_parent(storey, ifc_class='IfcBuilding'))} - {name}".lstrip(" -")
        if names.count(name) > 1
        else name
        for storey, name in zip(storeys, names)
    ]
    counters = {}
    resolved = []
    for name in qualified:
        if qualified.count(name) > 1:
            counters[name] = counters.get(name, 0) + 1
            name = f"{name} ({counters[name]})"
        resolved.append(name)
    return resolved
