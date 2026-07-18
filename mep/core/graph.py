# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Pure Python, no bpy: importable and testable outside Blender.

"""Topological model of a drainage network (EN 12056 family 1).

Gravity networks are pure trees: every node drains into exactly one
downstream node, ending at a single outfall. Discharge units (DU/UD)
accumulate leaf-to-root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NodeKind(Enum):
    """Node roles; the mapped IFC class is created by ifc_export."""

    TERMINAL = "terminal"  # sanitary appliance, generates DU -> IfcFlowTerminal
    JUNCTION = "junction"  # collector/branch point -> IfcFlowFitting
    OUTFALL = "outfall"    # downstream delivery (manhole, sewer) -> IfcFlowTerminal


class SegmentKind(Enum):
    """Pipe run roles; each is sized against a different norm table."""

    BRANCH = "branch"        # appliance branch (diramazione)
    STACK = "stack"          # vertical stack (colonna)
    COLLECTOR = "collector"  # horizontal collector (collettore orizzontale)


@dataclass
class Node:
    id: str
    kind: NodeKind
    # TERMINAL only: appliance key into the norms ud_apparecchi table.
    appliance: str | None = None
    # TERMINAL only: own discharge units; filled from the norm table or set directly.
    ud: float = 0.0
    # 3D position in meters (project coords); required by routing, not by sizing.
    position: tuple[float, float, float] | None = None


@dataclass
class Segment:
    upstream: str
    downstream: str
    kind: SegmentKind
    length_m: float = 0.0
    slope_pct: float | None = None  # horizontal runs only

    @property
    def id(self) -> str:
        return f"{self.upstream}->{self.downstream}"


class NetworkError(ValueError):
    """The network violates the tree constraints of a gravity system."""


@dataclass
class Network:
    nodes: dict[str, Node] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)

    def add_node(self, node: Node) -> Node:
        if node.id in self.nodes:
            raise NetworkError(f"duplicate node id: {node.id}")
        self.nodes[node.id] = node
        return node

    def connect(
        self,
        upstream: str,
        downstream: str,
        kind: SegmentKind,
        length_m: float = 0.0,
        slope_pct: float | None = None,
    ) -> Segment:
        for node_id in (upstream, downstream):
            if node_id not in self.nodes:
                raise NetworkError(f"unknown node id: {node_id}")
        if any(s.upstream == upstream for s in self.segments):
            raise NetworkError(f"node {upstream} already has a downstream segment")
        segment = Segment(upstream, downstream, kind, length_m, slope_pct)
        self.segments.append(segment)
        return segment

    # -- structure ------------------------------------------------------------

    def outfall(self) -> Node:
        outfalls = [n for n in self.nodes.values() if n.kind is NodeKind.OUTFALL]
        if len(outfalls) != 1:
            raise NetworkError(f"expected exactly one outfall, found {len(outfalls)}")
        return outfalls[0]

    def validate(self) -> None:
        """Check the tree constraints; raise NetworkError otherwise."""
        root = self.outfall()
        downstream_of = {s.upstream: s.downstream for s in self.segments}
        if root.id in downstream_of:
            raise NetworkError("outfall cannot have a downstream segment")
        for node in self.nodes.values():
            if node is root:
                continue
            if node.id not in downstream_of:
                raise NetworkError(f"node {node.id} has no path to the outfall")
            # Follow the drain path; a tree reaches the root in <= n steps.
            current, hops = node.id, 0
            while current != root.id:
                current = downstream_of.get(current)
                hops += 1
                if current is None or hops > len(self.nodes):
                    raise NetworkError(f"cycle or dead end downstream of {node.id}")

    def upstream_segments(self, node_id: str) -> list[Segment]:
        return [s for s in self.segments if s.downstream == node_id]

    def segments_leaf_to_root(self) -> list[Segment]:
        """Segments ordered so every segment comes after all segments upstream of it."""
        ordered: list[Segment] = []
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            for segment in self.upstream_segments(node_id):
                if segment.upstream not in visited:
                    visited.add(segment.upstream)
                    visit(segment.upstream)
                ordered.append(segment)

        visit(self.outfall().id)
        return ordered

    def cumulative_ud(self) -> dict[str, float]:
        """Total DU drained by each segment (keyed by segment.id), leaf-to-root."""
        node_total = {node_id: self.nodes[node_id].ud for node_id in self.nodes}
        totals: dict[str, float] = {}
        for segment in self.segments_leaf_to_root():
            totals[segment.id] = node_total[segment.upstream]
            node_total[segment.downstream] += node_total[segment.upstream]
        return totals
