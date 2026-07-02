"""Keyword-based classification of IfcMaterial names -> ACI hatch colour.

Material names are free text set by whoever authored the IFC model, so there
is no reliable schema field to key off. This maps common Italian/English
keywords to a material category and a fixed ACI colour; anything unmatched
falls back to the same grey used by the generic IfcWall_Hatches layer.
"""

from __future__ import annotations

# (category, keywords, aci_color)
_MATERIAL_CATEGORIES: list[tuple[str, tuple[str, ...], int]] = [
    ("concrete",  ("calcestruzzo", "cls", "cemento", "beton", "concrete"), 253),
    ("insulation", ("isolante", "isolamento", "coibente", "coibentazione",
                     "insulation", "eps", "xps", "polistirene", "poliuretano",
                     "lana di vetro", "lana di roccia", "lana minerale"), 51),
    ("masonry", ("laterizio", "mattone", "muratura", "forato", "masonry", "brick"), 11),
    ("wood", ("legno", "legname", "wood", "timber", "abete", "larice",
              "lamellare", "rovere", "pino"), 43),
]

_DEFAULT_COLOR = 254  # matches the generic IfcWall_Hatches layer colour


def classify_material_color(material_name: str | None) -> int:
    """Return the ACI colour index for a material name, or the default grey."""
    if not material_name:
        return _DEFAULT_COLOR
    name = material_name.lower()
    for _category, keywords, color in _MATERIAL_CATEGORIES:
        if any(kw in name for kw in keywords):
            return color
    return _DEFAULT_COLOR
