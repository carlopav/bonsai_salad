# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Pure Python, no bpy: importable and testable outside Blender.

"""Appliance key (norms ud_apparecchi table) from IFC data, cascade lookup:
explicit EN12056_Utilizzatore pset override first, then standard data
(PredefinedType, Pset_SanitaryTerminalTypeCistern.CisternCapacity). The custom
pset is only needed for variants the standard schema cannot express (doccia
con tappo, orinatoio a valvola/parete, pozzetti per DN).
"""

from __future__ import annotations

# IfcSanitaryTerminalTypeEnum / IfcElectricApplianceTypeEnum -> appliance key.
_PREDEFINED = {
    "WASHHANDBASIN": "lavabo",
    "BIDET": "bide",
    "BATH": "vasca_da_bagno",
    "SINK": "lavello_da_cucina",
    "SHOWER": "doccia_senza_tappo",
    "URINAL": "orinatoio_con_cassetta",
    "DISHWASHER": "lavastoviglie_domestica",
    "WASHINGMACHINE": "lavatrice_max_6kg",
}

_WC_VOLUMES_L = (4.0, 6.0, 7.5, 9.0)  # prospetto 2 cistern classes


def _cistern_liters(psets: dict) -> float | None:
    capacity = psets.get("Pset_SanitaryTerminalTypeCistern", {}).get("CisternCapacity")
    if capacity is None:
        return None
    # IfcVolumeMeasure is in the project volume unit (normally m3); values in
    # plausible liter range are taken as liters.
    return capacity * 1000.0 if capacity <= 0.06 else capacity


def derive_appliance(entity) -> str | None:
    """Appliance key from standard IFC data only, or None."""
    import ifcopenshell.util.element

    type_entity = ifcopenshell.util.element.get_type(entity) or entity
    predefined = getattr(type_entity, "PredefinedType", None)
    if predefined in (None, "NOTDEFINED", "USERDEFINED"):
        predefined = getattr(entity, "PredefinedType", None)

    if predefined == "TOILETPAN":
        liters = _cistern_liters(ifcopenshell.util.element.get_psets(type_entity))
        volume = min(_WC_VOLUMES_L,
                     key=lambda v: abs(v - liters)) if liters else 6.0
        return f"wc_cassetta_{volume}l"
    return _PREDEFINED.get(predefined)


def appliance_of(entity) -> str | None:
    """Cascade: EN12056_Utilizzatore override, then standard data."""
    import ifcopenshell.util.element

    type_entity = ifcopenshell.util.element.get_type(entity)
    for product in (type_entity, entity):
        if product is None:
            continue
        pset = ifcopenshell.util.element.get_pset(product, "EN12056_Utilizzatore")
        if pset and pset.get("Apparecchio"):
            return pset["Apparecchio"]
    return derive_appliance(entity)
