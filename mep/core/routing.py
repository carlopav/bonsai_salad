# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Pure Python, no bpy: importable and testable outside Blender.

"""Geometric routing along the sized network (v1 sketch).

Orthogonal strategy: branches and collectors run horizontally with the slope
chosen by sizing (falling toward the downstream node), then drop vertically;
stacks are vertical. Fitting requirements are emitted at direction changes
(curva at segment corners and single-inlet joints) and where flows merge
(n incoming -> n-1 braghe in series, as built), matched against the catalog
raccordi when FxF data is available — unmatched fittings become warnings, so
the routing stays usable while the catalog is being transcribed.

Not yet handled (v1): offset stacks (deviazioni di colonna), fitting FxF
trimming of pipe lengths, obstacle avoidance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .graph import Network, NodeKind, SegmentKind
from .sizing import SegmentSizing

Vec = tuple[float, float, float]

_MIN_RUN_M = 0.01  # legs shorter than this are dropped, not routed
_MIN_BEND_DEG = 1.0  # smaller deviations are treated as straight through


class RoutingError(ValueError):
    """The network cannot be routed (missing positions, unsupported layout)."""


@dataclass
class RoutedPipe:
    segment_id: str
    kind: SegmentKind
    dn: int
    start: Vec
    end: Vec

    @property
    def length_m(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def direction(self) -> Vec:
        length = self.length_m
        return tuple((e - s) / length for s, e in zip(self.start, self.end))


@dataclass
class RoutedFitting:
    at: str  # node id, or "<segment_id>@corner"
    tipo: str  # curva | braga
    dn: int
    position: Vec
    angolo_deg: float
    dn_derivazione: int | None = None
    fxf_mm: float | None = None  # filled when matched against the catalog
    matched: bool = False


@dataclass
class Joint:
    """Where segments meet at a junction node; drives port wiring in ifc_export."""

    node_id: str
    incoming_segments: list[str]  # ordered as the braghe consume them
    outgoing_segment: str
    position: Vec


@dataclass
class RoutedNetwork:
    pipes: list[RoutedPipe] = field(default_factory=list)
    fittings: list[RoutedFitting] = field(default_factory=list)
    joints: list[Joint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def total_length_m(self, dn: int | None = None) -> float:
        return sum(p.length_m for p in self.pipes if dn is None or p.dn == dn)


def _deviation_deg(upstream: RoutedPipe, downstream: RoutedPipe) -> float:
    cos = sum(a * b for a, b in zip(upstream.direction, downstream.direction))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _match_fitting(fitting: RoutedFitting, catalog, warnings: list[str]) -> None:
    """Fill FxF from the closest-angle catalog entry of the same tipo/DN."""
    if catalog is None:
        return
    rows = [
        r for r in catalog.raccordi
        if r.get("tipo") == fitting.tipo and r.get("dn") == fitting.dn
        and (fitting.dn_derivazione is None
             or r.get("dn_derivazione") == fitting.dn_derivazione)
    ]
    if not rows:
        warnings.append(
            f"{fitting.at}: no {fitting.tipo} DN {fitting.dn} in catalog "
            f"{catalog.name} (angle {fitting.angolo_deg} deg)"
        )
        return
    row = min(rows, key=lambda r: abs(r.get("angolo", 0) - fitting.angolo_deg))
    fitting.angolo_deg = row.get("angolo", fitting.angolo_deg)
    fitting.fxf_mm = row.get("fxf_mm")
    fitting.matched = True


def route_network(
    network: Network,
    sizings: list[SegmentSizing],
    catalog=None,
    default_branch_slope_pct: float = 1.0,  # prospetto 5, sistema I minimum
) -> RoutedNetwork:
    """Return placed pipes, fitting requirements and joints for the sized network."""
    for node in network.nodes.values():
        if node.position is None:
            raise RoutingError(f"node {node.id} has no position")

    routed = RoutedNetwork()

    for record in sizings:
        seg = record.segment
        a = network.nodes[seg.upstream].position
        b = network.nodes[seg.downstream].position

        if seg.kind is SegmentKind.STACK:
            if math.dist(a[:2], b[:2]) > _MIN_RUN_M:
                routed.warnings.append(
                    f"{seg.id}: stack offset {math.dist(a[:2], b[:2]):.2f} m "
                    "ignored (deviazioni di colonna not routed in v1)"
                )
            routed.pipes.append(
                RoutedPipe(seg.id, seg.kind, record.dn, (b[0], b[1], a[2]), b)
            )
            continue

        # Horizontal leg: toward the downstream XY, falling at the design slope.
        slope = record.pendenza_cm_m or seg.slope_pct or default_branch_slope_pct
        run = math.dist(a[:2], b[:2])
        corner = (b[0], b[1], a[2] - run * slope / 100.0)
        if run > _MIN_RUN_M:
            routed.pipes.append(RoutedPipe(seg.id, seg.kind, record.dn, a, corner))

        # Vertical leg down to the downstream node, with a curva at the corner.
        drop = corner[2] - b[2]
        if drop > _MIN_RUN_M:
            routed.pipes.append(RoutedPipe(seg.id, seg.kind, record.dn, corner, b))
            if run > _MIN_RUN_M:
                angle = 90.0 - math.degrees(math.atan(slope / 100.0))
                fitting = RoutedFitting(
                    f"{seg.id}@corner", "curva", record.dn, corner, round(angle, 1)
                )
                _match_fitting(fitting, catalog, routed.warnings)
                routed.fittings.append(fitting)
        elif drop < -_MIN_RUN_M:
            routed.warnings.append(
                f"{seg.id}: downstream node {drop:.2f} m above the sloped run "
                "(check levels)"
            )

    _joint_fittings(network, sizings, routed, catalog)
    return routed


def route_along(
    network: Network,
    sizings: list[SegmentSizing],
    catalog=None,
) -> RoutedNetwork:
    """Route a skeleton-derived network: one pipe per segment, following the
    edge exactly (no orthogonal splitting); fittings at the junction nodes."""
    for node in network.nodes.values():
        if node.position is None:
            raise RoutingError(f"node {node.id} has no position")

    routed = RoutedNetwork()
    for record in sizings:
        seg = record.segment
        routed.pipes.append(RoutedPipe(
            seg.id, seg.kind, record.dn,
            network.nodes[seg.upstream].position,
            network.nodes[seg.downstream].position,
        ))
    _joint_fittings(network, sizings, routed, catalog)
    return routed


def _joint_fittings(
    network: Network,
    sizings: list[SegmentSizing],
    routed: RoutedNetwork,
    catalog,
) -> None:
    """Curva when a single inlet changes direction, n-1 braghe in series when
    n flows merge (first incoming runs through, the others branch in)."""
    sized = {s.segment.id: s for s in sizings}
    legs: dict[str, list[RoutedPipe]] = {}
    for pipe in routed.pipes:
        legs.setdefault(pipe.segment_id, []).append(pipe)

    for node in network.nodes.values():
        if node.kind is not NodeKind.JUNCTION:
            continue
        outgoing = [s for s in network.segments if s.upstream == node.id]
        if not outgoing:
            continue
        incoming = network.upstream_segments(node.id)
        out_id = outgoing[0].id
        dn_main = sized[out_id].dn
        routed.joints.append(
            Joint(node.id, [s.id for s in incoming], out_id, node.position)
        )
        if len(incoming) == 1:
            angle = _deviation_deg(legs[incoming[0].id][-1], legs[out_id][0])
            if angle < _MIN_BEND_DEG:
                continue
            fitting = RoutedFitting(
                node.id, "curva", dn_main, node.position, round(angle, 1)
            )
            _match_fitting(fitting, catalog, routed.warnings)
            routed.fittings.append(fitting)
        else:
            for branch in incoming[1:]:
                fitting = RoutedFitting(
                    node.id, "braga", dn_main, node.position, 88.5,
                    dn_derivazione=sized[branch.id].dn,
                )
                _match_fitting(fitting, catalog, routed.warnings)
                routed.fittings.append(fitting)
