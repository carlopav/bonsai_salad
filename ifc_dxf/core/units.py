"""The IFC project's length unit, and the DXF drawing unit it maps to.

The DXF is written in the project's own length unit. Every coordinate read
from IFC data (placements, curves, profiles, literals) is already in it; the
geometry engine, which works in metres, is converted to it where its output
meets those coordinates. Sizes the code states in metres -- paper heights,
snap tolerances -- are divided by the unit scale wherever they meet model
coordinates.

Leaf module: no dependencies on other core modules.
"""

import ifcopenshell.util.unit

# (metres per unit, DXF $INSUNITS code) for the length units an IFC project
# can declare.
_INSUNITS = (
    (1.0, 6),        # metre
    (0.001, 4),      # millimetre
    (0.01, 5),       # centimetre
    (0.1, 14),       # decimetre
    (1000.0, 7),     # kilometre
    (0.3048, 2),     # foot (the US survey foot falls within the tolerance)
    (0.0254, 1),     # inch
    (0.9144, 10),    # yard
    (1609.344, 3),   # mile
)
_IMPERIAL = frozenset({1, 2, 3, 10})


def project_unit_scale(ifc):
    """Metres per project length unit: 1.0 for METRE, 0.3048 for FOOT."""
    return ifcopenshell.util.unit.calculate_unit_scale(ifc)


def dxf_insunits(unit_scale):
    """DXF $INSUNITS code for a unit of `unit_scale` metres; 0 (unitless)
    when no DXF unit matches."""
    for metres, code in _INSUNITS:
        if abs(unit_scale - metres) <= metres * 1e-5:
            return code
    return 0


def is_imperial(insunits):
    """True for the DXF units that set $MEASUREMENT to English (0)."""
    return insunits in _IMPERIAL
