# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Pure Python, no bpy: importable and testable outside Blender.

"""The editable tracciato as plain data (spec §8).

A Skeleton mirrors an edges-only Blender mesh: verts + edges, an appliance key
on terminal leaves, one outfall vertex. Operators only translate bmesh <->
Skeleton; everything else (orthogonal proposal, validation, slope
normalization, conversion to a sized Network) lives here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .graph import Network, Node, NodeKind, SegmentKind

Vec = tuple[float, float, float]

_MERGE_TOL_M = 0.001
_VERTICAL_TOL = 0.999  # |dz|/length above this = stack edge
_SHORT_STACK_M = 1.0  # vertical runs up to this are appliance drops, not stacks


@dataclass
class Skeleton:
    verts: list[Vec] = field(default_factory=list)
    edges: list[tuple[int, int]] = field(default_factory=list)
    appliances: dict[int, str] = field(default_factory=dict)  # leaf vert -> key
    outfall: int | None = None

    def valence(self, index: int) -> int:
        return sum(index in edge for edge in self.edges)


def orthogonal_skeleton(
    terminals: list[tuple],  # (appliance key, position[, port Z direction])
    stack_xy: tuple[float, float],
    outfall: Vec,
    junction_drop_m: float = 0.3,
    stack_base_z: float | None = None,
    outfall_direction: Vec | None = None,
    stub_m: float = 0.4,
) -> Skeleton:
    """Initial proposal: per-floor junction -> stack -> base -> outfall.

    Terminals are grouped by z (floor); each floor gets a junction below the
    lowest terminal, on the stack XY; the stack base sits below the outfall
    level, then a collector run reaches the outfall. Runs are Manhattan
    (L-shaped in plan, shared corners weld into tees); intermediate z is
    interpolated — apply_slopes normalizes it afterwards.

    When a port direction is given (local Z of the IfcDistributionPort =
    outgoing connection direction), the run leaves the terminal with a stub
    along it, and arrives at the outfall against its SINK port direction.
    """
    sk = Skeleton()
    index: dict[tuple, int] = {}

    def vert(p: Vec, weld: bool = True) -> int:
        key = tuple(round(c, 4) for c in p)
        if weld and key in index:
            return index[key]
        sk.verts.append(tuple(map(float, p)))
        index[key] = len(sk.verts) - 1
        return index[key]

    def stub(from_index: int, direction: Vec | None) -> int:
        if direction is None:
            return from_index
        length = math.sqrt(sum(c * c for c in direction))
        if length < 1e-6:
            return from_index
        origin = sk.verts[from_index]
        end = vert(tuple(o + c / length * stub_m for o, c in zip(origin, direction)))
        sk.edges.append((from_index, end))
        return end

    def manhattan(a_index: int, b_index: int) -> None:
        """a -> corner (a.x, b.y) -> b; direct edge when already aligned."""
        a, b = sk.verts[a_index], sk.verts[b_index]
        if abs(a[0] - b[0]) < _MERGE_TOL_M or abs(a[1] - b[1]) < _MERGE_TOL_M:
            sk.edges.append((a_index, b_index))
            return
        leg1 = abs(a[1] - b[1])
        total = leg1 + abs(a[0] - b[0])
        corner = vert((a[0], b[1], a[2] + (b[2] - a[2]) * leg1 / total))
        sk.edges.append((a_index, corner))
        sk.edges.append((corner, b_index))

    floors: dict[float, list[int]] = {}
    for entry in terminals:
        appliance, position = entry[0], entry[1]
        direction = entry[2] if len(entry) > 2 else None
        i = vert(position, weld=False)
        sk.appliances[i] = appliance
        floors.setdefault(round(position[2], 3), []).append(stub(i, direction))

    base_z = stack_base_z if stack_base_z is not None else outfall[2] + 0.001
    base = vert((stack_xy[0], stack_xy[1], base_z))
    previous = base
    for z in sorted(floors):
        junction = vert((stack_xy[0], stack_xy[1], z - junction_drop_m))
        for i in floors[z]:
            manhattan(i, junction)
        sk.edges.append((junction, previous))
        previous = junction

    sk.outfall = vert(outfall, weld=False)
    approach = stub(sk.outfall, outfall_direction)
    manhattan(base, approach)
    return sk


def validate(sk: Skeleton) -> list[str]:
    """Errors preventing sizing; empty list = valid tree."""
    errors = []
    if sk.outfall is None or not 0 <= sk.outfall < len(sk.verts):
        return ["recapito non impostato"]

    for i, a in enumerate(sk.verts):
        for j in range(i + 1, len(sk.verts)):
            if math.dist(a, sk.verts[j]) < _MERGE_TOL_M:
                errors.append(f"vertici sovrapposti {i}/{j}: merge by distance")

    adjacency: dict[int, list[int]] = {}
    for a, b in sk.edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    seen = {sk.outfall}
    stack = [sk.outfall]
    while stack:
        for other in adjacency.get(stack.pop(), []):
            if other not in seen:
                seen.add(other)
                stack.append(other)
    unreachable = set(range(len(sk.verts))) - seen
    if unreachable:
        errors.append(f"vertici senza percorso al recapito: {sorted(unreachable)}")
    elif len(sk.edges) != len(sk.verts) - 1:
        errors.append("il tracciato contiene anelli: deve essere un albero")

    for i in range(len(sk.verts)):
        if sk.valence(i) == 1 and i != sk.outfall and i not in sk.appliances:
            errors.append(f"foglia {i} senza apparecchio assegnato")
    return errors


def _parents(sk: Skeleton) -> dict[int, int]:
    """Vertex -> next vertex toward the outfall (tree assumed valid)."""
    adjacency: dict[int, list[int]] = {}
    for a, b in sk.edges:
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    parents: dict[int, int] = {}
    stack = [sk.outfall]
    seen = {sk.outfall}
    while stack:
        current = stack.pop()
        for other in adjacency.get(current, []):
            if other not in seen:
                seen.add(other)
                parents[other] = current
                stack.append(other)
    return parents


def _is_stack_edge(a: Vec, b: Vec) -> bool:
    length = math.dist(a, b)
    return length > 0 and abs(a[2] - b[2]) / length > _VERTICAL_TOL


def to_network(sk: Skeleton) -> Network:
    """Network with one node per vertex and one segment per edge.

    Segment kinds from geometry: vertical = stack; horizontal upstream of a
    stack = branch; downstream = collector; direct appliance run with no stack
    = branch. Slopes from the actual z drop.
    """
    parents = _parents(sk)
    net = Network()
    for i, position in enumerate(sk.verts):
        if i == sk.outfall:
            kind = NodeKind.OUTFALL
        elif i in sk.appliances:
            kind = NodeKind.TERMINAL
        else:
            kind = NodeKind.JUNCTION
        net.add_node(Node(f"v{i}", kind, appliance=sk.appliances.get(i),
                          position=position))

    def stack_downstream(index: int) -> bool:
        while index in parents:
            if _is_stack_edge(sk.verts[index], sk.verts[parents[index]]):
                return True
            index = parents[index]
        return False

    # Vertices touched by a long vertical run: short verticals sharing one
    # continue the stack; isolated short drops (appliance stubs, arrival
    # drops) are sized as branches.
    stack_verts: set[int] = set()
    for child, parent in parents.items():
        a, b = sk.verts[child], sk.verts[parent]
        if _is_stack_edge(a, b) and math.dist(a, b) > _SHORT_STACK_M:
            stack_verts.update((child, parent))

    for child, parent in parents.items():
        a, b = sk.verts[child], sk.verts[parent]
        if _is_stack_edge(a, b):
            long_enough = math.dist(a, b) > _SHORT_STACK_M
            continues_stack = child in stack_verts or parent in stack_verts
            kind = (SegmentKind.STACK if long_enough or continues_stack
                    else SegmentKind.BRANCH)
            slope = None
        else:
            run = math.dist(a[:2], b[:2])
            slope = round((a[2] - b[2]) / run * 100.0, 3) if run > 0 else None
            if stack_downstream(parent) or child in sk.appliances:
                kind = SegmentKind.BRANCH
            else:
                kind = SegmentKind.COLLECTOR
        net.connect(f"v{child}", f"v{parent}", kind,
                    length_m=math.dist(a, b), slope_pct=slope)
    return net


def apply_slopes(
    sk: Skeleton,
    branch_slope_pct: float = 1.0,  # prospetto 5, sistema I minimum
    collector_slope_pct: float = 2.0,
) -> Skeleton:
    """Recompute junction z walking root -> leaves: horizontal edges climb at
    the design slope, stack edges keep their length, terminal z stays fixed."""
    parents = _parents(sk)
    children: dict[int, list[int]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)

    net = to_network(sk)  # kinds from the current geometry
    kind_of = {int(s.upstream[1:]): s.kind for s in net.segments}
    verts = list(sk.verts)

    def walk(parent: int) -> None:
        for child in children.get(parent, []):
            a, b = sk.verts[child], verts[parent]
            vertical = math.dist(a[:2], b[:2]) < _MERGE_TOL_M
            if kind_of[child] is SegmentKind.STACK or vertical:
                # Preserve the original drop (stacks and short vertical stubs).
                verts[child] = (a[0], a[1],
                                verts[parent][2] + (a[2] - sk.verts[parents[child]][2]))
            elif child not in sk.appliances:
                run = math.dist(a[:2], b[:2])
                slope = (branch_slope_pct if kind_of[child] is SegmentKind.BRANCH
                         else collector_slope_pct)
                verts[child] = (a[0], a[1], b[2] + run * slope / 100.0)
            walk(child)

    walk(sk.outfall)
    return Skeleton(verts, list(sk.edges), dict(sk.appliances), sk.outfall)
