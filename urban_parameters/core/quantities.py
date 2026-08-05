# Bonsai Salad — urban_parameters tool

"""Urban planning parameters of the IfcSpatialZones standing for a buildable
volume: gross area, mean height, and the volume the two multiply to.

Everything is read from the zone's body in world coordinates and handed back
in project units, so the table and the quantity sets say the same numbers.
"""

from collections import namedtuple

import numpy as np

import ifcopenshell.api.pset
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.shape
import ifcopenshell.util.unit

ZONE_CLASS = "IfcSpatialZone"

# A table totals zones of one ObjectType at a time. The zones carrying none are
# a group of their own, under a heading no ObjectType would be spelled with.
NO_TYPE = "(senza tipo)"
ANY_TYPE = ""

QTO_NAME = "Parametri urbanistici"
AREA = "Superficie lorda"
HEIGHT = "Altezza media"
VOLUME = "Volume urbanistico"

COEFFICIENT = "Coefficiente"
DEFAULT_COEFFICIENT = 1.0

TOTAL = "Totale"
ADDITIONS = "Totale lordo"
DETRACTIONS = "Totale detrazioni"
NET = "Totale netto"

Measured = namedtuple("Measured", ("zone", "name", "area", "height", "volume", "coefficient"))

MEASURES = {AREA: "IfcAreaMeasure", HEIGHT: "IfcLengthMeasure", VOLUME: "IfcVolumeMeasure"}
UNIT_TYPES = {"IfcAreaMeasure": "AREAUNIT", "IfcLengthMeasure": "LENGTHUNIT", "IfcVolumeMeasure": "VOLUMEUNIT"}
SUPERSCRIPTS = {"2": "²", "3": "³"}


class UnmeasurableGeometry(ValueError):
    """Raised for a body that is not a closed, consistently oriented solid.

    Measuring one is worse than refusing to. `get_volume` sums the signed
    tetrahedra between each triangle and the origin, so a broken surface still
    yields a number: it is just off by the contribution of whatever is missing,
    which is a third of that area times its distance from the project origin.
    Near the origin the result stays plausible enough to reach a table
    unnoticed, and it moves if the model is ever relocated.
    """


def object_type(zone):
    """The zone's ObjectType, the untyped ones under a heading of their own."""
    return zone.ObjectType or NO_TYPE


def spatial_zones(ifc_file, of_type=ANY_TYPE):
    """The spatial zones of the file, by name, of one ObjectType. A table is
    only worth totalling over zones of the same kind, so the type is the
    filter everything else is built on. Empty on IFC2X3, which has no such
    class."""
    try:
        zones = ifc_file.by_type(ZONE_CLASS)
    except RuntimeError:
        return []
    if of_type != ANY_TYPE:
        zones = [zone for zone in zones if object_type(zone) == of_type]
    return sorted(zones, key=lambda zone: (zone.Name or "").lower())


def object_types(ifc_file):
    """Every ObjectType the file's zones carry, once each. What the panel
    offers to choose from."""
    return sorted({object_type(zone) for zone in spatial_zones(ifc_file)}, key=str.lower)


def settings():
    geom_settings = ifcopenshell.geom.settings()
    # The gross area is a projection along world Z: a zone whose placement is
    # tilted has to be measured after it, not in its own frame.
    geom_settings.set("use-world-coords", True)
    return geom_settings


def mesh_defect(geometry):
    """What disqualifies a triangulated body as a solid, worded for the user,
    or None when it is sound. Every edge of a closed and consistently oriented
    surface is shared by exactly two triangles, which walk it once in each
    direction — so counting edges is enough to tell."""
    faces = ifcopenshell.util.shape.get_faces(geometry)
    directed = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    _, shared = np.unique(np.sort(directed, axis=1), axis=0, return_counts=True)
    _, walks = np.unique(directed, axis=0, return_counts=True)
    if open_edges := int((shared == 1).sum()):
        return f"its surface is not closed ({open_edges} open edges)"
    if branching := int((shared > 2).sum()):
        return f"its surface is not manifold ({branching} edges shared by more than two faces)"
    if reversed_edges := int((walks > 1).sum()):
        return f"its faces are not consistently oriented ({reversed_edges} reversed edges)"
    return None


def measure(element, geom_settings=None):
    """(area, mean height, volume) of an element's body in SI units, or None
    when it carries no usable body. The mean height is the volume over the
    gross area, so multiplying the two back gives the real volume however
    sloped or stepped the top is.

    Raises UnmeasurableGeometry when the body is not a closed solid, rather
    than returning the plausible wrong number that would follow."""
    try:
        # The shape has to outlive its geometry: read off a temporary, the
        # vertex buffers come back empty.
        shape = ifcopenshell.geom.create_shape(geom_settings or settings(), element)
    except RuntimeError:
        return None
    geometry = shape.geometry
    if defect := mesh_defect(geometry):
        raise UnmeasurableGeometry(f"{element.Name or 'Unnamed zone'} cannot be measured: {defect}.")
    area = ifcopenshell.util.shape.get_footprint_area(geometry)
    if not area:
        return None
    volume = ifcopenshell.util.shape.get_volume(geometry)
    return area, volume / area, volume


def measure_zones(ifc_file, zones):
    """[Measured] in project units, leaving out the zones with no body. Areas
    and volumes are the measured magnitudes, always positive: the coefficient
    is carried alongside them, never folded in. Lets UnmeasurableGeometry
    through: one unsound zone spoils the totals, so the whole run is worth
    stopping."""
    from ifc5d.qto import SI2ProjectUnitConverter

    converter = SI2ProjectUnitConverter(ifc_file)
    geom_settings = settings()
    rows = []
    for zone in zones:
        measured = measure(zone, geom_settings)
        if measured is None:
            continue
        rows.append(
            Measured(
                zone,
                zone.Name or "",
                converter.convert(measured[0], "IfcAreaMeasure"),
                converter.convert(measured[1], "IfcLengthMeasure"),
                converter.convert(measured[2], "IfcVolumeMeasure"),
                coefficient(zone),
            )
        )
    return rows


def coefficient(zone):
    """How the zone enters the count: 1 by default, -1 for a volume that is
    detracted from the others, any fraction for one counted in part."""
    value = quantity_set(zone).get(COEFFICIENT)
    return DEFAULT_COEFFICIENT if value is None else float(value)


def write_coefficient(ifc_file, zone, value):
    """Sets the coefficient, quantity set created on the way. Only ever called
    for a deliberate act of the user: a take-off leaves an edited one alone."""
    ifcopenshell.api.pset.edit_qto(
        ifc_file,
        qto=_quantity_set_entity(ifc_file, zone),
        properties={COEFFICIENT: _coefficient_measure(ifc_file, value)},
    )


def counted(row):
    """(name, coefficient, area, height, volume) as the row enters the count:
    the coefficient scales area and volume, the mean height is geometry and
    does not move. The columns hold what is totalled and the coefficient says
    why — a halved zone shows 0.5 next to the half it contributes.

    Detractions come out positive: they are subtracted once, by their own
    subtotal, not cell by cell."""
    weight = abs(row.coefficient)
    return row.name, row.coefficient, row.area * weight, row.height, row.volume * weight


def sections(rows):
    """[(label, [(name, area, height, volume)])]: the zones that add up, then
    the ones that are detracted. With no detractions there is a single block,
    closed by the plain total."""
    additions = [counted(row) for row in rows if row.coefficient >= 0]
    detractions = [counted(row) for row in rows if row.coefficient < 0]
    if not detractions:
        return [(TOTAL, additions)]
    return [(ADDITIONS, additions), (DETRACTIONS, detractions)]


def totals(rows):
    """(area, mean height, volume) of a whole set of rows, net of the
    detractions. The height is the volume weighted mean: the only one that
    multiplies the total area back to the total volume."""
    area = sum(row.area * row.coefficient for row in rows)
    volume = sum(row.volume * row.coefficient for row in rows)
    return area, (volume / area if area else 0.0), volume


def unit_symbol(ifc_file, measure_type):
    unit = ifcopenshell.util.unit.get_project_unit(ifc_file, UNIT_TYPES[measure_type])
    symbol = ifcopenshell.util.unit.get_unit_symbol(unit) if unit else ""
    if symbol and symbol[-1] in SUPERSCRIPTS:
        symbol = symbol[:-1] + SUPERSCRIPTS[symbol[-1]]
    return symbol


def headers(ifc_file):
    """The table's first row: the coefficient, then the quantities with their
    project unit. The coefficient is a pure number and carries none."""
    row = ["Name", COEFFICIENT]
    for name, measure_type in MEASURES.items():
        symbol = unit_symbol(ifc_file, measure_type)
        row.append(f"{name} ({symbol})" if symbol else name)
    return row


def quantity_set(zone):
    """The zone's quantity set as a dict, empty when it has none yet."""
    return ifcopenshell.util.element.get_pset(zone, QTO_NAME, should_inherit=False) or {}


def _quantity_set_entity(ifc_file, zone):
    qto = quantity_set(zone)
    if qto:
        return ifc_file.by_id(qto["id"])
    return ifcopenshell.api.pset.add_qto(ifc_file, zone, QTO_NAME)


def _coefficient_measure(ifc_file, value):
    """The dimensionless quantity the schema offers. IFC4X3 has a real valued
    IfcQuantityNumber; IFC4 only has IfcQuantityCount, whose CountValue is
    still a number there, so a fraction survives it.

    Both assert value >= 0 in their where rules (IfcQuantityCount_WR21), so a
    detraction stored here is a quantity set that will not pass a strict
    validator. It is stored here anyway, by decision: the coefficient belongs
    next to the measures it weighs.
    """
    if ifc_file.schema == "IFC4X3":
        return ifc_file.createIfcNumericMeasure(float(value))
    return ifc_file.createIfcCountMeasure(float(value))


def write_qto(ifc_file, zone, area, height, volume):
    """Stores the measures on the zone as its "Parametri urbanistici" quantity
    set, seeding the coefficient at 1 the first time. A coefficient already
    there is the user's, and a take-off never writes over it.

    The measures are explicit: a quantity set of our own has no buildingSMART
    template, and without them every Italian name would be read as a length."""
    properties = {
        AREA: ifc_file.createIfcAreaMeasure(area),
        HEIGHT: ifc_file.createIfcLengthMeasure(height),
        VOLUME: ifc_file.createIfcVolumeMeasure(volume),
    }
    if COEFFICIENT not in quantity_set(zone):
        properties[COEFFICIENT] = _coefficient_measure(ifc_file, DEFAULT_COEFFICIENT)
    ifcopenshell.api.pset.edit_qto(ifc_file, qto=_quantity_set_entity(ifc_file, zone), properties=properties)
