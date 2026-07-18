# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""IFC writing for the routed network, via ifcopenshell.api (upstream style).

The IFC is the single source of truth (spec §8): sizing values and norm
citations live in psets, not in side files — the Typst report is derived by
reading the model.

write_network targets an existing model (the loaded Bonsai file): contexts,
system, circuit and types are found and reused; regenerating a line removes
the circuit's previous occurrences first. export_network wraps it for a
standalone file. Lengths are converted to the model's units (mm projects).

Model structure:
- one IfcPipeSegmentType per DN / IfcPipeFittingType per (tipo, DN): catalog
  masters (profile from the catalog when transcribed); occurrences reference
  them (takeoff = count/length per type)
- standard psets: Pset_PipeSegmentTypeCommon (NominalDiameter, Outer/Inner
  Diameter) on types; Pset_PipeSegmentOccurrence (Gradient, InvertElevation,
  InteriorRoughnessCoefficient = kb 1,0 mm dei prospetti B) and
  Qto_PipeSegmentBaseQuantities.Length on occurrences;
  Pset_DistributionPortCommon.PortNumber on ports
- custom pset EN12056_Dimensionamento (output only, written at generation):
  SommaDU, portate, grado di riempimento and the norm citations per segment
- grouping: IfcDistributionSystem (DRAINAGE) + one IfcDistributionCircuit per
  line; spatial containment in the given (or first) building
- representations: Body (SweptSolid, MODEL_VIEW) + Axis (Curve3D, GRAPH_VIEW)
  per segment; the editable skeleton mesh is rebuilt from these (ifc_read)
- connectivity: IfcDistributionPort SINK/SOURCE per end, one directed
  IfcRelConnectsPorts per connection, pipe->fitting->pipe; open SINKs at the
  appliances, one open SOURCE at the outfall
"""

from __future__ import annotations

import math

from .routing import RoutedNetwork
from .sizing import SegmentSizing

_WALL_MM = 3.0  # placeholder wall thickness until catalog data is transcribed
_KB_M = 0.001  # interior roughness kb = 1,0 mm (EN 12056-2, appendice B)


def _matrix(start, direction=None):
    """4x4 placement matrix at start, local Z along direction (or world Z)."""
    import numpy as np

    m = np.eye(4)
    if direction is not None:
        z = np.array(direction, dtype=float)
        z /= np.linalg.norm(z)
        ref = np.array((1.0, 0.0, 0.0)) if abs(z[0]) < 0.9 else np.array((0.0, 1.0, 0.0))
        x = np.cross(ref, z)
        x /= np.linalg.norm(x)
        m[:3, 0], m[:3, 1], m[:3, 2] = x, np.cross(z, x), z
    m[:3, 3] = start
    return m


def _get_context(f, identifier, target_view):
    import ifcopenshell.api as api
    import ifcopenshell.util.representation

    context = ifcopenshell.util.representation.get_context(
        f, "Model", identifier, target_view)
    if context is not None:
        return context
    model = ifcopenshell.util.representation.get_context(f, "Model")
    if model is None:
        model = api.run("context.add_context", f, context_type="Model")
    return api.run("context.add_context", f, context_type="Model",
                   context_identifier=identifier, target_view=target_view,
                   parent=model)


def write_network(
    f,
    routed: RoutedNetwork,
    sizings: list[SegmentSizing] | None = None,
    linea: str = "Linea 1",
    catalog=None,
    container=None,
) -> list:
    """Write the routed network into the model; returns the new products.

    Regenerating an existing line: the circuit's previous pipe/fitting
    occurrences are removed first (types are kept and reused).
    """
    import ifcopenshell.api as api
    import ifcopenshell.util.unit

    sized = {s.segment.id: s for s in (sizings or [])}
    # Geometry below is computed in meters; models may use mm.
    u = 1.0 / ifcopenshell.util.unit.calculate_unit_scale(f)

    body_ctx = _get_context(f, "Body", "MODEL_VIEW")
    axis_ctx = _get_context(f, "Axis", "GRAPH_VIEW")

    if container is None:
        buildings = f.by_type("IfcBuilding") or f.by_type("IfcSite")
        container = buildings[0] if buildings else None

    system = next(
        (s for s in f.by_type("IfcDistributionSystem")
         if not s.is_a("IfcDistributionCircuit") and s.PredefinedType == "DRAINAGE"),
        None,
    )
    if system is None:
        system = api.run("system.add_system", f, ifc_class="IfcDistributionSystem")
        system.Name = "Scarico acque reflue"
        system.PredefinedType = "DRAINAGE"

    circuit = next(
        (c for c in f.by_type("IfcDistributionCircuit") if c.Name == linea), None)
    if circuit is None:
        circuit = api.run("system.add_system", f, ifc_class="IfcDistributionCircuit")
        circuit.Name = linea
        api.run("group.assign_group", f, products=[circuit], group=system)
    else:
        for rel in list(circuit.IsGroupedBy):
            for product in list(rel.RelatedObjects):
                if product.is_a("IfcPipeSegment") or product.is_a("IfcPipeFitting"):
                    api.run("root.remove_product", f, product=product)

    def add_pset(product, name, props):
        pset = api.run("pset.add_pset", f, product=product, name=name)
        api.run("pset.edit_pset", f, pset=pset, properties=props)

    def add_qto(product, name, quantities):
        qto = api.run("pset.add_qto", f, product=product, name=name)
        api.run("pset.edit_qto", f, qto=qto, properties=quantities)

    def pipe_profile(dn: int) -> tuple[float, float]:
        """(outer radius, wall) in meters: catalog data, placeholder fallback."""
        entry = catalog.pipe_profile_m(dn) if catalog is not None else None
        return entry or (dn / 2000.0, _WALL_MM / 1000.0)

    def circle_body(radius_m: float, depth_m: float, wall_m: float | None = None):
        profile = f.createIfcCircleHollowProfileDef(
            "AREA", None,
            f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0.0, 0.0))),
            radius_m * u, min(wall_m or _WALL_MM / 1000.0, radius_m * 0.5) * u,
        )
        solid = f.createIfcExtrudedAreaSolid(
            profile,
            f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0))),
            f.createIfcDirection((0.0, 0.0, 1.0)), depth_m * u,
        )
        return f.createIfcShapeRepresentation(body_ctx, "Body", "SweptSolid", [solid])

    def axis_curve(depth_m: float):
        polyline = f.createIfcPolyline([
            f.createIfcCartesianPoint((0.0, 0.0, 0.0)),
            f.createIfcCartesianPoint((0.0, 0.0, depth_m * u)),
        ])
        return f.createIfcShapeRepresentation(axis_ctx, "Axis", "Curve3D", [polyline])

    def find_type(ifc_class, name):
        return next((t for t in f.by_type(ifc_class) if t.Name == name), None)

    pipe_types: dict[int, object] = {}
    fitting_types: dict[tuple[str, int], object] = {}
    products = []

    def port(element, position, flow_direction, number):
        p = api.run("system.add_port", f, element=element)
        p.FlowDirection = flow_direction
        api.run("geometry.edit_object_placement", f, product=p, matrix=_matrix(position))
        add_pset(p, "Pset_DistributionPortCommon", {"PortNumber": number})
        return p

    def connect(source_port, sink_port):
        api.run("system.connect_port", f, port1=source_port, port2=sink_port,
                direction="SOURCE")

    # -- pipes: one leg = one occurrence, SINK/SOURCE port at either end ------
    seg_legs: dict[str, list[tuple[object, object]]] = {}  # seg id -> (inlet, outlet)
    for pipe in routed.pipes:
        radius, wall = pipe_profile(pipe.dn)
        if pipe.dn not in pipe_types:
            entry = catalog.pipe_for_dn(pipe.dn) if catalog is not None else None
            commercial = entry.get("dn") if entry else None
            name = (f"Tubo {catalog.name} {commercial}" if commercial
                    else f"Tubo DN{pipe.dn}")
            t = find_type("IfcPipeSegmentType", name)
            if t is None:
                t = api.run("root.create_entity", f, ifc_class="IfcPipeSegmentType",
                            name=name, predefined_type="RIGIDSEGMENT")
                add_pset(t, "Pset_PipeSegmentTypeCommon", {
                    "NominalDiameter": pipe.dn / 1000.0 * u,
                    "OuterDiameter": radius * 2 * u,
                    "InnerDiameter": (radius - wall) * 2 * u,
                })
            pipe_types[pipe.dn] = t
        occ = api.run("root.create_entity", f, ifc_class="IfcPipeSegment",
                      name=pipe.segment_id)
        api.run("type.assign_type", f, related_objects=[occ],
                relating_type=pipe_types[pipe.dn])
        api.run("geometry.edit_object_placement", f, product=occ,
                matrix=_matrix(pipe.start, pipe.direction))
        api.run("geometry.assign_representation", f, product=occ,
                representation=circle_body(radius, pipe.length_m, wall))
        api.run("geometry.assign_representation", f, product=occ,
                representation=axis_curve(pipe.length_m))
        products.append(occ)

        occurrence = {
            "InteriorRoughnessCoefficient": _KB_M * u,
            "InvertElevation": (min(pipe.start[2], pipe.end[2]) - radius) * u,
        }
        run = math.dist(pipe.start[:2], pipe.end[:2])
        if run > 0.01:  # sloped horizontal leg; vertical legs have no gradient
            occurrence["Gradient"] = (pipe.start[2] - pipe.end[2]) / run
        add_pset(occ, "Pset_PipeSegmentOccurrence", occurrence)
        add_qto(occ, "Qto_PipeSegmentBaseQuantities", {"Length": pipe.length_m * u})

        record = sized.get(pipe.segment_id)
        if record:
            sizing_props = {
                "SommaDU": record.ud_total,
                "PortataAcqueReflueLs": record.qww_ls,
                "PortataProgettoLs": record.q_design_ls,
                "Riferimenti": "; ".join(
                    f"{r['per']}: {r['norma']}, {r['voce']}" for r in record.refs
                ),
            }
            if record.riempimento_hd:
                sizing_props["GradoRiempimento"] = record.riempimento_hd
            add_pset(occ, "EN12056_Dimensionamento", sizing_props)

        seg_legs.setdefault(pipe.segment_id, []).append(
            (port(occ, pipe.start, "SINK", 1), port(occ, pipe.end, "SOURCE", 2))
        )

    # -- fittings ---------------------------------------------------------------
    fitting_occs: list[tuple[object, object]] = []  # (RoutedFitting, occurrence)
    for fitting in routed.fittings:
        key = (fitting.tipo, fitting.dn)
        if key not in fitting_types:
            name = f"{fitting.tipo.capitalize()} DN{fitting.dn}"
            t = find_type("IfcPipeFittingType", name)
            if t is None:
                t = api.run("root.create_entity", f, ifc_class="IfcPipeFittingType",
                            name=name,
                            predefined_type="BEND" if fitting.tipo == "curva"
                            else "JUNCTION")
                add_pset(t, "Pset_PipeFittingTypeCommon", {"Reference": fitting.tipo})
            fitting_types[key] = t
        occ = api.run("root.create_entity", f, ifc_class="IfcPipeFitting",
                      name=f"{fitting.tipo} {fitting.at}")
        api.run("type.assign_type", f, related_objects=[occ],
                relating_type=fitting_types[key])
        radius, wall = pipe_profile(fitting.dn)
        api.run("geometry.edit_object_placement", f, product=occ,
                matrix=_matrix((fitting.position[0], fitting.position[1],
                                fitting.position[2] - radius)))
        api.run("geometry.assign_representation", f, product=occ,
                representation=circle_body(radius * 1.2, radius * 2, wall))
        products.append(occ)
        fitting_occs.append((fitting, occ))

    # -- port graph: pipe -> fitting -> pipe ------------------------------------
    # Corners: the two legs of a split segment, through the corner curva.
    corner_occs = {ft.at: (ft, occ) for ft, occ in fitting_occs if ft.at.endswith("@corner")}
    for seg_id, pairs in seg_legs.items():
        for (_, out1), (in2, _) in zip(pairs, pairs[1:]):
            entry = corner_occs.get(f"{seg_id}@corner")
            if entry is None:
                connect(out1, in2)
                continue
            ft, occ = entry
            connect(out1, port(occ, ft.position, "SINK", 1))
            connect(port(occ, ft.position, "SOURCE", 2), in2)

    # Joints: through the node curva, or the chain of braghe (first incoming
    # runs through the main inlets, the others branch in).
    node_occs: dict[str, list[tuple[object, object]]] = {}
    for ft, occ in fitting_occs:
        if not ft.at.endswith("@corner"):
            node_occs.setdefault(ft.at, []).append((ft, occ))
    for joint in routed.joints:
        current = seg_legs[joint.incoming_segments[0]][-1][1]  # first inlet's outlet
        branch_outlets = [seg_legs[s][-1][1] for s in joint.incoming_segments[1:]]
        for ft, occ in node_occs.get(joint.node_id, []):
            number = 1
            connect(current, port(occ, joint.position, "SINK", number))
            if ft.tipo == "braga" and branch_outlets:
                number += 1
                connect(branch_outlets.pop(0), port(occ, joint.position, "SINK", number))
            current = port(occ, joint.position, "SOURCE", number + 1)
        connect(current, seg_legs[joint.outgoing_segment][0][0])

    if container is not None:
        api.run("spatial.assign_container", f, products=products,
                relating_structure=container)
    api.run("system.assign_system", f, products=products, system=system)
    api.run("system.assign_system", f, products=products, system=circuit)
    return products


def export_network(
    routed: RoutedNetwork,
    path: str,
    sizings: list[SegmentSizing] | None = None,
    project_name: str = "MEP",
    linea: str = "Linea 1",
    catalog=None,
) -> None:
    """Write the routed network to a standalone IFC4 file."""
    import ifcopenshell.api as api

    f = api.run("project.create_file", version="IFC4")
    project = api.run("root.create_entity", f, ifc_class="IfcProject", name=project_name)
    # Explicit meters: the api default is millimeters, routing works in meters.
    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    api.run("aggregate.assign_object", f, products=[site], relating_object=project)
    api.run("aggregate.assign_object", f, products=[building], relating_object=site)

    write_network(f, routed, sizings=sizings, linea=linea, catalog=catalog,
                  container=building)
    f.write(path)
