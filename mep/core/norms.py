# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Pure Python, no bpy: importable and testable outside Blender.

"""Loaders for the transcribed norm tables in mep/norms/*/extracted/.

Every value keeps its norm/table citation (NormRef) so sizing results can
cite the exact source in the Typst report. Sources, schemas and transcription
notes are documented in the README.md next to each set of json tables.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


NORMS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "norms")


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


@dataclass(frozen=True)
class NormRef:
    norma: str
    riferimento: str

    def as_dict(self, per: str) -> dict:
        """Citation entry in the shape expected by the Typst report template."""
        return {"norma": self.norma, "voce": self.riferimento, "per": per}


@dataclass(frozen=True)
class ApplianceDU:
    apparecchio: str
    du: dict[str, float | None]  # system ("I".."IV") -> DU in l/s
    nota: str | None
    ref: NormRef


@dataclass(frozen=True)
class BranchCapacity:
    qmax_ls: float
    dn: dict[str, int | None]  # system -> DN
    max_wc: dict[str, int] | None  # system -> max WC count (None = no limit)
    nota: str | None
    ref: NormRef


@dataclass(frozen=True)
class StackCapacity:
    dn: int
    qmax_braga_squadra_ls: float
    qmax_braga_angolo_ls: float
    dn_ventilazione: int | None
    ref: NormRef


@dataclass(frozen=True)
class CollectorCapacity:
    pendenza_cm_m: float
    dn: int
    qmax_ls: float
    v_ms: float
    ref: NormRef


@dataclass(frozen=True)
class En12056:
    apparecchi: dict[str, ApplianceDU]
    k: dict[str, float]  # uso -> K
    k_ref: NormRef
    diametri_interni_minimi: dict[int, int]  # DN -> d_min mm
    diramazioni: list[BranchCapacity]
    diramazioni_limiti: dict[str, dict]  # "sistema_I" -> limits
    colonne_min_dn_con_wc: dict[str, int]  # system -> minimum stack DN with WCs
    colonne_primaria: list[StackCapacity]
    colonne_secondaria: list[StackCapacity]
    collettori: dict[float, list[CollectorCapacity]]  # h/d (0.5, 0.7) -> rows


@dataclass(frozen=True)
class PvcPipeDimension:
    dn_od: int
    de_max_mm: float
    e_min_mm: dict[str, float]  # series ("SN4", "SN8") -> minimum wall thickness
    ref: NormRef


def load_en12056(extracted_dir: str | None = None) -> En12056:
    d = extracted_dir or os.path.join(NORMS_DIR, "en_12056", "extracted")

    data = _load_json(os.path.join(d, "ud_apparecchi.json"))
    ref = NormRef(data["norma"], data["riferimento"])
    apparecchi = {
        row["apparecchio"]: ApplianceDU(row["apparecchio"], row["du"], row.get("nota"), ref)
        for row in data["apparecchi"]
    }

    data = _load_json(os.path.join(d, "coefficiente_k.json"))
    k_ref = NormRef(data["norma"], data["riferimento"])
    k = {row["uso"]: row["k"] for row in data["coefficienti"]}

    data = _load_json(os.path.join(d, "diametri_minimi.json"))
    diametri = {row["dn"]: row["d_min_mm"] for row in data["diametri"]}

    data = _load_json(os.path.join(d, "diramazioni.json"))
    ref = NormRef(data["norma"], data["riferimento"])
    diramazioni = [
        BranchCapacity(row["qmax_ls"], row["dn"], row.get("max_wc"), row.get("nota"), ref)
        for row in data["capacita"]
    ]
    limiti = {
        key: value
        for key, value in data["limiti"].items()
        if key.startswith("sistema_")
    }

    data = _load_json(os.path.join(d, "colonne.json"))
    min_dn_con_wc = data["min_dn_con_wc"]
    colonne: dict[str, list[StackCapacity]] = {}
    for section in ("ventilazione_primaria", "ventilazione_secondaria"):
        ref = NormRef(data["norma"], data[section]["riferimento"])
        colonne[section] = [
            StackCapacity(
                row["dn"],
                row["qmax_braga_squadra_ls"],
                row["qmax_braga_angolo_ls"],
                row.get("dn_ventilazione"),
                ref,
            )
            for row in data[section]["capacita"]
        ]

    data = _load_json(os.path.join(d, "collettori.json"))
    collettori: dict[float, list[CollectorCapacity]] = {}
    for section, hd in (("riempimento_50", 0.5), ("riempimento_70", 0.7)):
        ref = NormRef(data["norma"], data[section]["riferimento"])
        rows = []
        for row in data[section]["capacita"]:
            for key, value in row.items():
                if key.startswith("dn_"):
                    qmax, v = value
                    rows.append(
                        CollectorCapacity(row["pendenza_cm_m"], int(key[3:]), qmax, v, ref)
                    )
        collettori[hd] = rows

    return En12056(
        apparecchi=apparecchi,
        k=k,
        k_ref=k_ref,
        diametri_interni_minimi=diametri,
        diramazioni=diramazioni,
        diramazioni_limiti=limiti,
        colonne_min_dn_con_wc=min_dn_con_wc,
        colonne_primaria=colonne["ventilazione_primaria"],
        colonne_secondaria=colonne["ventilazione_secondaria"],
        collettori=collettori,
    )


def load_en1401(extracted_dir: str | None = None) -> list[PvcPipeDimension]:
    d = extracted_dir or os.path.join(NORMS_DIR, "en_1401", "extracted")
    data = _load_json(os.path.join(d, "dimensioni_sn4_sn8.json"))
    ref = NormRef(data["norma"], data["riferimento"])
    return [
        PvcPipeDimension(
            row["dn_od"],
            row["de_max_mm"],
            {"SN4": row["sn4_e_min_mm"], "SN8": row["sn8_e_min_mm"]},
            ref,
        )
        for row in data["diametri"]
    ]
