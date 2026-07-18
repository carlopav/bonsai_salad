# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import os

import pytest

import ifcopenshell

from mep.core import (
    SegmentKind, apply_slopes, export_network, load_en12056, network_from_ifc,
    orthogonal_skeleton, route_along, size_network, skeleton_from_ifc,
    to_network, validate_skeleton,
)

TERMINALS = [
    ("wc_cassetta_6.0l", (3.0, 2.0, 0.0)),
    ("lavabo", (4.5, 2.0, 0.0)),
    ("wc_cassetta_6.0l", (3.0, 2.0, 3.0)),
    ("lavabo", (4.5, 2.0, 3.0)),
]


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    sk = apply_slopes(orthogonal_skeleton(
        TERMINALS, stack_xy=(0.0, 0.0), outfall=(15.0, 0.0, -1.1),
        stack_base_z=-0.8))
    net = to_network(sk)
    sizings = size_network(net, load_en12056())
    routed = route_along(net, sizings)
    path = os.path.join(tmp_path_factory.mktemp("ifc"), "roundtrip.ifc")
    export_network(routed, path, sizings=sizings, linea="Linea RT")
    return sk, net, path


def test_skeleton_roundtrip(exported):
    sk, _net, path = exported
    rebuilt, warnings = skeleton_from_ifc(ifcopenshell.open(path), "Linea RT")
    assert warnings == []
    assert len(rebuilt.verts) == len(sk.verts)
    assert len(rebuilt.edges) == len(sk.edges)
    # Same welded geometry, outfall recovered from the open SOURCE port.
    assert sorted(tuple(round(c, 3) for c in v) for v in rebuilt.verts) == \
        sorted(tuple(round(c, 3) for c in v) for v in sk.verts)
    assert rebuilt.outfall is not None
    assert tuple(round(c, 3) for c in rebuilt.verts[rebuilt.outfall]) == (15.0, 0.0, -1.1)


def test_network_roundtrip_kinds(exported):
    sk, net, path = exported
    rebuilt, _warnings = network_from_ifc(ifcopenshell.open(path), "Linea RT")
    original = sorted((s.kind.value, round(s.length_m, 3)) for s in net.segments)
    recovered = sorted((s.kind.value, round(s.length_m, 3)) for s in rebuilt.segments)
    assert recovered == original
    # Appliances are not in the file (no terminal elements): leaves unmarked.
    errors = validate_skeleton(skeleton_from_ifc(ifcopenshell.open(path), "Linea RT")[0])
    assert sum("senza apparecchio" in e for e in errors) == len(TERMINALS)


def test_missing_circuit_warns(exported):
    _sk, _net, path = exported
    _rebuilt, warnings = skeleton_from_ifc(ifcopenshell.open(path), "Altra Linea")
    assert any("non trovato" in w for w in warnings)
