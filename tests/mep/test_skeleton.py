# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import pytest

from mep.core import (
    SegmentKind, Skeleton, apply_slopes, load_en12056, orthogonal_skeleton,
    route_along, size_network, to_network, validate_skeleton,
)

TERMINALS = [
    ("wc_cassetta_6.0l", (3.0, 2.0, 0.0)),
    ("lavabo", (4.5, 2.0, 0.0)),
    ("wc_cassetta_6.0l", (3.0, 2.0, 3.0)),
    ("lavabo", (4.5, 2.0, 3.0)),
]


def proposal() -> Skeleton:
    return orthogonal_skeleton(TERMINALS, stack_xy=(0.0, 0.0),
                               outfall=(15.0, 0.0, -1.1), stack_base_z=-0.8)


def test_orthogonal_proposal_is_valid():
    sk = proposal()
    assert validate_skeleton(sk) == []
    # 4 terminals + 4 branch corners + base + 2 junctions + outfall
    # (collector is already aligned in plan: no corner)
    assert len(sk.verts) == 12
    assert len(sk.edges) == 11
    # Manhattan: every edge is axis-aligned in plan or vertical.
    for a, b in sk.edges:
        dx = abs(sk.verts[a][0] - sk.verts[b][0])
        dy = abs(sk.verts[a][1] - sk.verts[b][1])
        assert dx < 1e-6 or dy < 1e-6


def test_validation_errors():
    sk = proposal()
    assert validate_skeleton(Skeleton(sk.verts, sk.edges, sk.appliances, None)) \
        == ["recapito non impostato"]

    orphan = Skeleton(sk.verts + [(9.0, 9.0, 9.0)], sk.edges, sk.appliances, sk.outfall)
    assert any("senza percorso" in e for e in validate_skeleton(orphan))

    ring = Skeleton(sk.verts, sk.edges + [(0, 2)], sk.appliances, sk.outfall)
    assert any("anelli" in e for e in validate_skeleton(ring))

    missing = Skeleton(sk.verts, sk.edges, {}, sk.outfall)
    assert sum("senza apparecchio" in e for e in validate_skeleton(missing)) == 4


def test_to_network_kinds_from_geometry():
    net = to_network(proposal())
    kinds = {}
    for seg in net.segments:
        kinds.setdefault(seg.kind, 0)
        kinds[seg.kind] += 1
    # 4 appliance branches split at the Manhattan corner (2 edges each),
    # 2 stack drops (base->p1->p2), 1 collector
    assert kinds[SegmentKind.BRANCH] == 8
    assert kinds[SegmentKind.STACK] == 2
    assert kinds[SegmentKind.COLLECTOR] == 1


def test_apply_slopes_moves_junctions_not_terminals():
    sk = proposal()
    sloped = apply_slopes(sk, branch_slope_pct=1.0, collector_slope_pct=2.0)
    for i in sk.appliances:
        assert sloped.verts[i] == sk.verts[i]
    # Collector: base climbs from the outfall at 2 cm/m over 15 m of run.
    base = next(i for i, v in enumerate(sk.verts) if v[:2] == (0.0, 0.0) and v[2] == -0.8)
    assert sloped.verts[base][2] == pytest.approx(-1.1 + 15.0 * 0.02)
    assert validate_skeleton(sloped) == []


def test_port_direction_stubs():
    sk = orthogonal_skeleton(
        [("wc_cassetta_6.0l", (3.0, 2.0, 0.0), (0.0, 0.0, -1.0)),  # floor drain
         ("lavabo", (4.5, 2.0, 0.0), (0.0, 1.0, 0.0))],            # wall outlet
        stack_xy=(0.0, 0.0), outfall=(15.0, 0.0, -1.1),
        outfall_direction=(1.0, 0.0, 0.0), stack_base_z=-0.8)
    assert validate_skeleton(sk) == []
    # WC stub: straight down 0.4 from the port.
    assert (3.0, 2.0, -0.4) in sk.verts
    # Lavabo stub: 0.4 along +Y from the port.
    assert (4.5, 2.4, 0.0) in sk.verts
    # Arrival stub against the outfall SINK port Z.
    assert (15.4, 0.0, -1.1) in sk.verts
    # Short vertical stubs are branches, never stacks.
    net = to_network(sk)
    stub = next(s for s in net.segments if s.upstream == "v0")
    assert stub.kind is SegmentKind.BRANCH


def test_apply_slopes_preserves_vertical_stubs():
    sk = orthogonal_skeleton(
        [("wc_cassetta_6.0l", (3.0, 2.0, 0.0), (0.0, 0.0, -1.0))],
        stack_xy=(0.0, 0.0), outfall=(15.0, 0.0, -1.1), stack_base_z=-0.8)
    sloped = apply_slopes(sk)
    i = sk.verts.index((3.0, 2.0, -0.4))
    # Still exactly below the appliance after slope normalization.
    assert sloped.verts[i][:2] == (3.0, 2.0)
    assert sloped.verts[i][2] < 0.0
    assert validate_skeleton(sloped) == []


def test_skeleton_to_pipes_end_to_end():
    sk = apply_slopes(proposal())
    net = to_network(sk)
    sizings = size_network(net, load_en12056())
    routed = route_along(net, sizings)
    # One pipe per edge, following it exactly.
    assert len(routed.pipes) == len(sk.edges)
    assert routed.total_length_m() == pytest.approx(
        sum(s.length_m for s in net.segments))
    # Flows merge at the floor junctions: braghe present; stack min DN with WC.
    assert any(f.tipo == "braga" for f in routed.fittings)
    stack_dns = {r.dn for r in sizings if r.segment.kind is SegmentKind.STACK}
    assert stack_dns == {100}
