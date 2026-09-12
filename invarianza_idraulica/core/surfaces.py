# Bonsai Salad — invarianza_idraulica tool

"""Le superfici scolanti di un ambito di intervento, ciascuna con il proprio
coefficiente di deflusso.

Le superfici di un progetto sono impilate — il tetto sopra i muri, la
pavimentazione sopra il terreno — e sommarne le impronte conterebbe più volte
lo stesso punto di lotto. Qui ogni punto conta una volta sola, per la superficie
più alta che lo copre, e il tutto è ritagliato sull'ambito: la somma delle righe
è l'area dell'ambito, per costruzione.
"""

from collections import namedtuple

import shapely

from . import coefficients, footprints

ZONE_CLASS = "IfcSpatialZone"

# Volumi d'aria e oggetti mobili non sono superfici scolanti. Il corpo di uno
# spazio va dal pavimento al soffitto: in pianta vincerebbe sulla pavimentazione
# che lo riveste, azzerandola.
EXCLUDED = (
    "IfcFurnishingElement",
    "IfcFurniture",
    "IfcSystemFurnitureElement",
    "IfcAnnotation",
    "IfcSpace",
    "IfcExternalSpatialElement",
    "IfcOpeningElement",
    "IfcVirtualElement",
    "IfcGrid",
)

RESIDUAL = "(superficie non attribuita)"

# Le classi con una posizione certa nella pila la ricevono; la quota decide solo
# fra le restanti. La quota massima è un criterio debole — decide per il punto
# più alto di una mesh, non punto per punto — e da sola metterebbe il terreno
# sopra l'edificio ogni volta che il lotto è in pendenza.
SOLAR, ROOF, ORDINARY, TERRAIN = 0, 1, 2, 3

Row = namedtuple("Row", ("label", "coefficient", "source", "area", "geometry"))
Measurement = namedtuple(
    "Measurement",
    ("boundary", "total", "rows", "impermeable", "mean_coefficient", "ambiguous", "unmeasurable"),
)


class NoBoundary(ValueError):
    """L'ambito non ha un'impronta: senza perimetro non c'è nulla da ripartire."""


def is_terrain(element):
    return element.is_a("IfcSite") or (
        element.is_a("IfcGeographicElement") and getattr(element, "PredefinedType", None) == "TERRAIN"
    )


def priority(element):
    if element.is_a("IfcSolarDevice"):
        return SOLAR
    if element.is_a("IfcRoof"):
        return ROOF
    if is_terrain(element):
        return TERRAIN
    return ORDINARY


def candidates(ifc_file, zone):
    """Gli elementi che possono coprire una porzione di lotto. IfcSite non è un
    IfcElement e va chiesto a parte, ma è quasi sempre il terreno."""
    elements = list(ifc_file.by_type("IfcElement")) + list(ifc_file.by_type("IfcSite"))
    return [
        element
        for element in elements
        if element != zone and not any(element.is_a(excluded) for excluded in EXCLUDED)
    ]


def measure(ifc_file, zone):
    """Le superfici dell'ambito, raggruppate per etichetta e coefficiente.

    Le righe portano con sé la propria geometria risolta: la planimetria
    disegna gli stessi poligoni che hanno prodotto i numeri, e non c'è modo che
    disegno e tabella divergano."""
    geom_settings = footprints.settings()
    outline = footprints.footprint(zone, geom_settings)
    if outline is None:
        raise NoBoundary(f"{zone.Name or 'Ambito senza nome'} non ha un corpo da cui ricavare il perimetro.")
    boundary = outline.polygon

    measured, unmeasurable = [], []
    for element in candidates(ifc_file, zone):
        found = footprints.footprint(element, geom_settings)
        if found is None:
            if footprints.has_body(element):
                unmeasurable.append(element)
            continue
        measured.append((element, found))

    measured.sort(key=lambda pair: (priority(pair[0]), -pair[1].top))

    covered = shapely.Polygon()
    groups, ambiguous = {}, []
    for element, found in measured:
        visible = found.polygon.intersection(boundary).difference(covered)
        if visible.is_empty:
            continue
        covered = shapely.union_all([covered, visible])
        found_coefficient = coefficients.coefficient(element)
        if found_coefficient.source == coefficients.AMBIGUOUS:
            ambiguous.append(element)
        key = (found_coefficient.label, found_coefficient.value, found_coefficient.source)
        groups.setdefault(key, []).append(visible)

    residual = boundary.difference(covered)
    if not residual.is_empty:
        groups[(RESIDUAL, coefficients.DEFAULT, coefficients.PREDEFINED)] = [residual]

    rows = [
        Row(label, value, source, geometry.area, geometry)
        for (label, value, source), parts in groups.items()
        for geometry in [shapely.union_all(parts)]
    ]
    rows.sort(key=lambda row: row.area, reverse=True)

    total = boundary.area
    impermeable = sum(row.area * row.coefficient for row in rows)
    return Measurement(
        boundary,
        total,
        rows,
        impermeable,
        impermeable / total if total else 0.0,
        ambiguous,
        unmeasurable,
    )
