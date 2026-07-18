# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""Quantity takeoff for bonsaibim_computo_contabilita_ita.

With one IfcPipeSegmentType/IfcPipeFittingType per DN(+angle) as catalog
master, the takeoff is: total length per segment type, occurrence count per
fitting type, article codes from mep/catalog/*/articles.yaml.
"""

from __future__ import annotations


def quantities_by_type(ifc, system_entity) -> dict:
    """Return {type name: {"count": n, "length_m": total, "articolo": code}}."""
    raise NotImplementedError("depends on ifc_export (core/ifc_export.py)")
