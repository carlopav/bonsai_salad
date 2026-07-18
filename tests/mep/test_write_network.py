# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import pytest

import ifcopenshell.api as api
import ifcopenshell.util.element

from mep.core import (
    Network, Node, NodeKind, SegmentKind, load_en12056, network_from_ifc,
    route_along, size_network, write_network,
)


def small_net() -> tuple[Network, list]:
    net = Network()
    net.add_node(Node("wc", NodeKind.TERMINAL, appliance="wc_cassetta_6.0l",
                      position=(0.0, 0.0, 0.0)))
    net.add_node(Node("pozzetto", NodeKind.OUTFALL, position=(10.0, 0.0, -0.2)))
    net.connect("wc", "pozzetto", SegmentKind.COLLECTOR, slope_pct=2.0)
    sizings = size_network(net, load_en12056())
    return net, sizings


def mm_model():
    f = api.run("project.create_file", version="IFC4")
    api.run("root.create_entity", f, ifc_class="IfcProject", name="t")
    api.run("unit.assign_unit", f)  # default: millimeters
    api.run("context.add_context", f, context_type="Model")
    api.run("root.create_entity", f, ifc_class="IfcBuilding", name="B")
    return f


def test_write_into_mm_model_scales_lengths():
    f = mm_model()
    net, sizings = small_net()
    products = write_network(f, route_along(net, sizings), sizings=sizings)
    pipe = next(p for p in products if p.is_a("IfcPipeSegment"))
    qto = ifcopenshell.util.element.get_psets(pipe)["Qto_PipeSegmentBaseQuantities"]
    # ~10 m run stored in mm.
    assert qto["Length"] == pytest.approx(10002, abs=1)
    profile = f.by_type("IfcCircleHollowProfileDef")[0]
    assert profile.Radius == pytest.approx(50.0)  # DN 100 placeholder, mm
    # Round-trip scales back to meters.
    rebuilt, _ = network_from_ifc(f, "Linea 1")
    assert rebuilt.segments[0].length_m == pytest.approx(10.002, abs=0.001)


def test_regeneration_replaces_circuit_occurrences():
    f = mm_model()
    net, sizings = small_net()
    write_network(f, route_along(net, sizings), sizings=sizings, linea="L1")
    first_types = len(f.by_type("IfcPipeSegmentType"))
    write_network(f, route_along(net, sizings), sizings=sizings, linea="L1")
    # Occurrences replaced, not duplicated; types reused.
    assert len(f.by_type("IfcPipeSegment")) == 1
    assert len(f.by_type("IfcPipeSegmentType")) == first_types
    assert len(f.by_type("IfcDistributionCircuit")) == 1
    assert len(f.by_type("IfcDistributionSystem")) == 2  # system + circuit


def test_two_lines_share_system_and_types():
    f = mm_model()
    net, sizings = small_net()
    write_network(f, route_along(net, sizings), sizings=sizings, linea="L1")
    write_network(f, route_along(net, sizings), sizings=sizings, linea="L2")
    assert len(f.by_type("IfcDistributionCircuit")) == 2
    assert len([s for s in f.by_type("IfcDistributionSystem")
                if not s.is_a("IfcDistributionCircuit")]) == 1
    assert len(f.by_type("IfcPipeSegmentType")) == 1  # shared per-DN master
