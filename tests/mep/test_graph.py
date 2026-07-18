# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import pytest

from mep.core.graph import Network, NetworkError, Node, NodeKind, SegmentKind


def bathroom() -> Network:
    """wc + lavabo -> junction -> stack -> outfall."""
    net = Network()
    net.add_node(Node("wc", NodeKind.TERMINAL, appliance="wc_cassetta_6.0l", ud=2.0))
    net.add_node(Node("lavabo", NodeKind.TERMINAL, appliance="lavabo", ud=0.5))
    net.add_node(Node("giunto", NodeKind.JUNCTION))
    net.add_node(Node("base_colonna", NodeKind.JUNCTION))
    net.add_node(Node("pozzetto", NodeKind.OUTFALL))
    net.connect("wc", "giunto", SegmentKind.BRANCH, length_m=1.0)
    net.connect("lavabo", "giunto", SegmentKind.BRANCH, length_m=2.0)
    net.connect("giunto", "base_colonna", SegmentKind.STACK, length_m=6.0)
    net.connect("base_colonna", "pozzetto", SegmentKind.COLLECTOR, length_m=8.0, slope_pct=2.0)
    return net


def test_validate_ok():
    bathroom().validate()


def test_cumulative_ud_leaf_to_root():
    net = bathroom()
    totals = net.cumulative_ud()
    assert totals["wc->giunto"] == 2.0
    assert totals["lavabo->giunto"] == 0.5
    assert totals["giunto->base_colonna"] == 2.5
    assert totals["base_colonna->pozzetto"] == 2.5


def test_leaf_to_root_order():
    net = bathroom()
    order = [s.id for s in net.segments_leaf_to_root()]
    assert order.index("giunto->base_colonna") > order.index("wc->giunto")
    assert order.index("base_colonna->pozzetto") > order.index("giunto->base_colonna")


def test_two_outfalls_rejected():
    net = bathroom()
    net.add_node(Node("pozzetto2", NodeKind.OUTFALL))
    with pytest.raises(NetworkError, match="exactly one outfall"):
        net.validate()


def test_orphan_node_rejected():
    net = bathroom()
    net.add_node(Node("bidet", NodeKind.TERMINAL, ud=0.5))
    with pytest.raises(NetworkError, match="no path"):
        net.validate()


def test_double_downstream_rejected():
    net = bathroom()
    with pytest.raises(NetworkError, match="already has a downstream"):
        net.connect("wc", "base_colonna", SegmentKind.BRANCH)


def test_cycle_rejected():
    net = Network()
    net.add_node(Node("a", NodeKind.JUNCTION))
    net.add_node(Node("b", NodeKind.JUNCTION))
    net.add_node(Node("out", NodeKind.OUTFALL))
    net.connect("a", "b", SegmentKind.COLLECTOR)
    net.connect("b", "a", SegmentKind.COLLECTOR)
    with pytest.raises(NetworkError):
        net.validate()
