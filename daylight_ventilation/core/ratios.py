# Bonsai Salad — daylight_ventilation tool

"""The rule half of the check: what each filling contributes, what each room
requires, and whether it passes.

Every quantity exists twice, as a computed property the calculation always
rewrites and an override property only the user writes. An absent override means
the computed value applies — so a recalculation cannot destroy a correction, and
no tolerance comparison is needed to guess which is which.
"""

import ifcopenshell.api.pset
import ifcopenshell.util.element

FILLING_PSET = "Superfici aeroilluminanti"
CLEAR = "Luce architettonica"
DAYLIGHT = "Superficie illuminante"
AIR = "Superficie aerante"


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
