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

Routing is **data- and output-driven, no class whitelist** (v3):

- **visibility oracle**: an element with no output at all (loops *or*
  segments) is hidden -> skipped. Replaces any slab-occlusion heuristic, with
  exact void / double-height behavior, because it *is* the same HLR result
  Bonsai renders.
- **priority 1 — authored 2D representation** (`_has_2d_plan_repr`): elements
  whose IFC data carries a genuinely 2D representation for this view (Plan
  context, or FootPrint/Axis) draw as shared BLOCK/INSERT plan symbols
  (``plan_symbols.place_plan_symbol``), gated by the oracle — a partially
  visible symbol INSERTs its full block (normal CAD convention). This is
  Bonsai's own rule: Plan-context representations win over Body/HLR. The
  lookup table's Model/Body/MODEL_VIEW fallback rows deliberately do NOT
  qualify — they match any 3D body (walls included), and HLR output is the
  occlusion-correct replacement for that old crease-edge fallback.
- **priority 2 — output-driven section/view**: whatever HLR says, per element:
  section loops -> polygons in the ``section`` role (fused + hatched by the
  shared writer — walls, columns, but equally cut slabs, beams, stairs,
  building element parts, proxies); closed ``projection`` loops minus the
  section area -> ``view`` polygons; open segments -> chained polylines, also
  ``view`` (HLR already clipped their hidden parts: correct *partial*
  occlusion). Everything lands on the element's class layer; the role is
  written as an explicit entity style (see ``core/layers.py``).
- **view profile** (`_VIEW_PROFILE`, styling only): in plan-family views the
  *viewed* closed loops of slabs/coverings/roofs become footprint LWPOLYLINE
  GROUPs instead of ``view`` polygons. Non-fabric elements (``_is_hatchable``:
  spatial elements, furnishing…) are never hatched — their cut loops draw as
  unhatched outlines in the ``view`` role.

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
    camera_dir_pos,
    world_matrix_col_major,
    camera_body_local_extents,
)
from ..ifc_query import (
    get_elements,
    get_material_key,
    find_plan_repr,
    _get_drawing_annotations,
)
from ..dxf_template import _parse_scale_factor
from ..dxf_writer import _write_dxf
from ..plan_symbols import place_plan_symbol, _chain_segments
from ..geometry import (
    _wall_z_range,
    _extract_wall_polygon_with_openings,
    _slab_footprint_world,
)


_SVG_NS = "{http://www.w3.org/2000/svg}"
_IFC_NS = "{http://www.ifcopenshell.org/ns}"

# View profile: drawing-convention decisions per TargetView. The geometric
# routing itself is output-driven (HLR says per element whether it was cut or
# viewed; no class whitelist) -- these are pure styling rules on the result.
# "footprint_viewed": these classes' viewed shape is recomposed from authored
# footprints via the painter's algorithm (_recompose_plan_footprints) into
# footprint LWPOLYLINE GROUPs on the plain class layer -- single source, no
# HLR fragments or coplanar z-fight losses.
# "no_hatch": building fabric that is conventionally NOT hatched in this view
# even when cut -- stairs/ramps/railings in plans (a railing crosses the cut
# plane in virtually every plan). In future SECTION_VIEW/ELEVATION_VIEW
# profiles these classes hatch normally (a cut concrete flight is hatched).
_PLAN_NO_HATCH = frozenset({
    "IfcStair", "IfcStairFlight", "IfcRamp", "IfcRampFlight", "IfcRailing",
})
_VIEW_PROFILE = {
    "PLAN_VIEW": {
        "footprint_viewed": frozenset({"IfcSlab", "IfcCovering", "IfcRoof"}),
        "no_hatch": _PLAN_NO_HATCH,
    },
    "REFLECTED_PLAN_VIEW": {
        "footprint_viewed": frozenset({"IfcSlab", "IfcCovering", "IfcRoof"}),
        "no_hatch": _PLAN_NO_HATCH,
    },
}

# Universal hatch gate, driven by the IFC schema's own fabric-vs-contents
# taxonomy: only building fabric ever receives a section hatch. IfcFurniture
# (IfcFurnishingElement branch), IfcSanitaryTerminal (IfcDistributionElement
# branch), appliances, transport and spatial elements all fail the test
# automatically -- cut through or not, they draw as outlines.
# IfcBuiltElement is the IFC4X3 rename of IfcBuildingElement. The last three
# sit outside that branch and are listed because the drafting convention, not
# the schema, decides: IfcBuildingElementPart so cut wall layers hatch
# individually, ground and site because a cut through terrain is hatched.
_HATCHABLE_CLASSES = (
    "IfcBuildingElement",
    "IfcBuiltElement",
    "IfcBuildingElementPart",
    "IfcGeographicElement",
    "IfcSite",
)


# Room volumes are analytical space, not fabric. Written into the HLR scene
# they behave as opaque solids and hide everything they contain -- verified on
# a real plan (2T-Fontane, drawing 0GBxUdA8nE5BbFy9k81SHQ): 8 IfcSpatialZone
# hid 59 of 84 furniture and all 13 sanitary terminals. So they take no part in
# the visibility pass at all, neither occluding nor occluded: they are drawn
# straight from their authored footprint as a plain boundary polyline, on a
# no-plot layer. IfcSite stays in the pass -- its solid is real terrain.
# (Bonsai's get_drawing_elements drops IfcSpace but not IfcSpatialZone.)
_SPACE_VOLUME_CLASSES = ("IfcSpace", "IfcSpatialZone")


def _is_space_volume(element):
    for name in _SPACE_VOLUME_CLASSES:
        try:
            if element.is_a(name):
                return True
        except Exception:
            continue
    return False


def _projected_mesh_outline(element, cam_inv_np):
    """Camera-space outline of an element's whole body, from its mesh.

    Fallback for bodies whose authored footprint cannot be read (BReps,
    IfcPolygonalFaceSet, unsupported profiles): project every triangle and take
    their union, which is the silhouette for a prismatic volume.
    """
    try:
        s = ifcopenshell.geom.settings()
        s.set("use-world-coords", True)
        shape = ifcopenshell.geom.create_shape(s, element)
        verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
        faces = np.array(shape.geometry.faces, dtype=int).reshape(-1, 3)
    except Exception:
        return None
    if not len(verts) or not len(faces):
        return None
    pts = (cam_inv_np @ np.hstack([verts, np.ones((len(verts), 1))]).T).T[:, :2]
    tris = []
    for a, b, c in faces:
        tri = shapely.Polygon([tuple(pts[a]), tuple(pts[b]), tuple(pts[c])])
        if tri.is_valid and tri.area > 1e-9:
            tris.append(tri)
    if not tris:
        return None
    try:
        merged = shapely.unary_union(tris)
    except Exception:
        return None
    return None if merged.is_empty else merged


def _space_boundary_entries(elements, cam_inv_np):
    """Each space/zone as a closed boundary polyline in camera space.

    Authored footprint first (exact); mesh silhouette when that fails.
    Returns footprint_polys entries: [(gid, ifc_class, layer, exterior, holes)].
    """
    entries = []
    for element in elements:
        wm = world_matrix_col_major(element)
        z_elem = float(wm[14])
        try:
            fp_world = _slab_footprint_world(element, wm)
        except Exception:
            fp_world = None

        if fp_world is not None and not fp_world.is_empty:
            def _proj(ring):
                return [(float(c[0]), float(c[1])) for c in
                        (cam_inv_np @ np.array([wx, wy, z_elem, 1.0])
                         for wx, wy in ring.coords[:-1])]
            polys = [(_proj(fp_world.exterior),
                      [_proj(r) for r in fp_world.interiors])]
        else:
            merged = _projected_mesh_outline(element, cam_inv_np)
            if merged is None:
                continue
            geoms = (merged.geoms if hasattr(merged, "geoms") else [merged])
            polys = [([(float(x), float(y)) for x, y in g.exterior.coords[:-1]],
                      [[(float(x), float(y)) for x, y in r.coords[:-1]]
                       for r in g.interiors])
                     for g in geoms if isinstance(g, shapely.Polygon)]

        for exterior, holes in polys:
            if len(exterior) >= 3:
                entries.append((element.GlobalId, element.is_a(),
                                element.is_a(), exterior, holes))
    return entries


def _is_hatchable(element):
    for name in _HATCHABLE_CLASSES:
        try:
            if element.is_a(name):
                return True
        except Exception:
            continue
    return False


def _has_2d_plan_repr(element, target_view):
    """True when the element (or its type) carries a genuinely 2D
    representation for this view: a "Plan" ContextType, or a FootPrint/Axis
    identifier. This gates the BLOCK/INSERT symbol path.

    Deliberately stricter than find_plan_repr alone: the search table's
    Model/Body/MODEL_VIEW fallback rows match any element's 3D body (walls
    included), and building a "symbol" from a 3D body is exactly what the HLR
    output supersedes -- HLR linework is occlusion-correct, the crease-edge
    fallback is not.
    """
    plan_repr, _from_type = find_plan_repr(element, target_view)
    if plan_repr is None:
        return False
    ctx = plan_repr.ContextOfItems
    return (getattr(ctx, "ContextType", None) == "Plan"
            or getattr(ctx, "ContextIdentifier", None) in ("FootPrint", "Axis"))

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


# SVG edge classification (IfcOpenShell PR #8608, issue #3668): the serializer
# classifies projection edges pre-HLR from real face topology and writes the
# class on each <path> (not on the <g>, so Bonsai's linework merge can't clobber
# it). Which classes reach the output is the serializer's call, driven by the
# drawing's settings — we never re-filter its linework.
_EDGE_CLASSES = frozenset({"boundary", "outline", "sharp", "crease", "flush"})

# The six settings are per-drawing in Bonsai: stored in EPset_Drawing, read into
# the camera props by tool.Drawing.import_camera_props, applied in
# setup_serialiser. We read the same pset so a drawing exports to DXF and to SVG
# with identical linework. Defaults mirror Bonsai's property defaults, used
# whenever a key is absent from the pset.
_EDGE_SETTINGS = (
    # (serializer setting, EPset_Drawing property, Bonsai default)
    ("svg-use-edge-classification", "UseEdgeClassification", False),
    ("svg-render-crease-edges", "RenderCreases", True),
    ("svg-valley-angle-min-degrees", "ValleyAngleMinDegrees", 12.0),
    ("svg-render-sharp-edges", "RenderSharp", True),
    ("svg-ridge-angle-min-degrees", "RidgeAngleMinDegrees", 45.0),
    ("svg-emit-flush-edges", "RenderFlush", False),
)


def _edge_classification_settings(pset):
    """The six #3668 serializer settings for a drawing, read from its
    EPset_Drawing with Bonsai's property defaults as fallback."""
    values = {}
    for key, prop, default in _EDGE_SETTINGS:
        raw = (pset or {}).get(prop)
        if raw is None:
            values[key] = default
        elif isinstance(default, bool):
            values[key] = bool(raw)
        else:
            values[key] = float(raw)
    return values


def _setup_serialiser(ifc, drawing, scale_factor_val, target_view,
                      edge_settings=None):
    """Build the native SVG/HLR serializer configured exactly like Bonsai's
    setup_serialiser (bonsai/bim/module/drawing/operator.py) — same settings,
    same order, so DXF and SVG exports run the identical engine configuration.

    Returns (gs, buf, serialiser, edge_classification); the flag is False when
    the drawing opts out or the ifcopenshell build predates the #3668 settings,
    in which case the HLR linework arrives unclassified and unfiltered.
    """
    if edge_settings is None:
        edge_settings = _edge_classification_settings(None)
    gs = ifcopenshell.geom.settings()
    gs.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)
    gs.set("iterator-output", ifcopenshell.ifcopenshell_wrapper.NATIVE)
    # Read by the serializer's constructor, so it must be set on gs first.
    edge_classification = bool(edge_settings["svg-use-edge-classification"])
    try:
        for key, _prop, _default in _EDGE_SETTINGS:
            gs.set(key, edge_settings[key])
    except Exception:
        edge_classification = False  # ifcopenshell build without #3668
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
    return gs, buf, serialiser, edge_classification


# Closed-loop test in svg units (paper mm): serializer loops repeat their first
# point exactly, so a tight tolerance is safe.
_CLOSE_TOL = 1e-6


def _parse_hlr_svg(svg_text, x_min_l, y_max_l, factor):
    """Parse the serializer's SVG into per-guid camera-space records.

    Returns {guid: {"section": [Polygon], "projection": [Polygon],
    "lines": [(pts, edge_class)]}}. `edge_class` is the #3668 class carried by
    the individual <path> (None when the build/setting emits none); a single
    'd' may hold several subpaths, all sharing their path's class.
    """
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
            edge_class = next((c for c in (path.get("class") or "").split()
                               if c in _EDGE_CLASSES), None)
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
                    rec["lines"].append((pts, edge_class))
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
    return out


def _count_edge_classes(hlr_out):
    """Per-class counts of the open linework received (log / validation aid)."""
    counts = {}
    for rec in hlr_out.values():
        for _pts, cls in rec["lines"]:
            key = cls or "unclassified"
            counts[key] = counts.get(key, 0) + 1
    return counts


# Matches the writer's SNAP_TOL: section groups are fused with a 0.5 mm miter
# snap, so what the hatch finally covers is the raw union grown by that much.
# 0.5 mm in model metres is 0.005 mm on paper at 1:100 -- nothing drawable is
# at risk.
_SECTION_COVER_TOL = 5e-4


def _drop_lines_covered_by_section(lines, section_polys):
    """Drop the open segments that merely retrace the element's own cut.

    A wall crossing the cut plane reports its section loop *and* a projection
    of the same prism, so the two describe one rectangle: the section outline
    is drawn fused and hatched, the segments would double it unfused. Segments
    outside the cut area (a wall step or footing visible below it) stay.
    """
    if not lines or not section_polys:
        return lines, 0
    try:
        area = shapely.unary_union(section_polys).buffer(_SECTION_COVER_TOL)
    except Exception:
        return lines, 0
    kept = []
    for pts, cls in lines:
        try:
            if area.covers(shapely.LineString(pts)):
                continue
        except Exception:
            pass
        kept.append((pts, cls))
    return kept, len(lines) - len(kept)


def _group_lines_by_class(lines):
    """Split [(pts, edge_class)] into {edge_class: [pts]} — chaining must not
    merge segments of different classes into one polyline."""
    groups = {}
    for pts, cls in lines:
        groups.setdefault(cls, []).append(pts)
    return groups


def _run_hlr_scene(ifc, drawing, elements, scale_factor_val, target_view,
                   edge_settings=None):
    """Write the whole element set + the camera element into one serializer
    run and parse the per-guid output.

    Returns (out, n_written, t_hlr, edge_classification), out as documented on
    _parse_hlr_svg. An element absent from `out` produced no visible output: it
    is fully hidden by the global HLR pass (or has no geometry) and must not be
    drawn.

    Space volumes never enter the pass -- see _is_space_volume.
    """
    gs, buf, serialiser, edge_classification = _setup_serialiser(
        ifc, drawing, scale_factor_val, target_view, edge_settings)

    t0 = time.perf_counter()
    n_written = 0
    include = [e for e in elements if not _is_space_volume(e)] + [drawing]
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

    out = _parse_hlr_svg(svg_text, x_min_l, y_max_l, factor)
    return out, n_written, t_hlr, edge_classification


# Painter's-algorithm tie-breaker for coplanar horizontal build-up: at equal
# z_top the finish paints over the structure (drafting convention), roofs over
# everything.
_FOOTPRINT_PRIORITY = {"IfcRoof": 3, "IfcCovering": 2, "IfcSlab": 1}


def _recompose_plan_footprints(candidates, cam_inv_np, sections_union):
    """Plan-profile footprint recomposition: authored geometry as the single
    source of shape, painter's algorithm as the visibility rule.

    HLR z-fights on coplanar horizontal surfaces (a flooring covering flush
    with its slab suppresses BOTH elements' boundary edges) and fragments the
    rest into segments. For slabs/coverings/roofs in a plan the visibility
    problem is really 2.5D, so it is solved exactly here instead: each
    element's authored footprint (`_slab_footprint_world`, extrusion profile
    through booleans) minus every footprint above it (z_top descending,
    `_FOOTPRINT_PRIORITY` breaks coplanar ties) minus the section hatch areas.
    One clean closed outline per element, no duplicate fragments.

    candidates: [(element, gid, rec)] -- oracle-visible footprint-class
    elements. Returns (footprint_entries, fallback) where footprint_entries
    feed _write_dxf's footprint_polys and fallback lists the candidates whose
    authored footprint could not be extracted (BReps...) -- the caller draws
    those from their HLR output as before.
    """
    prepared = []
    fallback = []
    for element, gid, rec in candidates:
        wm = world_matrix_col_major(element)
        try:
            fp_world = _slab_footprint_world(element, wm)
        except Exception:
            fp_world = None
        if fp_world is None or fp_world.is_empty:
            fallback.append((element, gid, rec))
            continue
        z_elem = float(wm[14])
        def _proj(ring):
            pts = []
            for wx, wy in ring.coords[:-1]:
                c = cam_inv_np @ np.array([wx, wy, z_elem, 1.0])
                pts.append((float(c[0]), float(c[1])))
            return pts
        try:
            fp_cam = shapely.Polygon(_proj(fp_world.exterior),
                                     [_proj(r) for r in fp_world.interiors])
            if not fp_cam.is_valid:
                fp_cam = fp_cam.buffer(0)
        except Exception:
            fallback.append((element, gid, rec))
            continue
        if fp_cam.is_empty:
            fallback.append((element, gid, rec))
            continue
        _, z_top = _wall_z_range(element, wm)
        prio = _FOOTPRINT_PRIORITY.get(element.is_a(), 0)
        prepared.append((z_top if z_top is not None else z_elem, prio, gid,
                         element.is_a(), fp_cam))

    entries = []
    covered = None  # union of footprints already painted (they lie above)
    for z_top, prio, gid, cls, fp in sorted(
            prepared, key=lambda t: (t[0], t[1], t[2]), reverse=True):
        region = fp
        try:
            if covered is not None:
                region = region.difference(covered)
            if sections_union is not None:
                region = region.difference(sections_union)
            covered = fp if covered is None else covered.union(fp)
        except Exception:
            pass
        if region.is_empty:
            continue
        geoms = region.geoms if hasattr(region, "geoms") else [region]
        for g in geoms:
            if not isinstance(g, shapely.Polygon) or g.area < 1e-6:
                continue
            ext = [(float(x), float(y)) for x, y in g.exterior.coords[:-1]]
            holes = [[(float(x), float(y)) for x, y in r.coords[:-1]]
                     for r in g.interiors]
            if len(ext) >= 3:
                # (gid, ifc_class, layer, ...): footprints draw on the class layer
                entries.append((gid, cls, cls, ext, holes))
    return entries, fallback


# Linework cleaning thresholds, in PAPER millimetres (converted to model units
# via the drawing scale). Both sit strictly below plot resolution (~0.13 mm
# finest pen), so nothing a plotter could render is ever removed -- when in
# doubt, geometry is kept.
_SIMPLIFY_PAPER_MM = 0.05   # Douglas-Peucker vertex tolerance
_CULL_PAPER_MM     = 0.15   # min bbox diagonal for a whole chained polyline


def _chain_lines(lines, scale_factor=None, seen_segments=None):
    """Chain open visible-edge polylines into longer runs by shared endpoints,
    then clean them below plot resolution.

    setSegmentProjection emits viewed geometry as individual 2-point paths;
    exploding into segments and rechaining (plan_symbols._chain_segments)
    merges e.g. a viewed wall's four edges back into one closed outline.

    seen_segments (a per-layer set, owned by the caller) deduplicates exact
    segments: HLR emits an edge once per element that shows it, so an edge
    shared by two adjacent walls (or by an element's own section and
    projection groups) would otherwise be drawn twice -- BricsCAD's OVERKILL
    flags those. Dedup is same-layer only; cross-layer duplicates are kept so
    switching a layer off never opens gaps in another.

    When scale_factor is given, two conservative cleaning passes run on the
    chained result (dense mesh tessellation produces facet crumbs): whole
    polylines smaller than _CULL_PAPER_MM on paper are dropped, and vertices
    within _SIMPLIFY_PAPER_MM of the simplified line are removed. Both
    thresholds are sub-plot-resolution, so no renderable geometry is lost.
    """
    segs = []
    for pts in lines:
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if a == b:
                continue
            if seen_segments is not None:
                ka = (round(a[0], 6), round(a[1], 6))
                kb = (round(b[0], 6), round(b[1], 6))
                key = (ka, kb) if ka <= kb else (kb, ka)
                if key in seen_segments:
                    continue
                seen_segments.add(key)
            segs.append((a, b))
    polylines = _chain_segments(segs)

    if not scale_factor:
        return polylines
    to_model  = 0.001 / scale_factor          # paper mm -> model metres
    cull_diag = _CULL_PAPER_MM * to_model
    simp_tol  = _SIMPLIFY_PAPER_MM * to_model
    cleaned = []
    for pts in polylines:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        dx, dy = max(xs) - min(xs), max(ys) - min(ys)
        if (dx * dx + dy * dy) ** 0.5 < cull_diag:
            continue
        if len(pts) > 2:
            try:
                simp = shapely.LineString(pts).simplify(simp_tol)
                pts = [(float(x), float(y)) for x, y in simp.coords]
            except Exception:
                pass
        cleaned.append(pts)
    return cleaned


def export_drawing(ifc, drawing, pset, output_path, template_path=None,
                   crease_angle_deg=15.0, fuse_by_material=False):
    """Export a single Bonsai drawing to DXF using the accurate (global HLR
    oracle) pipeline.

    fuse_by_material: keep touching walls of different materials as separate
    outlines. Off by default -- fabric that touches reads as one solid, which
    is what a plan normally shows.
    """
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

    edge_settings = _edge_classification_settings(pset)
    hlr_out, n_written, t_hlr, edge_classification = _run_hlr_scene(
        ifc, drawing, elements, scale_factor_val, target_view, edge_settings
    )
    print(f"  HLR scene  : {t_hlr:.2f}s  ({n_written} elements written, "
          f"{len(hlr_out)} with visible output — global occlusion)")

    col_major   = camera_matrix_inv_col_major(drawing)
    _cam_inv_np = np.array(col_major, dtype=float).reshape(4, 4, order='F')
    _cam_R      = _cam_inv_np[:3, :3]
    _cam_x_proj = _cam_R @ np.array([1.0, 0.0, 0.0])
    _cam_rot_deg = float(np.degrees(np.arctan2(float(_cam_x_proj[1]), float(_cam_x_proj[0]))))

    cam_dir, _cam_pos = camera_dir_pos(drawing)
    # Linework filtering belongs to the serializer, driven by the drawing's own
    # EPset_Drawing settings: whatever it emits is what the drawing asked for.
    counts = _count_edge_classes(hlr_out)
    if edge_classification:
        print(f"  Edge class : serializer-side (#3668), ridge >= "
              f"{edge_settings['svg-ridge-angle-min-degrees']:.0f}° valley >= "
              f"{edge_settings['svg-valley-angle-min-degrees']:.0f}°, flush "
              f"{'kept' if edge_settings['svg-emit-flush-edges'] else 'dropped'} — "
              f"segments { {k: v for k, v in sorted(counts.items())} }")
    else:
        why = ("drawing opts out (EPset_Drawing.UseEdgeClassification)"
               if not edge_settings["svg-use-edge-classification"]
               else "ifcopenshell build predates #3668")
        print(f"  Edge class : off — {why}; unfiltered HLR linework "
              f"({sum(counts.values())} segments)")

    wall_polys_by_key = {}  # key -> [(Polygon, gid), ...]
    footprint_polys   = []  # [(gid, ifc_class, layer, exterior_pts, [hole_pts])]
    block_defs    = {}
    block_order   = []
    block_inserts = {}
    seen_blocks   = {}
    direct_entities = []  # [{layer, gid, ifc_class, polylines, arcs, circles, ellipses}]

    profile = _VIEW_PROFILE.get(target_view, {})
    footprint_viewed = profile.get("footprint_viewed", frozenset())
    no_hatch = profile.get("no_hatch", frozenset())

    n_hidden = n_cut = n_view = n_fp = n_sym = n_viewlines = 0
    n_cut_dup = 0  # view segments retracing their own element's cut
    hidden_classes = {}
    cut_classes = {}
    seen_layer_segments = {}  # (layer, role) -> normalized segment keys (dedup)
    footprint_candidates = []  # oracle-visible slab/covering/roof, recomposed post-loop

    # Spaces and zones take no part in the visibility pass: plain boundary
    # polyline from the authored footprint, on their own no-plot layer.
    space_volumes = [e for e in elements if _is_space_volume(e)]
    if space_volumes:
        entries = _space_boundary_entries(space_volumes, _cam_inv_np)
        footprint_polys.extend(entries)
        print(f"  Spaces     : {len({e[0] for e in entries})}/{len(space_volumes)} "
              f"outlined ({len(entries)} polylines, no-plot layer, excluded "
              f"from the HLR pass)")

    # Sorted iteration keeps block_order (and thus the DXF) deterministic.
    for element in sorted(elements, key=lambda e: getattr(e, "GlobalId", "")):
        ifc_class = element.is_a()
        gid = getattr(element, "GlobalId", None)
        if _is_space_volume(element):
            continue
        rec = hlr_out.get(gid)

        # --- Visibility oracle: no HLR output at all -> hidden (or no geom).
        # Open segments count as output: viewed geometry arrives as segments
        # (setSegmentProjection), not closed loops.
        if rec is None or not (rec["section"] or rec["projection"] or rec["lines"]):
            n_hidden += 1
            hidden_classes[ifc_class] = hidden_classes.get(ifc_class, 0) + 1
            continue

        sec, proj, lines = rec["section"], rec["projection"], rec["lines"]

        # --- Priority 1: authored 2D representation for this view -> shared
        # BLOCK/INSERT plan symbol (Bonsai's own rule: Plan-context reprs win
        # over Body/HLR). Gated by _has_2d_plan_repr so the search table's
        # Model/Body/MODEL_VIEW fallback never turns a 3D body into a symbol.
        if _has_2d_plan_repr(element, target_view):
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
                continue

        # --- Priority 2: output-driven. HLR already said whether this element
        # is cut (section loops) or viewed (projection loops / open segments);
        # no class whitelist. Hatching is gated by the schema (building fabric
        # only, _is_hatchable) and the view profile's no_hatch conventions.
        material = get_material_key(element) if fuse_by_material else None
        hatchable = _is_hatchable(element) and ifc_class not in no_hatch

        if sec and hatchable:
            key = (ifc_class, material, "section", None)
            wall_polys_by_key.setdefault(key, []).extend((p, gid) for p in sec)
            n_cut += 1
            cut_classes[ifc_class] = cut_classes.get(ifc_class, 0) + 1

        # Horizontal build-up: the viewed shape is recomposed post-loop from
        # authored footprints (painter's algorithm) -- single source, no HLR
        # fragments/z-fight losses. The cut part (above) is hatched normally.
        if ifc_class in footprint_viewed:
            footprint_candidates.append((element, gid, rec))
            continue

        # Closed projection loops beyond the section area = visible viewed
        # parts (or the whole element, if not cut at all).
        view_polys = proj
        if sec and proj and hatchable:
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
        if not hatchable:
            view_polys = sec + view_polys  # cut loops drawn as unhatched outlines

        if view_polys:
            wm = world_matrix_col_major(element)
            _, z_max = _wall_z_range(element, wm)
            z_top_key = round(z_max, 3) if z_max is not None else None
            key = (ifc_class, material, "view", z_top_key)
            wall_polys_by_key.setdefault(key, []).extend((p, gid) for p in view_polys)

        # Open visible-edge segments (the common form for viewed geometry):
        # HLR already clipped the hidden parts, draw them as-is.
        if lines and sec:
            lines, n_dropped = _drop_lines_covered_by_section(lines, sec)
            n_cut_dup += n_dropped
        if lines:
            # Dedup is per (layer, role): section and view share a layer now, and
            # a segment drawn in both roles must survive in both.
            seen = seen_layer_segments.setdefault((ifc_class, "view"), set())
            for edge_class, cls_lines in _group_lines_by_class(lines).items():
                chained = _chain_lines(cls_lines, scale_factor_val, seen)
                if chained:
                    direct_entities.append({"layer": ifc_class, "role": "view",
                                            "gid": gid,
                                            "ifc_class": ifc_class,
                                            "edge_class": edge_class,
                                            "from_hlr": True,
                                            "polylines": chained,
                                            "arcs": [], "circles": [], "ellipses": []})
                    n_viewlines += len(chained)

        if (view_polys or lines) and not sec:
            n_view += 1

    # Every hatched cut area of the drawing: what these polygons cover is
    # filled on paper, so linework inside them cannot be seen.
    sec_polys = [p for (_c, _m, role, _z), ps in wall_polys_by_key.items()
                 if role == "section" for p, _g in ps]
    try:
        sections_union = shapely.unary_union(sec_polys) if sec_polys else None
    except Exception:
        sections_union = None

    # Viewed outlines buried under another element's cut (a below-cut wall part
    # under the neighbour it joins): the element's own cut was already
    # subtracted above, this drops what other cuts hide.
    if sections_union is not None:
        buried = sections_union.buffer(_SECTION_COVER_TOL)
        for key, polys in list(wall_polys_by_key.items()):
            if key[2] != "view":
                continue
            kept = [(p, g) for p, g in polys if not buried.covers(p)]
            n_cut_dup += len(polys) - len(kept)
            if kept:
                wall_polys_by_key[key] = kept
            else:
                del wall_polys_by_key[key]

    # --- Plan-profile footprint recomposition (single-source, painter's
    # algorithm; see _recompose_plan_footprints). Elements whose authored
    # footprint cannot be extracted fall back to their HLR output.
    if footprint_candidates:
        entries, fp_fallback = _recompose_plan_footprints(
            footprint_candidates, _cam_inv_np, sections_union)
        footprint_polys.extend(entries)
        n_fp = len({e[0] for e in entries})
        for element, gid, rec in fp_fallback:
            ifc_class = element.is_a()
            for poly in rec["projection"]:
                ext_pts = [(float(x), float(y)) for x, y in poly.exterior.coords[:-1]]
                holes = [[(float(x), float(y)) for x, y in ring.coords[:-1]]
                         for ring in poly.interiors]
                if len(ext_pts) >= 3:
                    footprint_polys.append((gid, ifc_class, ifc_class, ext_pts, holes))
            seen = seen_layer_segments.setdefault((ifc_class, "view"), set())
            for edge_class, cls_lines in _group_lines_by_class(rec["lines"]).items():
                chained = _chain_lines(cls_lines, scale_factor_val, seen)
                if chained:
                    direct_entities.append({"layer": ifc_class, "role": "view",
                                            "gid": gid,
                                            "ifc_class": ifc_class,
                                            "edge_class": edge_class,
                                            "from_hlr": True,
                                            "polylines": chained,
                                            "arcs": [], "circles": [], "ellipses": []})
                    n_viewlines += len(chained)
            n_fp += 1
        if fp_fallback:
            print(f"  Footprints : {len(fp_fallback)} element(s) without extractable "
                  f"authored footprint kept their HLR output")

    # HLR output buried under another element's hatched cut: invisible on
    # paper, so it is dropped. Only HLR output (`from_hlr`) is filtered --
    # authored 2D symbols keep every stroke, a door leaf legitimately runs
    # inside the wall it sits in.
    if sections_union is not None and footprint_polys:
        buried = sections_union.buffer(_SECTION_COVER_TOL)

        def _is_buried(ring):
            try:
                return len(ring) >= 3 and buried.covers(shapely.Polygon(ring))
            except Exception:
                return False

        kept_fp = []
        for gid, cls, layer, exterior, holes in footprint_polys:
            if _is_buried(exterior):
                n_cut_dup += 1
                continue
            # A hole punched by a cut is already outlined by that cut; a hole
            # that is a real void (a stairwell) keeps its ring.
            kept_holes = [h for h in holes if not _is_buried(h)]
            n_cut_dup += len(holes) - len(kept_holes)
            kept_fp.append((gid, cls, layer, exterior, kept_holes))
        footprint_polys = kept_fp

    if sections_union is not None and direct_entities:
        buried = sections_union.buffer(_SECTION_COVER_TOL)
        for ent in direct_entities:
            if not ent.get("from_hlr") or not ent["polylines"]:
                continue
            kept = []
            for pts in ent["polylines"]:
                try:
                    if len(pts) > 1 and buried.covers(shapely.LineString(pts)):
                        continue
                except Exception:
                    pass
                kept.append(pts)
            n_cut_dup += len(ent["polylines"]) - len(kept)
            n_viewlines -= len(ent["polylines"]) - len(kept)
            ent["polylines"] = kept
        direct_entities = [e for e in direct_entities
                           if e["polylines"] or e["arcs"] or e["circles"]
                           or e["ellipses"]]

    print(f"  Hidden(HLR): {n_hidden} elements fully occluded or without geometry")
    if hidden_classes:
        print(f"  Hidden(HLR): { {k: v for k, v in sorted(hidden_classes.items())} }")
    print(f"  Drawn      : {n_cut} sectioned, {n_view} viewed "
          f"({n_viewlines} view polylines, {n_cut_dup} dropped as buried in a "
          f"hatched cut), {n_fp} footprints, {n_sym} plan symbols")
    if cut_classes:
        print(f"  Sectioned  : { {k: v for k, v in sorted(cut_classes.items())} }")

    # Authored-geometry patch union: HLR occasionally loses thin miter wedges
    # at wall joints (coincident clip planes between connected walls), leaving
    # pinhole voids in the fused section hatch that no element's output
    # covers. The authored profiles (extrusion + boolean clips -- the same
    # machinery as the approximate pipeline) act as the correctness oracle:
    # the writer fills fused-group holes that this union says are solid and
    # that no other group draws. Genuine voids (shafts, cavities) stay.
    patch_polys = []
    for element in elements:
        rec = hlr_out.get(getattr(element, "GlobalId", None))
        if not rec or not rec["section"] or not _is_hatchable(element):
            continue
        try:
            poly, _ = _extract_wall_polygon_with_openings(
                element, world_matrix_col_major(element), col_major,
                _cam_pos[2], cam_dir)
        except Exception:
            poly = None
        if poly is not None and not poly.is_empty:
            patch_polys.append(poly)
    section_patch_union = shapely.unary_union(patch_polys) if patch_polys else None

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
               direct_entities=direct_entities or None,
               section_patch_union=section_patch_union)
    print(f"  DXF gen    : {time.perf_counter() - t1:.2f}s")
    size_kb = os.path.getsize(output_path) // 1024
    print(f"  >> {output_path}  ({size_kb} KB)")
    return output_path
