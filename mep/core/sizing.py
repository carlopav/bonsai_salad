# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Pure Python, no bpy: importable and testable outside Blender.

"""EN 12056-2 sizing engine for gravity drainage networks.

Per segment, leaf-to-root: cumulative DU -> Qww = K * sqrt(sum DU) (§6.3.1),
design flow = max(Qww, largest single appliance DU) (§6.3.4), minimum DN from
the table matching the segment kind (branches: prospetto 4; stacks: prospetti
11/12; horizontal collectors: prospetti B.1/B.2 at the segment slope). DN never
decreases in the flow direction (§5.5). Every value carries its norm citation,
ready for the Typst report (mep/reports/).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .graph import Network, NodeKind, Segment, SegmentKind
from .norms import En12056, NormRef


class SizingError(ValueError):
    """The network cannot be sized against the norm tables."""


@dataclass
class SegmentSizing:
    segment: Segment
    ud_total: float
    qww_ls: float
    q_design_ls: float
    dn: int
    pendenza_cm_m: float | None = None
    riempimento_hd: float | None = None
    refs: list[dict] = field(default_factory=list)


def _resolve_du(network: Network, tables: En12056, system: str) -> dict[str, dict]:
    """Fill terminal DU values from the appliance table; return citations by node id."""
    refs: dict[str, dict] = {}
    for node in network.nodes.values():
        if node.kind is not NodeKind.TERMINAL or node.appliance is None:
            continue
        entry = tables.apparecchi.get(node.appliance)
        if entry is None:
            raise SizingError(f"unknown appliance for node {node.id}: {node.appliance}")
        du = entry.du.get(system)
        if du is None:
            raise SizingError(
                f"appliance {node.appliance} is not allowed in system {system}"
            )
        node.ud = du
        refs[node.id] = entry.ref.as_dict(f"DU {node.appliance}")
    return refs


def _size_branch(q: float, wc_count: int, tables: En12056, system: str) -> tuple[int, NormRef]:
    candidates = []
    for row in tables.diramazioni:
        dn = row.dn.get(system)
        if dn is None or row.qmax_ls < q:
            continue
        max_wc = (row.max_wc or {}).get(system)
        if max_wc is not None and wc_count > max_wc:
            continue
        candidates.append((row.qmax_ls, dn, row.ref))
    if not candidates:
        raise SizingError(
            f"branch flow {q:.2f} l/s ({wc_count} WC) exceeds unventilated branch "
            f"capacity (system {system}); the branch must be ventilated"
        )
    qmax, dn, ref = min(candidates)
    return dn, ref

def _size_stack(
    q: float, wc_count: int, tables: En12056, system: str, ventilation: str, branch_fitting: str
) -> tuple[int, NormRef]:
    rows = (
        tables.colonne_primaria if ventilation == "primaria" else tables.colonne_secondaria
    )
    attr = (
        "qmax_braga_squadra_ls" if branch_fitting == "squadra" else "qmax_braga_angolo_ls"
    )
    min_dn = tables.colonne_min_dn_con_wc.get(system, 0) if wc_count else 0
    candidates = [
        (row.dn, row.ref)
        for row in rows
        if getattr(row, attr) >= q and row.dn >= min_dn
    ]
    if not candidates:
        raise SizingError(f"stack flow {q:.2f} l/s exceeds prospetto 11/12 capacities")
    return min(candidates)


def _size_collector(
    q: float, slope_cm_m: float, tables: En12056, filling: float
) -> tuple[int, NormRef]:
    rows = tables.collettori.get(filling)
    if rows is None:
        raise SizingError(f"no collector table for filling degree h/d={filling}")
    # Conservative: use the largest tabulated slope not exceeding the actual one.
    slopes = sorted({row.pendenza_cm_m for row in rows})
    usable = [s for s in slopes if s <= slope_cm_m]
    if not usable:
        raise SizingError(
            f"collector slope {slope_cm_m} cm/m below the norm minimum {slopes[0]} cm/m"
        )
    slope = usable[-1]
    candidates = [
        (row.dn, row.ref)
        for row in rows
        if row.pendenza_cm_m == slope and row.qmax_ls >= q
    ]
    if not candidates:
        raise SizingError(
            f"collector flow {q:.2f} l/s exceeds capacities at slope {slope} cm/m"
        )
    return min(candidates)


def size_network(
    network: Network,
    tables: En12056,
    system: str = "I",
    usage: str = "intermittente",
    stack_ventilation: str = "primaria",
    branch_fitting: str = "squadra",
    collector_filling: float = 0.5,
) -> list[SegmentSizing]:
    """Size every segment leaf-to-root; returns records ordered leaf-to-root."""
    network.validate()
    du_refs = _resolve_du(network, tables, system)
    if usage not in tables.k:
        raise SizingError(f"unknown usage class: {usage} (expected one of {sorted(tables.k)})")
    k = tables.k[usage]
    cumulative = network.cumulative_ud()

    # §6.3.4 b): design flow is at least the largest single appliance DU upstream.
    max_du: dict[str, float] = {n: network.nodes[n].ud for n in network.nodes}
    # WC count upstream, for the prospetto 4/11/12 footnote limits.
    wc: dict[str, int] = {
        n: int(bool(node.appliance and node.appliance.startswith("wc")))
        for n, node in network.nodes.items()
    }
    results: list[SegmentSizing] = []
    dn_upstream: dict[str, int] = {}  # node id -> largest DN arriving at it

    for segment in network.segments_leaf_to_root():
        ud = cumulative[segment.id]
        qww = k * math.sqrt(ud) if ud > 0 else 0.0
        q = max(qww, max_du[segment.upstream])
        wc_count = wc[segment.upstream]
        max_du[segment.downstream] = max(max_du[segment.downstream], max_du[segment.upstream])
        wc[segment.downstream] += wc_count

        record = SegmentSizing(segment, ud, round(qww, 2), round(q, 2), 0)
        if segment.upstream in du_refs:
            record.refs.append(du_refs[segment.upstream])
        record.refs.append(tables.k_ref.as_dict(f"K = {k} (uso {usage})"))
        if segment.kind is SegmentKind.BRANCH:
            dn, ref = _size_branch(q, wc_count, tables, system)
            record.refs.append(ref.as_dict("DN diramazione"))
        elif segment.kind is SegmentKind.STACK:
            dn, ref = _size_stack(q, wc_count, tables, system, stack_ventilation, branch_fitting)
            record.refs.append(ref.as_dict("DN colonna"))
        else:
            slope = segment.slope_pct
            if slope is None:
                raise SizingError(f"collector segment {segment.id} has no slope")
            dn, ref = _size_collector(q, slope, tables, collector_filling)
            record.pendenza_cm_m = slope
            record.riempimento_hd = collector_filling
            record.refs.append(ref.as_dict("DN collettore"))

        # §5.5: DN must not decrease in the flow direction.
        dn = max(dn, dn_upstream.get(segment.upstream, 0))
        dn_upstream[segment.downstream] = max(dn_upstream.get(segment.downstream, 0), dn)
        record.dn = dn
        results.append(record)

    return results


def report_json(
    sizings: list[SegmentSizing],
    progetto: str,
    linea: str,
    sistema: str,
    data: str,
) -> dict:
    """Sizing results in the JSON shape read by reports/templates/sizing_report.typ."""
    return {
        "progetto": progetto,
        "linea": linea,
        "sistema": sistema,
        "data": data,
        "tratti": [
            {
                "id": record.segment.id,
                "da": record.segment.upstream,
                "a": record.segment.downstream,
                "ud_cumulate": record.ud_total,
                "dn_mm": record.dn,
                "portata_ls": record.q_design_ls,
                "pendenza_percento": record.pendenza_cm_m or 0,
                "riempimento_hd": record.riempimento_hd or 0,
                "riferimenti": record.refs,
            }
            for record in sizings
        ],
    }
