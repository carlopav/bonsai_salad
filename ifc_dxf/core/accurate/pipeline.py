"""Accurate pipeline v2: one global OCC/HLR scene pass, used as a visibility
oracle — mirrors Bonsai's own SVG export architecture.

The whole drawing element set plus the camera annotation element is written
into ifcopenshell's native SVG/HLR serializer, configured exactly like
Bonsai's ``setup_serialiser`` (bonsai/bim/module/drawing/operator.py): no
manual ``addDrawing`` — instead ``setElevationRefGuid(drawing.GlobalId)`` and
the camera element itself define the drawing plane — plus
``setSubtractionSettings(ALWAYS)``, ``setUsePrefiltering``, ``setUnifyInputs``,
``setSegmentProjection``, ``setProfileThreshold``. ``finalize()`` then runs
OpenCASCADE hidden-line removal over the whole scene at once, giving the same
global occlusion as Bonsai's SVG plans: elements fully hidden under a slab or
covering produce no output at all.

(Historical note: v1 believed the serializer's ``projection`` group was
mispositioned and that below-cut elements produced nothing. Both were
artifacts of v1's manual ``addDrawing`` configuration — disproven empirically;
see ifc_dxf/README.md, Pipeline B.)

The serializer emits two kinds of geometry, and both must be kept: *closed
loops* (section outlines, full silhouettes) and *open segments* — viewed
geometry arrives as individual 2-point paths (``setSegmentProjection(True)``),
and above-``ProfileThreshold`` objects come as wireframe. Parsing only closed
loops silently discards every viewed element (the v2.0 bug: "no viewed walls,
no terrain in the DXF").

Per-guid SVG output is consumed four ways:

- **visibility oracle**: an element with no output at all (loops *or*
  segments) is hidden -> skipped. Replaces any slab-occlusion heuristic, with
  exact void / double-height behavior, because it *is* the same HLR result
  Bonsai renders.
- **walls/columns** (`_HLR_SECTION_CLASSES`): ``<g class="{IfcClass}">`` loops
  -> ``_Section`` polygons (fused + hatched by the shared writer); closed
  ``projection`` loops minus the section area -> ``_View`` polygons; open
  segments -> chained polylines on the ``_View`` layer (HLR already clipped
  their hidden parts, i.e. correct *partial* occlusion for free).
- **slabs/coverings/roofs** (`_FOOTPRINT_CLASSES`): closed loops -> footprint
  LWPOLYLINE groups (shared writer path); open segments -> chained polylines
  on the class layer.
- **everything else**: native 2D BLOCK/INSERT plan symbols (shared
  ``plan_symbols.place_plan_symbol``), gated by the oracle — a partially
  visible symbol INSERTs its full block (normal CAD convention). Visible
  elements with no usable 2D representation fall back to their HLR output as
  direct polylines (wireframe fallback).

SVG coordinates with this configuration are paper-frame (mm at drawing scale,
y down, origin at the camera box top-left), not camera metres. They are mapped
back deterministically from the camera body's local extents
(``camera.camera_body_local_extents``) — computed, not calibrated.

Only PLAN_VIEW / REFLECTED_PLAN_VIEW are exercised; the transform for mirrored
or non-zenithal views is untested.

Remaining v1 parity gaps (see README roadmap): material-layer hatch
decomposition (item 9) and overhead-fill re-addition (item 11 — note: overhead
elements sit above the cut plane, so they must *bypass* the oracle when
implemented, HLR cannot see them).
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
    camera_matrix_inv_col_major,
    world_matrix_col_major,
    camera_body_local_extents,
)
from ..ifc_query import get_elements, get_material_name, _get_drawing_annotations
from ..dxf_template import _parse_scale_factor
from ..dxf_writer import _write_dxf
from ..plan_symbols import place_plan_symbol, _chain_segments
from ..geometry import _wall_z_range


_SVG_NS = "{http://www.w3.org/2000/svg}"
_IFC_NS = "{http://www.ifcopenshell.org/ns}"

# Walls/columns: HLR section loops -> _Section, projection loops -> _View.
# Matches the approximate pipeline's _SECTION_CLASSES.
_HLR_SECTION_CLASSES = frozenset({"IfcWall", "IfcWallStandardCase", "IfcColumn"})

# Horizontal build-up: HLR loops -> footprint LWPOLYLINE groups, matching the
# approximate pipeline's footprint path (README, Bucket A output rule 2).
_FOOTPRINT_CLASSES = frozenset({"IfcSlab", "IfcCovering", "IfcRoof"})

# Matches "M-1.23,4.56" / "L1.23,-4.56" tokens in a polygonal (straight-
# segment-only) SVG path 'd' attribute. setPolygonal(True) + setUseHlrPoly(True)
# guarantee no curve commands (C/A/Q) are ever emitted.
_PATH_CMD_RE = re.compile(
    r'([ML])(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?),(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
)


def _parse_svg_path_subpaths(d):
    """Split a polygonal SVG path 'd' attribute into point sequences.

    A single 'd' may contain several M-delimited subpaths; each M starts a new
    one. Two kinds occur in the serializer output and both are returned here
    (>= 2 points each): *closed loops* (section outlines and full silhouettes;
    the last point repeats the first) and *open segments* (visible-edge
    linework of viewed geometry: setSegmentProjection(True) splits projection
    output into individual 2-point paths, and above-ProfileThreshold objects
    are emitted as wireframe). Callers must distinguish the two — treating
    only closed loops as real output silently discards every viewed element.
    """
    subpaths = []
    current = []
    for cmd, x, y in _PATH_CMD_RE.findall(d):
        pt = (float(x), float(y))
        if cmd == "M":
            if len(current) >= 2:
                subpaths.append(current)
            current = [pt]
        else:  # "L"
            current.append(pt)
    if len(current) >= 2:
        subpaths.append(current)
    return subpaths


def _setup_serialiser(ifc, drawing, scale_factor_val, target_view):
    """Build the native SVG/HLR serializer configured exactly like Bonsai's
    setup_serialiser (bonsai/bim/module/drawing/operator.py) — same settings,
    same order, so DXF and SVG exports run the identical engine configuration.
    """
    gs = ifcopenshell.geom.settings()
    gs.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)
    gs.set("iterator-output", ifcopenshell.ifcopenshell_wrapper.NATIVE)
    buf = ifcopenshell.geom.serializers.buffer()
    ss  = ifcopenshell.geom.serializer_settings()
    serialiser = ifcopenshell.geom.serializers.svg(buf, gs, ss)
    serialiser.setWithoutStoreys(True)
    serialiser.setPolygonal(True)
    serialiser.setUseHlrPoly(True)
    # Objects with more edges are rendered as wireframe instead of HLR (perf)
    serialiser.setProfileThreshold(10000)
    serialiser.setUseNamespace(True)
    serialiser.setAlwaysProject(True)
    serialiser.setAutoElevation(False)
    serialiser.setAutoSection(False)
    serialiser.setPrintSpaceNames(False)
    serialiser.setPrintSpaceAreas(False)
    serialiser.setDrawDoorArcs(False)
    serialiser.setNoCSS(True)
    serialiser.setElevationRefGuid(drawing.GlobalId)
    serialiser.setScale(scale_factor_val)
    serialiser.setSubtractionSettings(ifcopenshell.ifcopenshell_wrapper.ALWAYS)
    serialiser.setUsePrefiltering(True)  # See IfcOpenShell #3359
    serialiser.setUnifyInputs(True)
    serialiser.setSegmentProjection(True)
    if target_view == "REFLECTED_PLAN_VIEW":
        serialiser.setMirrorY(True)
    serialiser.setFile(ifc)
    return gs, buf, serialiser


def _run_hlr_scene(ifc, drawing, elements, scale_factor_val, target_view):
    """Write the whole element set + the camera element into one serializer
    run and parse the per-guid output.

    Returns (out, n_written, t_hlr) where out maps element GlobalId ->
    {"section": [Polygon], "projection": [Polygon]} in camera-space metres.
    An element absent from `out` produced no visible output: it is fully
    hidden by the global HLR pass (or has no geometry) and must not be drawn.
    """
    gs, buf, serialiser = _setup_serialiser(ifc, drawing, scale_factor_val, target_view)

    t0 = time.perf_counter()
    n_written = 0
    include = list(elements) + [drawing]
    it = ifcopenshell.geom.iterator(gs, ifc, multiprocessing.cpu_count(), include=include)
    if it.initialize():
        while True:
            serialiser.write(it.get())
            n_written += 1
            if not it.next():
                break
    serialiser.finalize()
    svg_text = buf.get_value()
    t_hlr = time.perf_counter() - t0

    ext = camera_body_local_extents(drawing)
    if ext is None:
        raise RuntimeError(
            "accurate pipeline: camera body geometry unavailable, cannot "
            "derive the paper->camera transform"
        )
    x_min_l, _x_max_l, _y_min_l, y_max_l = ext
    factor = 1000.0 * scale_factor_val  # svg units (paper mm) per metre

    # Closed-loop test in svg units (paper mm): serializer loops repeat their
    # first point exactly, so a tight tolerance is safe.
    _CLOSE_TOL = 1e-6

    out = {}
    root = ET.fromstring(svg_text)
    for g in root.iter(f"{_SVG_NS}g"):
        guid = g.get(f"{_IFC_NS}guid")
        if guid is None:
            continue  # storey/section wrapper groups carry no product guid
        classes = (g.get("class") or "").split()
        kind = "projection" if "projection" in classes else "section"
        for path in g.findall(f"{_SVG_NS}path"):
            d = path.get("d")
            if not d:
                continue
            for sub in _parse_svg_path_subpaths(d):
                is_closed = (len(sub) >= 4
                             and abs(sub[0][0] - sub[-1][0]) <= _CLOSE_TOL
                             and abs(sub[0][1] - sub[-1][1]) <= _CLOSE_TOL)
                pts = [(x / factor + x_min_l, y_max_l - y / factor)
                       for x, y in sub]
                rec = out.setdefault(
                    guid, {"section": [], "projection": [], "lines": []})
                if not is_closed:
                    # Open visible-edge linework (setSegmentProjection splits
                    # viewed geometry into segments; wireframe output too).
                    rec["lines"].append(pts)
                    continue
                try:
                    poly = shapely.Polygon(pts)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if poly.is_empty:
                        continue
                except Exception:
                    continue
                polys = ([poly] if isinstance(poly, shapely.Polygon)
                         else [p for p in getattr(poly, "geoms", [])
                               if isinstance(p, shapely.Polygon)])
                rec[kind].extend(polys)
    return out, n_written, t_hlr


def _polys_to_polylines(polys):
    """Closed polyline point lists (first point repeated last) from polygons,
    exterior + interior rings, for the direct_entities wireframe fallback."""
    polylines = []
    for poly in polys:
        polylines.append([(float(x), float(y)) for x, y in poly.exterior.coords])
        for ring in poly.interiors:
            polylines.append([(float(x), float(y)) for x, y in ring.coords])
    return polylines


def _chain_lines(lines):
    """Chain open visible-edge polylines into longer runs by shared endpoints.

    setSegmentProjection emits viewed geometry as individual 2-point paths;
    exploding into segments and rechaining (plan_symbols._chain_segments)
    merges e.g. a viewed wall's four edges back into one closed outline.
    """
    segs = []
    for pts in lines:
        for i in range(len(pts) - 1):
            if pts[i] != pts[i + 1]:
                segs.append((pts[i], pts[i + 1]))
    return _chain_segments(segs)


def export_drawing(ifc, drawing, pset, output_path, template_path=None, crease_angle_deg=15.0):
    """Export a single Bonsai drawing to DXF using the accurate (global HLR
    oracle) pipeline."""
    if not _SHAPELY_AVAILABLE:
        raise RuntimeError("accurate pipeline requires shapely")

    target_view = pset.get("TargetView", "PLAN_VIEW")
    human_scale = pset.get("HumanScale", "NTS")
    print(f"  TargetView : {target_view}   Scale: {human_scale}   Pipeline: accurate (HLR oracle)")

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

    hlr_out, n_written, t_hlr = _run_hlr_scene(
        ifc, drawing, elements, scale_factor_val, target_view
    )
    print(f"  HLR scene  : {t_hlr:.2f}s  ({n_written} elements written, "
          f"{len(hlr_out)} with visible output — global occlusion)")

    col_major   = camera_matrix_inv_col_major(drawing)
    _cam_inv_np = np.array(col_major, dtype=float).reshape(4, 4, order='F')
    _cam_R      = _cam_inv_np[:3, :3]
    _cam_x_proj = _cam_R @ np.array([1.0, 0.0, 0.0])
    _cam_rot_deg = float(np.degrees(np.arctan2(float(_cam_x_proj[1]), float(_cam_x_proj[0]))))

    wall_polys_by_key = {}
    footprint_polys   = []  # [(gid, layer, exterior_pts, [hole_pts])]
    block_defs    = {}
    block_order   = []
    block_inserts = {}
    seen_blocks   = {}
    direct_entities = []  # [{layer, polylines, arcs, circles, ellipses}]

    n_hidden = n_cut = n_view = n_fp = n_sym = n_wire = n_viewlines = 0
    hidden_classes = {}

    # Sorted iteration keeps block_order (and thus the DXF) deterministic.
    for element in sorted(elements, key=lambda e: getattr(e, "GlobalId", "")):
        ifc_class = element.is_a()
        gid = getattr(element, "GlobalId", None)
        rec = hlr_out.get(gid)

        # --- Visibility oracle: no HLR output at all -> hidden (or no geom).
        # Open segments count as output: viewed geometry arrives as segments
        # (setSegmentProjection), not closed loops.
        if rec is None or not (rec["section"] or rec["projection"] or rec["lines"]):
            n_hidden += 1
            hidden_classes[ifc_class] = hidden_classes.get(ifc_class, 0) + 1
            continue

        sec, proj, lines = rec["section"], rec["projection"], rec["lines"]

        if ifc_class in _HLR_SECTION_CLASSES:
            material = get_material_name(element)
            if sec:
                key = (ifc_class, material, f"{ifc_class}_Section", None)
                wall_polys_by_key.setdefault(key, []).extend(sec)
                n_cut += 1
            # Closed projection loops beyond the section area = visible
            # below-cut parts (or the whole wall, if not cut at all).
            view_polys = proj
            if sec and proj:
                try:
                    diff = shapely.unary_union(proj).difference(shapely.unary_union(sec))
                except Exception:
                    diff = None
                if diff is None or diff.is_empty:
                    view_polys = []
                else:
                    geoms = diff.geoms if hasattr(diff, "geoms") else [diff]
                    view_polys = [g for g in geoms
                                  if isinstance(g, shapely.Polygon) and g.area > 1e-6]
            if view_polys:
                wm = world_matrix_col_major(element)
                _, z_max = _wall_z_range(element, wm)
                z_top_key = round(z_max, 3) if z_max is not None else None
                key = (ifc_class, material, f"{ifc_class}_View", z_top_key)
                wall_polys_by_key.setdefault(key, []).extend(view_polys)
            # Open visible-edge segments (the common form for viewed walls):
            # HLR already clipped the hidden parts, draw them as-is on _View.
            if lines:
                chained = _chain_lines(lines)
                if chained:
                    direct_entities.append({"layer": f"{ifc_class}_View",
                                            "polylines": chained,
                                            "arcs": [], "circles": [], "ellipses": []})
                    n_viewlines += len(chained)
            if (view_polys or lines) and not sec:
                n_view += 1

        elif ifc_class in _FOOTPRINT_CLASSES:
            for poly in sec + proj:
                ext_pts = [(float(x), float(y)) for x, y in poly.exterior.coords[:-1]]
                holes = [[(float(x), float(y)) for x, y in ring.coords[:-1]]
                         for ring in poly.interiors]
                if len(ext_pts) >= 3:
                    footprint_polys.append((gid, ifc_class, ext_pts, holes))
            if lines:
                chained = _chain_lines(lines)
                if chained:
                    direct_entities.append({"layer": ifc_class,
                                            "polylines": chained,
                                            "arcs": [], "circles": [], "ellipses": []})
            n_fp += 1

        else:
            try:
                placed = place_plan_symbol(
                    element, ifc_class, target_view, crease_angle_deg,
                    _cam_R, _cam_inv_np, _cam_rot_deg,
                    block_defs, block_order, block_inserts, seen_blocks,
                    direct_entities,
                )
            except Exception:
                placed = False
            if placed:
                n_sym += 1
            else:
                # Wireframe fallback: visible per the oracle but no usable 2D
                # representation -> draw its HLR output directly.
                polylines = _polys_to_polylines(sec + proj) + _chain_lines(lines)
                if polylines:
                    direct_entities.append({"layer": ifc_class, "polylines": polylines,
                                            "arcs": [], "circles": [], "ellipses": []})
                    n_wire += 1

    print(f"  Hidden(HLR): {n_hidden} elements fully occluded or without geometry")
    if hidden_classes:
        print(f"  Hidden(HLR): { {k: v for k, v in sorted(hidden_classes.items())} }")
    print(f"  Drawn      : {n_cut} section walls, {n_view} view walls "
          f"({n_viewlines} view polylines), {n_fp} footprints, "
          f"{n_sym} plan symbols, {n_wire} HLR wireframes")

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
               drawing_scale=human_scale,
               footprint_polys=footprint_polys or None,
               direct_entities=direct_entities or None)
    print(f"  DXF gen    : {time.perf_counter() - t1:.2f}s")
    size_kb = os.path.getsize(output_path) // 1024
    print(f"  >> {output_path}  ({size_kb} KB)")
    return output_path
