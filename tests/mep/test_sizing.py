# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import pytest

from mep.core import (
    Network,
    Node,
    NodeKind,
    SegmentKind,
    SizingError,
    load_en12056,
    report_json,
    size_network,
)


@pytest.fixture(scope="module")
def tables():
    return load_en12056()


def bathroom() -> Network:
    net = Network()
    net.add_node(Node("wc", NodeKind.TERMINAL, appliance="wc_cassetta_6.0l"))
    net.add_node(Node("lavabo", NodeKind.TERMINAL, appliance="lavabo"))
    net.add_node(Node("giunto", NodeKind.JUNCTION))
    net.add_node(Node("base_colonna", NodeKind.JUNCTION))
    net.add_node(Node("pozzetto", NodeKind.OUTFALL))
    net.connect("wc", "giunto", SegmentKind.BRANCH, length_m=1.0)
    net.connect("lavabo", "giunto", SegmentKind.BRANCH, length_m=2.0)
    net.connect("giunto", "base_colonna", SegmentKind.STACK, length_m=6.0)
    net.connect("base_colonna", "pozzetto", SegmentKind.COLLECTOR, length_m=8.0, slope_pct=2.0)
    return net


def test_bathroom_sizing(tables):
    results = {r.segment.id: r for r in size_network(bathroom(), tables)}

    # WC branch: q = max(0.5*sqrt(2), DU 2.0) = 2.0 l/s; DN 80 excluded (senza WC),
    # prospetto 4 -> DN 90 (max due WC).
    wc = results["wc->giunto"]
    assert wc.q_design_ls == 2.0
    assert wc.dn == 90

    # Lavabo branch: q = max(0.5*sqrt(0.5), 0.5) = 0.5 l/s -> DN 40.
    assert results["lavabo->giunto"].dn == 40

    # Stack: q = 2.0 l/s would give DN 80, but WCs upstream -> min DN 100 (prospetto 11).
    stack = results["giunto->base_colonna"]
    assert stack.ud_total == 2.5
    assert stack.dn == 100

    # Collector at 2 cm/m, h/d = 0.5: DN 100 (Qmax 3.5 >= 2.0), never below upstream DN.
    collector = results["base_colonna->pozzetto"]
    assert collector.dn == 100
    assert collector.riempimento_hd == 0.5


def test_dn_never_decreases_downstream(tables):
    results = list(size_network(bathroom(), tables))
    dn_at = {}
    for record in results:
        upstream_dn = dn_at.get(record.segment.upstream, 0)
        assert record.dn >= upstream_dn
        dn_at[record.segment.downstream] = max(
            dn_at.get(record.segment.downstream, 0), record.dn
        )


def test_collector_slope_below_minimum_rejected(tables):
    net = bathroom()
    net.segments[-1].slope_pct = 0.2  # below the 0.5 cm/m of prospetto B.1
    with pytest.raises(SizingError, match="below the norm minimum"):
        size_network(net, tables)


def test_unknown_appliance_rejected(tables):
    net = bathroom()
    net.nodes["wc"].appliance = "sauna"
    with pytest.raises(SizingError, match="unknown appliance"):
        size_network(net, tables)


def test_report_json_shape(tables):
    results = size_network(bathroom(), tables)
    report = report_json(results, "Casa X", "Linea 1", "geberit_pe", "2026-07-10")
    assert report["progetto"] == "Casa X"
    assert len(report["tratti"]) == 4
    tratto = next(t for t in report["tratti"] if t["id"] == "wc->giunto")
    assert tratto["dn_mm"] == 90
    assert tratto["ud_cumulate"] == 2.0
    # Every value carries its norm citation for the Typst report.
    per = {r["per"] for r in tratto["riferimenti"]}
    assert "DU wc_cassetta_6.0l" in per
    assert "DN diramazione" in per
    assert all(r["norma"] == "UNI EN 12056-2:2001" for r in tratto["riferimenti"])
