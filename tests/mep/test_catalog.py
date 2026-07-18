# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import os

import pytest

import ifcopenshell
import ifcopenshell.util.element

from mep.catalog import available_systems, load_system
from mep.core import (
    Network, Node, NodeKind, SegmentKind, export_network, load_en12056,
    route_along, size_network, to_network,
)


def test_available_systems():
    assert available_systems() == ["geberit_pe", "geberit_silent_pp", "pvc_sn4_sn8"]


def test_pvc_dn_mapping():
    pvc = load_system("pvc_sn4_sn8")
    # EN 12056 DN -> commercial DN/OD per EN 1401.
    assert pvc.pipe_for_dn(100)["dn"] == 110
    assert pvc.pipe_for_dn(150)["dn"] == 160
    assert pvc.pipe_for_dn(300)["dn"] == 315
    assert pvc.pipe_for_dn(90) is None  # not made in PVC
    assert pvc.pipe_profile_m(100) == (0.055, 0.0032)  # de 110, SN4 e_min 3.2


def test_export_uses_catalog_profile(tmp_path):
    net = Network()
    net.add_node(Node("wc", NodeKind.TERMINAL, appliance="wc_cassetta_6.0l",
                      position=(0.0, 0.0, 0.0)))
    net.add_node(Node("pozzetto", NodeKind.OUTFALL, position=(10.0, 0.0, -0.2)))
    net.connect("wc", "pozzetto", SegmentKind.COLLECTOR, slope_pct=2.0)
    sizings = size_network(net, load_en12056())
    routed = route_along(net, sizings)
    path = os.path.join(tmp_path, "pvc.ifc")
    export_network(routed, path, sizings=sizings, catalog=load_system("pvc_sn4_sn8"))

    f = ifcopenshell.open(path)
    t = f.by_type("IfcPipeSegmentType")[0]
    assert t.Name == "Tubo pvc_sn4_sn8 110"
    pset = ifcopenshell.util.element.get_psets(t)["Pset_PipeSegmentTypeCommon"]
    assert pset["OuterDiameter"] == pytest.approx(0.110)
    assert pset["InnerDiameter"] == pytest.approx(0.110 - 2 * 0.0032)
    profile = f.by_type("IfcCircleHollowProfileDef")[0]
    assert profile.Radius == pytest.approx(0.055)
    assert profile.WallThickness == pytest.approx(0.0032)