# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import math

import pytest

from mep.core import (
    Network, Node, NodeKind, RoutingError, SegmentKind,
    load_en12056, route_network, size_network,
)


def small_line() -> Network:
    """wc -> giunto -> pozzetto: one horizontal+drop branch, one collector."""
    net = Network()
    net.add_node(Node("wc", NodeKind.TERMINAL, appliance="wc_cassetta_6.0l",
                      position=(2.0, 0.0, 1.0)))
    net.add_node(Node("giunto", NodeKind.JUNCTION, position=(0.0, 0.0, 0.0)))
    net.add_node(Node("pozzetto", NodeKind.OUTFALL, position=(5.0, 0.0, -0.1)))
    net.connect("wc", "giunto", SegmentKind.BRANCH)
    net.connect("giunto", "pozzetto", SegmentKind.COLLECTOR, slope_pct=2.0)
    return net


@pytest.fixture(scope="module")
def routed():
    net = small_line()
    sizings = size_network(net, load_en12056())
    return route_network(net, sizings)


def test_branch_splits_into_run_and_drop(routed):
    legs = [p for p in routed.pipes if p.segment_id == "wc->giunto"]
    assert len(legs) == 2
    run, drop = legs
    # Horizontal leg falls at the default 1% slope over 2 m of plan run.
    assert run.end[2] == pytest.approx(1.0 - 0.02)
    # Vertical leg lands on the junction.
    assert drop.start[:2] == drop.end[:2]
    assert drop.end == (0.0, 0.0, 0.0)


def test_corner_and_single_inlet_joint_get_curva(routed):
    tipi = {(f.at, f.tipo) for f in routed.fittings}
    assert ("wc->giunto@corner", "curva") in tipi
    # One incoming flow turning into the collector: curva, not braga.
    assert ("giunto", "curva") in tipi
    joint_curva = next(f for f in routed.fittings if f.at == "giunto")
    assert joint_curva.dn == 100  # collector DN


def test_merging_flows_get_chained_braghe():
    net = small_line()
    net.add_node(Node("lavabo", NodeKind.TERMINAL, appliance="lavabo",
                      position=(-2.0, 0.0, 1.0)))
    net.add_node(Node("doccia", NodeKind.TERMINAL, appliance="doccia_senza_tappo",
                      position=(0.0, 2.0, 1.0)))
    net.connect("lavabo", "giunto", SegmentKind.BRANCH)
    net.connect("doccia", "giunto", SegmentKind.BRANCH)
    sizings = size_network(net, load_en12056())
    routed = route_network(net, sizings)
    braghe = [f for f in routed.fittings if f.tipo == "braga"]
    # 3 incoming -> 2 braghe in series on the collector DN.
    assert len(braghe) == 2
    assert all(f.dn == 100 for f in braghe)
    assert sorted(f.dn_derivazione for f in braghe) == [40, 50]  # lavabo, doccia
    joint = next(j for j in routed.joints if j.node_id == "giunto")
    assert len(joint.incoming_segments) == 3
    assert joint.outgoing_segment == "giunto->pozzetto"


def test_collector_follows_designed_slope(routed):
    legs = [p for p in routed.pipes if p.segment_id == "giunto->pozzetto"]
    # 5 m at 2 cm/m -> 0.1 m drop, matching the outfall level: single sloped leg.
    assert len(legs) == 1
    assert legs[0].end[2] == pytest.approx(-0.1)
    assert legs[0].length_m == pytest.approx(math.dist((0, 0, 0), (5, 0, -0.1)))


def test_missing_position_rejected():
    net = small_line()
    net.nodes["wc"].position = None
    sizings = size_network(net, load_en12056())
    with pytest.raises(RoutingError, match="no position"):
        route_network(net, sizings)
