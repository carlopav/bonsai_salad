# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""Example line: two full bathrooms on a stack, outfall at the stack base.

Run from the repo root:  python -m mep.examples.bathrooms
Prints the sizing per segment, the routed pipes/fittings, and writes the
report JSON for reports/templates/sizing_report.typ into reports/generated/.
"""

from __future__ import annotations

import datetime
import json
import os

from mep.catalog import load_system
from mep.core import (
    Network, Node, NodeKind, SegmentKind,
    export_network, load_en12056, report_json, route_network, size_network,
)

BATHROOM = [  # appliance key -> XY position (meters)
    ("wc_cassetta_6.0l", (3.0, 2.0)),
    ("lavabo", (4.5, 2.0)),
    ("bide", (6.0, 2.0)),
    ("doccia_senza_tappo", (7.5, 2.0)),
]


def build_network() -> Network:
    net = Network()
    # Stack at (0, 0); one bathroom per floor, floor heights 3 m.
    for floor, z in (("p1", 0.0), ("p2", 3.0)):
        junction = net.add_node(
            Node(f"giunto_{floor}", NodeKind.JUNCTION, position=(2.0, 1.0, z - 0.3))
        )
        for appliance, (x, y) in BATHROOM:
            node_id = f"{appliance.split('_')[0]}_{floor}"
            net.add_node(
                Node(node_id, NodeKind.TERMINAL, appliance=appliance,
                     position=(x, y, z))
            )
            net.connect(node_id, junction.id, SegmentKind.BRANCH)
        net.add_node(
            Node(f"colonna_{floor}", NodeKind.JUNCTION, position=(0, 0, z - 0.4))
        )
        net.connect(junction.id, f"colonna_{floor}", SegmentKind.BRANCH)

    net.add_node(Node("base_colonna", NodeKind.JUNCTION, position=(0, 0, -0.8)))
    net.add_node(Node("pozzetto", NodeKind.OUTFALL, position=(15.0, 0, -1.1)))
    net.connect("colonna_p2", "colonna_p1", SegmentKind.STACK)
    net.connect("colonna_p1", "base_colonna", SegmentKind.STACK)
    # 15 m at 2 cm/m -> 0.3 m drop, matching the pozzetto level.
    net.connect("base_colonna", "pozzetto", SegmentKind.COLLECTOR, slope_pct=2.0)
    return net


def main() -> None:
    net = build_network()
    tables = load_en12056()
    sizings = size_network(net, tables, system="I", usage="intermittente")

    print(f"{'tratto':32} {'tipo':10} {'DU':>5} {'Q l/s':>6} {'DN':>4}")
    for r in sizings:
        print(f"{r.segment.id:32} {r.segment.kind.value:10} "
              f"{r.ud_total:5.1f} {r.q_design_ls:6.2f} {r.dn:4d}")

    catalog = load_system("geberit_pe")
    routed = route_network(net, sizings, catalog)
    print(f"\npipes: {len(routed.pipes)}  "
          f"(tot {routed.total_length_m():.1f} m)  fittings: {len(routed.fittings)}")
    for w in routed.warnings[:6]:
        print(f"  warning: {w}")
    if len(routed.warnings) > 6:
        print(f"  ... {len(routed.warnings) - 6} more warnings")

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "reports", "generated")
    out_path = os.path.join(out_dir, "esempio_bagni.json")
    report = report_json(sizings, progetto="Esempio", linea="Colonna 1",
                         sistema="geberit_pe", data=datetime.date.today().isoformat())
    with open(out_path, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(f"\nreport JSON: {out_path}")
    print("PDF: typst compile mep/reports/templates/sizing_report.typ "
          "out.pdf --input data=" + out_path)

    ifc_path = os.path.join(out_dir, "esempio_bagni.ifc")
    export_network(routed, ifc_path, sizings=sizings,
                   project_name="Esempio MEP — due bagni", linea="Colonna 1",
                   catalog=catalog)
    print(f"IFC: {ifc_path}  (apribile in Bonsai)")


if __name__ == "__main__":
    main()
