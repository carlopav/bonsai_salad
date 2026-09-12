"""
opening_dimensions — pure IFC core of sync_opening_dimensions (no bpy, no
bonsai).

Reads the size a door/window type was parametrised with and writes it onto its
occurrences' OverallWidth/OverallHeight. Bonsai sets those two attributes when
the occurrence is created and when the type is edited through its own editor,
but nothing re-derives them afterwards, so a type reshaped by other means
leaves its occurrences carrying the old numbers.

The source is the type's BBIM_Door/BBIM_Window pset, a single 'Data' property
holding Bonsai's parametric JSON in project units. Corrections are given in
metres (a Blender distance); dimensions are written in project units.
"""

import json

import ifcopenshell.util.element

TOLERANCE = 1e-6  # project units

# The pset Bonsai's parametric door/window editor writes on the type.
PSET_NAMES = {"IfcDoor": "BBIM_Door", "IfcWindow": "BBIM_Window"}


def is_opening(element):
    """True for the classes carrying OverallWidth/OverallHeight that Bonsai
    parametrises: everything else has no such pair to sync."""
    return element is not None and element.is_a() in PSET_NAMES


def type_data(element):
    """The parametric JSON of element's type, or None when the occurrence has
    no type, the type carries no BBIM pset, or its Data is not readable."""
    if not is_opening(element):
        return None
    element_type = ifcopenshell.util.element.get_type(element)
    if element_type is None:
        return None
    pset = ifcopenshell.util.element.get_psets(element_type).get(PSET_NAMES[element.is_a()])
    if not pset or not isinstance(pset.get("Data"), str):
        return None
    try:
        data = json.loads(pset["Data"])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def type_dimensions(element):
    """(width, height) in project units as the type was parametrised, or None
    when the type holds no usable pair."""
    data = type_data(element)
    if data is None:
        return None
    values = (data.get("overall_width"), data.get("overall_height"))
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in values):
        return None
    return (float(values[0]), float(values[1]))


def sync_dimensions(element, delta_width=0.0, delta_height=0.0, unit_scale=1.0):
    """Writes the type's dimensions, plus the corrections, onto element's
    OverallWidth/OverallHeight. The corrections are signed and in metres: a
    door's net passage is its type's size less the lining it loses on each
    side. Geometry is deliberately left untouched — the point of a correction
    is to record an opening that differs from the modelled one.

    Returns "synced", "unchanged" when the attributes already say so,
    "no_type_data" when the type has nothing to sync from, and "invalid" when
    a correction leaves nothing of the opening."""
    dimensions = type_dimensions(element)
    if dimensions is None:
        return "no_type_data"
    width, height = (value + delta / unit_scale for value, delta in zip(dimensions, (delta_width, delta_height)))
    if width <= 0.0 or height <= 0.0:
        return "invalid"
    current = (element.OverallWidth, element.OverallHeight)
    if all(value is not None and abs(value - target) <= TOLERANCE for value, target in zip(current, (width, height))):
        return "unchanged"
    element.OverallWidth = width
    element.OverallHeight = height
    return "synced"
