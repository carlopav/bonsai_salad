# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""Parametric geometry generators for this system.

Each function takes parameters read from data.yaml (DN, angle, ...) and
returns the IFC geometry for the part master (IfcPipeSegmentType /
IfcPipeFittingType), not a static mesh per SKU. Planned implementation via
ifcopenshell.util.shape_builder (circular extrusion for straight pipes,
revolved sweep from FxF + radius for bends).
"""


def straight_pipe(dn: int, length_mm: float):
    """Circular hollow extrusion for a straight run."""
    raise NotImplementedError


def bend(dn: int, angle_deg: float):
    """Sweep along an arc; radius derived from FxF dimension and angle."""
    raise NotImplementedError
