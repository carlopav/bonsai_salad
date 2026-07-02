"""Accurate pipeline: OCC/HLR-based, matches Bonsai's own SVG export.

Calls ifcopenshell's native SVG/HLR serializer (ifcopenshell.geom.serializers.svg,
built on OpenCASCADE's HLRBRep engine -- the same engine Bonsai's own SVG export
uses) for elements that need a true 3D section (walls, columns) and whose Z
range actually straddles the cut plane. Everything else uses the same
native-2D-representation BLOCK/INSERT logic as the approximate pipeline's
Bucket A (doors, windows, furniture, sanitary fixtures, ...) -- see
plan_symbols.py. Walls/columns entirely below the cut plane ("viewed", not
cut) reuse the approximate pipeline's Shapely profile-projection instead of
HLR, for the same reason plan symbols do: HLR's single addDrawing pass only
returns geometry actually sliced by the cut plane (empirically verified --
an isolated below-cut element produces nothing), and for a simple vertical
wall prism the profile projection is already the exact same silhouette a
true top-down HLR projection would give.

All three paths (HLR section, view-wall profile, plan symbol) resolve to the
same shared inputs consumed by _write_dxf: HLR and view-wall polygons both
feed wall_polys_by_key (get Shapely-fused outlines + standard hatch, same as
the approximate pipeline), plan symbols feed block_defs/block_inserts.

v1 scope (see ifc_dxf/README.md, Pipeline B): still no material-layer hatch
decomposition (IfcMaterialLayerSet strips) for HLR/view walls, and the
serializer's "projection" catch-all group is dropped, not drawn -- see the
README for why.
"""

import os
import re
import time
import multiprocessing
import xml.etree.ElementTree as ET

import numpy as np
import ifcopenshell
import ifcopenshell.geom

try:
    import shapely
    _SHAPELY_AVAILABLE = True
except ImportError:
    _SHAPELY_AVAILABLE = False

from ..camera import (
    camera_pos_dir_ref,
    camera_matrix_inv_col_major,
    camera_dir_pos,
    world_matrix_col_major,
)
from ..ifc_query import get_elements, get_material_name, _get_drawing_annotations
from ..dxf_template import _parse_scale_factor
from ..dxf_writer import _write_dxf
from ..plan_symbols import place_plan_symbol
from ..approximate.geometry import _wall_z_range, _extract_wall_polygon_with_openings


_SVG_NS = "{http://www.w3.org/2000/svg}"
_IFC_NS = "{http://www.ifcopenshell.org/ns}"

# Elements needing a true 3D section cut go through HLR (if they cross the
# cut plane) or Shapely profile projection (if they don't). Matches the
# approximate pipeline's own _SECTION_CLASSES exactly, so both pipelines
# agree on which classes get a real section/view vs. a native 2D plan symbol.
_HLR_SECTION_CLASSES = frozenset({"IfcWall", "IfcWallStandardCase", "IfcColumn"})

# Matches "M-1.23,4.56" / "L1.23,-4.56" tokens in a polygonal (straight-
# segment-only) SVG path 'd' attribute. setPolygonal(True) + setUseHlrPoly(True)
# guarantee no curve commands (C/A/Q) are ever emitted.
_PATH_CMD_RE = re.compile(
    r'([ML])(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?),(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
)


def _parse_svg_path_subpaths(d):
    """Split a polygonal SVG path 'd' attribute into point-loops.

    A single 'd' may contain several M-delimited subpaths (disjoint loops);
    each M starts a new loop. HLR body outlines close each loop back to its
    start point, so each loop can be built directly into a Shapely Polygon.
    """
    subpaths = []
    current = []
    for cmd, x, y in _PATH_CMD_RE.findall(d):
        pt = (float(x), float(y))
        if cmd == "M":
            if len(current) >= 3:
                subpaths.append(current)
            current = [pt]
        else:  # "L"
            current.append(pt)
    if len(current) >= 3:
        subpaths.append(current)
    return subpaths


def _run_hlr(ifc, drawing, cut_elements, scale_factor_val):
    """Run the native SVG/HLR serializer over cut_elements (already known to
    straddle the cut plane).

    Returns (polys_by_guid, n_dropped): polys_by_guid maps element GlobalId ->
    list of shapely Polygon (one per closed HLR loop in that element's "body"
    group); n_dropped is the path count from the unreliable "projection"
    catch-all group, discarded (see module docstring).
    """
    pos, view_dir, ref_dir = camera_pos_dir_ref(drawing)

    gs = ifcopenshell.geom.settings()
    gs.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)
    gs.set("iterator-output", ifcopenshell.ifcopenshell_wrapper.NATIVE)

    buf = ifcopenshell.geom.serializers.buffer()
    ss  = ifcopenshell.geom.serializer_settings()
    serialiser = ifcopenshell.geom.serializers.svg(buf, gs, ss)
    serialiser.setPolygonal(True)
    serialiser.setUseHlrPoly(True)
    serialiser.setAlwaysProject(True)
    serialiser.setAutoElevation(False)
    serialiser.setAutoSection(False)
    serialiser.setDrawDoorArcs(False)
    serialiser.setNoCSS(True)
    serialiser.setUseNamespace(True)
    serialiser.setWithoutStoreys(True)
    serialiser.setScale(scale_factor_val)
    serialiser.setFile(ifc)
    serialiser.addDrawing(pos, view_dir, ref_dir, getattr(drawing, "Name", None) or "drawing", True)

    t0 = time.perf_counter()
    n_written = 0
    if cut_elements:
        it = ifcopenshell.geom.iterator(gs, ifc, multiprocessing.cpu_count(), include=cut_elements)
        if it.initialize():
            while True:
                serialiser.write(it.get())
                n_written += 1
                if not it.next():
                    break
    serialiser.finalize()
    svg_text = buf.get_value()
    print(f"  HLR gen    : {time.perf_counter() - t0:.2f}s  ({n_written} elements written to serializer)")

    root = ET.fromstring(svg_text)

    polys_by_guid = {}
    n_dropped = 0
    for g in root.iter(f"{_SVG_NS}g"):
        cls = g.get("class")
        if cls in (None, "section"):
            continue
        paths = g.findall(f"{_SVG_NS}path")
        if not paths:
            continue
        if cls == "projection":
            n_dropped += len(paths)
            continue
        guid = g.get(f"{_IFC_NS}guid")
        for path in paths:
            d = path.get("d")
            if not d:
                continue
            for loop in _parse_svg_path_subpaths(d):
                try:
                    poly = shapely.Polygon(loop)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if poly.is_empty:
                        continue
                except Exception:
                    continue
                polys_by_guid.setdefault(guid, []).append(poly)

    return polys_by_guid, n_dropped


def export_drawing(ifc, drawing, pset, output_path, template_path=None, crease_angle_deg=15.0):
    """Export a single Bonsai drawing to DXF using the accurate (OCC/HLR) pipeline."""
    target_view = pset.get("TargetView", "PLAN_VIEW")
    human_scale = pset.get("HumanScale", "NTS")
    print(f"  TargetView : {target_view}   Scale: {human_scale}   Pipeline: accurate (HLR)")

    if template_path is None:
        pkg_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        template_path = os.path.join(pkg_dir, "templates", "ifc_dxf_template_metric.dxf")
    if os.path.isfile(template_path):
        print(f"  Template   : {os.path.basename(template_path)}")
    else:
        print(f"  Template   : (not found, using minimal fallback)")
        template_path = None

    scale_factor_val = _parse_scale_factor(pset.get("Scale", "")) or 0.01

    elements = get_elements(ifc, drawing, pset)
    print(f"  Elements   : {len(elements)}")

    hlr_classed     = [e for e in elements if e.is_a() in _HLR_SECTION_CLASSES]
    symbol_elements = [e for e in elements if e.is_a() not in _HLR_SECTION_CLASSES]

    cam_dir, cam_pos = camera_dir_pos(drawing)
    cut_z = cam_pos[2]

    # Split wall/column classes into cut (straddles the plane -> real HLR
    # section) vs viewed (entirely below it -> Shapely profile projection),
    # exactly like the approximate pipeline's classify_elements.
    cut_elements, view_elements = [], []
    for elem in hlr_classed:
        wm = world_matrix_col_major(elem)
        z_min, z_max = _wall_z_range(elem, wm)
        is_cut = z_min is None or (z_min <= cut_z <= z_max)
        (cut_elements if is_cut else view_elements).append(elem)

    wall_polys_by_key = {}

    # --- Section (cut) walls/columns: real HLR ---
    polys_by_guid, n_dropped = _run_hlr(ifc, drawing, cut_elements, scale_factor_val)
    n_hlr_polys = 0
    for elem in cut_elements:
        polys = polys_by_guid.get(elem.GlobalId)
        if not polys:
            continue
        ifc_class = elem.is_a()
        key = (ifc_class, get_material_name(elem), f"{ifc_class}_Section", None)
        wall_polys_by_key.setdefault(key, []).extend(polys)
        n_hlr_polys += len(polys)
    print(f"  HLR paths  : {n_hlr_polys} polygons from {len(cut_elements)} elements, "
          f"{n_dropped} projection paths dropped (unreliable position)")

    # --- View walls/columns: Shapely profile projection (same as approximate) ---
    n_view_placed = 0
    if _SHAPELY_AVAILABLE:
        col_major = camera_matrix_inv_col_major(drawing)
        for elem in view_elements:
            wm = world_matrix_col_major(elem)
            try:
                poly, _ = _extract_wall_polygon_with_openings(elem, wm, col_major, cut_z, cam_dir)
            except Exception:
                poly = None
            if poly is None:
                continue
            ifc_class = elem.is_a()
            _, z_max = _wall_z_range(elem, wm)
            z_top_key = round(z_max, 3) if z_max is not None else None
            key = (ifc_class, get_material_name(elem), f"{ifc_class}_View", z_top_key)
            wall_polys_by_key.setdefault(key, []).append(poly)
            n_view_placed += 1
    print(f"  View walls : {n_view_placed}/{len(view_elements)} placed via profile projection")

    col_major   = camera_matrix_inv_col_major(drawing)
    _cam_inv_np = np.array(col_major, dtype=float).reshape(4, 4, order='F')
    _cam_R      = _cam_inv_np[:3, :3]
    _cam_x_proj = _cam_R @ np.array([1.0, 0.0, 0.0])
    _cam_rot_deg = float(np.degrees(np.arctan2(float(_cam_x_proj[1]), float(_cam_x_proj[0]))))

    block_defs    = {}
    block_order   = []
    block_inserts = {}
    seen_blocks   = {}
    n_symbols_placed = 0
    for element in symbol_elements:
        try:
            placed = place_plan_symbol(
                element, element.is_a(), target_view, crease_angle_deg,
                _cam_R, _cam_inv_np, _cam_rot_deg,
                block_defs, block_order, block_inserts, seen_blocks,
            )
        except Exception:
            placed = False
        if placed:
            n_symbols_placed += 1
    print(f"  Plan symbols: {n_symbols_placed}/{len(symbol_elements)} placed via native 2D representation")

    annotations = _get_drawing_annotations(ifc, drawing)
    if annotations:
        print(f"  Annotations: {len(annotations)}"
              f" ({sum(1 for a in annotations if a.ObjectType == 'DIMENSION')} dims)")

    t1 = time.perf_counter()
    _write_dxf(output_path, block_defs, block_order, block_inserts,
               [], wall_polys_by_key,
               annotations=annotations, cam_inv_np=_cam_inv_np,
               template_path=template_path, scale_factor=scale_factor_val,
               drawing_name=getattr(drawing, "Name", None),
               drawing_identification=getattr(drawing, "Identification", None),
               drawing_scale=human_scale)
    print(f"  DXF gen    : {time.perf_counter() - t1:.2f}s")
    size_kb = os.path.getsize(output_path) // 1024
    print(f"  >> {output_path}  ({size_kb} KB)")
    return output_path
