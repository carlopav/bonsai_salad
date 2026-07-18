# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""Part library: per-system dimensional data + parametric geometry generators.

Each system folder holds data.json (dimensions), articles.json (article/price
mapping, kept separate: price lists change yearly) and geometry.py (parametric
generators per part family, not static meshes per SKU). Schemas and per-system
notes: see README.md in this folder.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

CATALOG_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


@dataclass(frozen=True)
class CatalogSystem:
    name: str
    tubi: list[dict]
    raccordi: list[dict]
    articoli: list[dict]

    def pipe_for_dn(self, dn: int) -> dict | None:
        """Tube entry for a norm DN (dn_norma maps norm DN to commercial size,
        e.g. EN 12056 DN 100 -> PVC DN/OD 110)."""
        for tube in self.tubi:
            if tube.get("dn_norma", tube.get("dn")) == dn:
                return tube
        return None

    def pipe_profile_m(self, dn: int) -> tuple[float, float] | None:
        """(outer radius, wall thickness) in meters for a norm DN, or None."""
        tube = self.pipe_for_dn(dn)
        if tube is None or "de_mm" not in tube:
            return None
        wall = tube.get("spessore_mm") or tube.get("sn4_spessore_mm")
        if wall is None:
            return None
        return (tube["de_mm"] / 2000.0, wall / 1000.0)


def available_systems() -> list[str]:
    return sorted(
        entry
        for entry in os.listdir(CATALOG_DIR)
        if os.path.isfile(os.path.join(CATALOG_DIR, entry, "data.json"))
    )


def load_system(name: str) -> CatalogSystem:
    d = os.path.join(CATALOG_DIR, name)
    data = _load_json(os.path.join(d, "data.json")) or {}
    articles = _load_json(os.path.join(d, "articles.json")) or {}
    return CatalogSystem(
        name=name,
        tubi=data.get("tubi") or [],
        raccordi=data.get("raccordi") or [],
        articoli=articles.get("articoli") or [],
    )
