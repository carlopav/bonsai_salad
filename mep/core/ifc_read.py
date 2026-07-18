# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Pure Python, no bpy: importable and testable outside Blender.

"""Rebuild the editable tracciato and the Network from an IFC model.

Inverse of ifc_export: pipes become skeleton edges, their port endpoints are
welded into vertices by following IfcRelConnectsPorts through the fittings
(and by coincident position as a fallback). The open SOURCE port is the
outfall; open SINK leaves take their appliance from a connected terminal
element when present (cascade lookup in appliances.py).
"""

from __future__ import annotations

from .appliances import appliance_of
from .graph import Network
from .skeleton import Skeleton, to_network


def _port_point(port, scale):
    import ifcopenshell.util.placement

    matrix = ifcopenshell.util.placement.get_local_placement(port.ObjectPlacement)
    return tuple(round(float(c) * scale, 4) for c in matrix[:3, 3])


def skeleton_from_ifc(f, circuit_name: str | None = None) -> tuple[Skeleton, list[str]]:
    """(skeleton, warnings) from the circuit's pipes and port connectivity."""
    import ifcopenshell.util.system
    import ifcopenshell.util.unit

    warnings: list[str] = []
    products = None
    if circuit_name:
        for circuit in f.by_type("IfcDistributionCircuit"):
            if circuit.Name == circuit_name:
                products = [obj for rel in circuit.IsGroupedBy
                            for obj in rel.RelatedObjects]
                break
        if products is None:
            warnings.append(f"circuito {circuit_name} non trovato: uso tutto il file")
    if products is None:
        products = list(f.by_type("IfcPipeSegment")) + list(f.by_type("IfcPipeFitting"))

    pipes = [p for p in products if p.is_a("IfcPipeSegment")]
    fittings = {p.id() for p in products if p.is_a("IfcPipeFitting")}
    scale = ifcopenshell.util.unit.calculate_unit_scale(f)

    # Pipe endpoints: (pipe id, end) -> position; port id -> endpoint key.
    endpoints: dict[tuple, tuple] = {}
    port_endpoint: dict[int, tuple] = {}
    port_element: dict[int, object] = {}
    for pipe in pipes:
        sink = source = None
        for port in ifcopenshell.util.system.get_ports(pipe):
            port_element[port.id()] = pipe
            if port.FlowDirection == "SINK" and sink is None:
                sink = port
            elif port.FlowDirection == "SOURCE" and source is None:
                source = port
        if sink is None or source is None:
            warnings.append(f"tubo {pipe.Name}: porte mancanti, escluso")
            continue
        for end, port in (("in", sink), ("out", source)):
            key = (pipe.id(), end)
            endpoints[key] = _port_point(port, scale)
            port_endpoint[port.id()] = key
    for fitting_id in fittings:
        for port in ifcopenshell.util.system.get_ports(f.by_id(fitting_id)):
            port_element[port.id()] = f.by_id(fitting_id)

    # Union-find over endpoints: joined by rels (through fittings) or position.
    parent: dict = {key: key for key in endpoints}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b):
        if a in parent and b in parent:
            parent[find(a)] = find(b)

    fitting_groups: dict[int, list] = {}
    terminal_of: dict[tuple, object] = {}  # endpoint key -> non-pipe element
    outfall_element_of: dict[tuple, object] = {}
    for rel in f.by_type("IfcRelConnectsPorts"):
        pair = []
        for port in (rel.RelatingPort, rel.RelatedPort):
            if port is None:
                continue
            owner = port_element.get(port.id())
            if port.id() in port_endpoint:
                pair.append(port_endpoint[port.id()])
            elif owner is not None and owner.id() in fittings:
                pair.append(("fitting", owner.id()))
            elif owner is None:
                # Port of an element outside the circuit (terminal/receptor).
                element = getattr(port, "Nests", None)
                element = element[0].RelatingObject if element else None
                pair.append(("element", element, port.FlowDirection))
        if len(pair) != 2:
            continue
        a, b = pair
        if a[0] == "fitting" or b[0] == "fitting":
            fitting_id = a[1] if a[0] == "fitting" else b[1]
            other = b if a[0] == "fitting" else a
            if other[0] not in ("fitting", "element"):
                fitting_groups.setdefault(fitting_id, []).append(other)
        elif a[0] == "element" or b[0] == "element":
            element_entry = a if a[0] == "element" else b
            other = b if a[0] == "element" else a
            if other[0] not in ("fitting", "element") and element_entry[1] is not None:
                if element_entry[2] in ("SOURCE", "SOURCEANDSINK"):
                    terminal_of[other] = element_entry[1]
                else:
                    outfall_element_of[other] = element_entry[1]
        else:
            union(a, b)
    for group in fitting_groups.values():
        for key in group[1:]:
            union(group[0], key)
    # Fallback weld: coincident endpoints (already rounded to 0.1 mm).
    by_position: dict[tuple, tuple] = {}
    for key, position in endpoints.items():
        if position in by_position:
            union(key, by_position[position])
        else:
            by_position[position] = key

    # Build the skeleton.
    sk = Skeleton()
    vertex_of: dict = {}
    for key in endpoints:
        root = find(key)
        if root not in vertex_of:
            sk.verts.append(endpoints[root])
            vertex_of[root] = len(sk.verts) - 1
    for pipe in pipes:
        key_in, key_out = (pipe.id(), "in"), (pipe.id(), "out")
        if key_in in endpoints and key_out in endpoints:
            sk.edges.append((vertex_of[find(key_in)], vertex_of[find(key_out)]))

    # Open SOURCE end = outfall; open SINK leaves = appliance attachment points.
    connected = set()
    for rel in f.by_type("IfcRelConnectsPorts"):
        for port in (rel.RelatingPort, rel.RelatedPort):
            if port is not None and port.id() in port_endpoint:
                connected.add(port_endpoint[port.id()])
    open_sources = [key for key in port_endpoint.values()
                    if key not in connected and key[1] == "out"]

    for key, element in terminal_of.items():
        appliance = appliance_of(element)
        if appliance:
            sk.appliances[vertex_of[find(key)]] = appliance

    if outfall_element_of:
        key = next(iter(outfall_element_of))
        sk.outfall = vertex_of[find(key)]
    elif len(open_sources) == 1:
        sk.outfall = vertex_of[find(open_sources[0])]
    elif not open_sources:
        warnings.append("nessuna porta SOURCE aperta: recapito non identificato")
    else:
        sk.outfall = vertex_of[find(open_sources[0])]
        warnings.append(f"{len(open_sources)} porte SOURCE aperte: "
                        "recapito ambiguo, usata la prima")
    return sk, warnings


def network_from_ifc(f, circuit_name: str | None = None) -> tuple[Network, list[str]]:
    """Network rebuilt from the model, ready for re-sizing/verification."""
    sk, warnings = skeleton_from_ifc(f, circuit_name)
    return to_network(sk), warnings
