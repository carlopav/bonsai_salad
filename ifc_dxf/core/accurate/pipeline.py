"""Accurate pipeline: OCC/HLR-based, matches Bonsai's own SVG export.

Calls ifcopenshell's native SVG/HLR serializer (ifcopenshell.geom.serializers.svg,
built on OpenCASCADE's HLRBRep engine -- the same engine Bonsai's own SVG export
uses) to get hidden-line-removed section-cut outlines for elements that need a
true 3D section (walls, columns). Everything else uses the same native-2D-
representation BLOCK/INSERT logic as the approximate pipeline's Bucket A
(doors, windows, furniture, sanitary fixtures, ...) -- see plan_symbols.py.
Both paths share the same _write_dxf writer, template and annotation handling
as the approximate pipeline. No separate OCC Python bindings are required --
the HLR engine is built into the standard ifcopenshell wheel.

v1 scope (see ifc_dxf/README.md, Pipeline B): linework only.
- No hatches yet for HLR section cuts (cut-surface fills need Shapely
  polygonize + raycast, mirroring Bonsai's SHAPELY fill mode).
- No below-cut "view" geometry yet for HLR classes -- only entities that
  actually cross the cut plane appear (the SVG serializer's "section" group).
- The serializer's "projection" catch-all group is dropped, not drawn: its
  paths were empirically found to be positioned unrelated to the element's
  real placement (verified even for a single isolated element), likely tied
  to how shared/mapped Type representations are handled internally. Drawing
  it would be actively misleading, so it's counted and discarded instead.
"""

import os
import re
import time
import multiprocessing
import xml.etree.ElementTree as ET

import numpy as np
import ifcopenshell
import ifcopenshell.geom

from ..camera import camera_pos_dir_ref, camera_matrix_inv_col_major
from ..ifc_query import get_elements, _get_drawing_annotations
from ..dxf_template import _parse_scale_factor
from ..dxf_writer import _write_dxf
from ..plan_symbols import place_plan_symbol


_SVG_NS = "{http://www.w3.org/2000/svg}"

# Elements needing a true 3D section cut go through HLR. Matches the
# approximate pipeline's own _SECTION_CLASSES exactly, so both pipelines
# agree on which classes get a real section vs. a native 2D plan symbol.
_HLR_SECTION_CLASSES = frozenset({"IfcWall", "IfcWallStandardCase", "IfcColumn"})

# Matches "M-1.23,4.56" / "L1.23,-4.56" tokens in a polygonal (straight-
# segment-only) SVG path 'd' attribute. setPolygonal(True) + setUseHlrPoly(True)
# guarantee no curve commands (C/A/Q) are ever emitted.
_PATH_CMD_RE = re.compile(
    r'([ML])(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?),(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
)


def _parse_svg_path_segments(d):
    """Split a polygonal SVG path 'd' attribute into line segments.

    A single 'd' may contain several M-delimited subpaths (disjoint loops);
    each M starts a new subpath without emitting a segment, each L connects
    to the previous point.
    """
    segments = []
    last = None
    for cmd, x, y in _PATH_CMD_RE.findall(d):
        pt = (float(x), float(y))
        if cmd == "M":
            last = pt
        else:  # "L"
            if last is not None:
                segments.append((last, pt))
            last = pt
    return segments


def _run_hlr(ifc, drawing, hlr_elements, scale_factor_val):
    """Run the native SVG/HLR serializer over hlr_elements.

    Returns (flat_edges, n_classified, n_dropped) where flat_edges is
    [(p0, p1, layer), ...] for the serializer's per-element "section" groups
    (layer = f"{ifc_class}_Section"); n_dropped counts paths from the
    unreliable "projection" catch-all group, discarded (see module docstring).
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
    if hlr_elements:
        it = ifcopenshell.geom.iterator(gs, ifc, multiprocessing.cpu_count(), include=hlr_elements)
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

    flat_edges = []
    n_classified = n_dropped = 0
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
        layer = f"{cls}_Section"
        for path in paths:
            d = path.get("d")
            if not d:
                continue
            for p0, p1 in _parse_svg_path_segments(d):
                flat_edges.append((p0, p1, layer))
        n_classified += len(paths)

    return flat_edges, n_classified, n_dropped


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

    hlr_elements    = [e for e in elements if e.is_a() in _HLR_SECTION_CLASSES]
    symbol_elements = [e for e in elements if e.is_a() not in _HLR_SECTION_CLASSES]

    flat_edges, n_classified, n_dropped = _run_hlr(ifc, drawing, hlr_elements, scale_factor_val)
    print(f"  HLR paths  : {n_classified} classified, {n_dropped} dropped (unreliable position)")

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
               flat_edges, {},
               annotations=annotations, cam_inv_np=_cam_inv_np,
               template_path=template_path, scale_factor=scale_factor_val,
               drawing_name=getattr(drawing, "Name", None),
               drawing_identification=getattr(drawing, "Identification", None),
               drawing_scale=human_scale)
    print(f"  DXF gen    : {time.perf_counter() - t1:.2f}s")
    size_kb = os.path.getsize(output_path) // 1024
    print(f"  >> {output_path}  ({size_kb} KB)")
    return output_path
