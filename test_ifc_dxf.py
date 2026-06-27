#!/usr/bin/env python3
"""
Standalone test: IFC → DXF via ifc_dxf Rust engine (no Blender / Bonsai).

Usage:
    python test_ifc_dxf.py                      # export drawing 0
    python test_ifc_dxf.py --drawing 1          # export drawing 1
    python test_ifc_dxf.py --list               # list available drawings
    python test_ifc_dxf.py --ifc path/to/file.ifc
    python test_ifc_dxf.py --out /tmp/out.dxf

Requires:
    • ifcopenshell installed in the Python running this script
    • ifc_dxf.pyd built and present in bonsai_salad/ifc_dxf/
      (run: python build_ifc_dxf.py from bonsai_salad/)

Optional (for PNG preview):
    • ezdxf  and  matplotlib  installed  (pip install ezdxf matplotlib)
"""

import sys
import os
import json
import argparse

import numpy as np

try:
    import shapely
    import shapely.ops
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

# ── locate ifc_dxf.pyd ───────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "ifc_dxf"))

import ifc_dxf
import ifcopenshell
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.selector
import ifcopenshell.geom

# ── default IFC path ─────────────────────────────────────────────────────────
DEFAULT_IFC = (
    r"C:\120grammi Dropbox\120grammi_lavori"
    r"\26-TV-2T_Costruzioni-2026-Fontane\TV-2T-02a-PP"
    r"\2T-Fontane-Modello-Ipotesi_12.ifc"
)

# ── drawing discovery ────────────────────────────────────────────────────────

def find_drawings(ifc):
    """Return [(IfcAnnotation, pset_dict)] for every Bonsai drawing."""
    result = []
    for ann in ifc.by_type("IfcAnnotation"):
        pset = ifcopenshell.util.element.get_psets(ann).get("EPset_Drawing", {})
        if pset:
            result.append((ann, pset))
    return result


# ── camera helpers ───────────────────────────────────────────────────────────

def _placement_matrix(drawing):
    """Return the 4x4 numpy placement matrix for the drawing annotation."""
    return ifcopenshell.util.placement.get_local_placement(drawing.ObjectPlacement)


def camera_matrix_inv_col_major(drawing):
    """Column-major flat list of the inverse camera world matrix.

    Required by PyCameraProjection.  Column-major = Fortran order in numpy,
    matching glam DMat4::from_cols_array.
    """
    m = _placement_matrix(drawing)
    m_inv = np.linalg.inv(m)
    return list(m_inv.flatten(order="F"))


def camera_dir_pos(drawing):
    """(camera_dir, camera_pos) from IFC placement.

    The drawing plane normal = local +Z of the placement.
    The camera looks along local -Z (downward for PLAN_VIEW).
    """
    m = _placement_matrix(drawing)
    cam_dir = list(-m[:3, 2].astype(float))   # local -Z in world
    cam_pos = list( m[:3, 3].astype(float))   # origin in world
    return cam_dir, cam_pos


# ── camera frustum culling ────────────────────────────────────────────────────

def get_camera_frustum_bbox(drawing):
    """Return (x_min, x_max, y_min, y_max, z_min, z_max) in world coordinates
    from the drawing camera's body geometry (IfcCsgSolid / IfcExtrudedAreaSolid).

    ifcopenshell.geom does not apply ObjectPlacement for IfcAnnotation, so we
    read local coords and transform manually with the placement matrix.
    Returns None if the body geometry is unavailable.
    """
    try:
        s = ifcopenshell.geom.settings()
        s.set('use-world-coords', False)
        shape = ifcopenshell.geom.create_shape(s, drawing)
        v_local = np.array(shape.geometry.verts).reshape(-1, 3)
        m = ifcopenshell.util.placement.get_local_placement(drawing.ObjectPlacement)
        ones = np.ones((len(v_local), 1))
        v_world = (m @ np.hstack([v_local, ones]).T).T[:, :3]
        return (v_world[:, 0].min(), v_world[:, 0].max(),
                v_world[:, 1].min(), v_world[:, 1].max(),
                v_world[:, 2].min(), v_world[:, 2].max())
    except Exception:
        return None


def _element_origin(element):
    """Return (x, y, z) world origin of element's ObjectPlacement, or None."""
    try:
        placement = getattr(element, "ObjectPlacement", None)
        if placement is None:
            return None
        m = ifcopenshell.util.placement.get_local_placement(placement)
        return float(m[0, 3]), float(m[1, 3]), float(m[2, 3])
    except Exception:
        return None


def element_in_frustum(element, frustum):
    """Return True if element's ObjectPlacement origin is inside the frustum bbox.

    Uses element origin as proxy for full bounding box — fast and correct for
    point-like elements (furniture, doors). For large elements (walls, slabs)
    the ObjectPlacement is at the element's base, which is the discriminating
    coordinate for storey assignment.
    """
    if frustum is None:
        return True
    x_min, x_max, y_min, y_max, z_min, z_max = frustum
    pt = _element_origin(element)
    if pt is None:
        return True  # no placement → include (annotations, etc.)
    x, y, z = pt
    return (x_min <= x <= x_max and
            y_min <= y <= y_max and
            z_min <= z <= z_max)


# ── element retrieval ────────────────────────────────────────────────────────

def get_elements(ifc, drawing, pset):
    """Reproduce Bonsai's get_drawing_elements + get_elements_in_camera_view
    in pure ifcopenshell, without requiring a Blender camera object.

    Selection pipeline:
    1. Include/Exclude query filters (from EPset_Drawing pset)
    2. Spatial culling: element origin inside camera frustum bbox (from body geom)
    """
    include = pset.get("Include", None)
    exclude = pset.get("Exclude", None)

    if include:
        try:
            data = json.loads(include)
            query = (data.get("query") if isinstance(data, dict) else None)
            if query:
                elements = ifcopenshell.util.selector.filter_elements(ifc, query)
            else:
                elements = ifcopenshell.util.selector.filter_elements(ifc, include)
        except (json.JSONDecodeError, ValueError):
            elements = ifcopenshell.util.selector.filter_elements(ifc, include)
    else:
        if ifc.schema == "IFC2X3":
            elements = set(ifc.by_type("IfcElement") + ifc.by_type("IfcSpatialStructureElement"))
        else:
            elements = set(ifc.by_type("IfcElement") + ifc.by_type("IfcSpatialElement"))
        elements = {e for e in elements if e.is_a() != "IfcSpace"}

    if exclude:
        try:
            data = json.loads(exclude)
            query = (data.get("query") if isinstance(data, dict) else None)
            if query:
                elements -= ifcopenshell.util.selector.filter_elements(ifc, query)
            else:
                elements -= ifcopenshell.util.selector.filter_elements(ifc, exclude)
        except (json.JSONDecodeError, ValueError):
            elements -= ifcopenshell.util.selector.filter_elements(ifc, exclude)

    elements -= set(ifc.by_type("IfcOpeningElement"))
    elements  = {e for e in elements if not e.is_a("IfcAnnotation")}

    # Spatial culling from camera frustum (Blender-agnostic)
    frustum = get_camera_frustum_bbox(drawing)
    if frustum is not None:
        before = len(elements)
        elements = {e for e in elements if element_in_frustum(e, frustum)}
        print(f"  Frustum    : {before} -> {len(elements)} elements  "
              f"(Z {frustum[4]:.2f}..{frustum[5]:.2f})")

    return elements


# ── bucket B helpers ──────────────────────────────────────────────────────────

# Each entry: (ContextType, ContextIdentifier, TargetView).
# Order = priority: first match wins.
# Mirrors Bonsai SVG context selection for Body/Facetation tiers;
# adds FootPrint/Axis as fallback for elements without an OCC section.
_PLAN_SEARCH = {
    "PLAN_VIEW": [
        ("Plan",  "Body",        "PLAN_VIEW"),
        ("Plan",  "Body",        "MODEL_VIEW"),
        ("Model", "Body",        "PLAN_VIEW"),
        ("Model", "Body",        "MODEL_VIEW"),
        ("Model", "FootPrint",   "PLAN_VIEW"),
        ("Model", "Axis",        "PLAN_VIEW"),
        ("Plan",  "Facetation",  "PLAN_VIEW"),
        ("Plan",  "Facetation",  "MODEL_VIEW"),
        ("Model", "Facetation",  "PLAN_VIEW"),
        ("Model", "Facetation",  "MODEL_VIEW"),
    ],
    "REFLECTED_PLAN_VIEW": [
        ("Plan",  "Body",        "REFLECTED_PLAN_VIEW"),
        ("Plan",  "Body",        "MODEL_VIEW"),
        ("Model", "Body",        "REFLECTED_PLAN_VIEW"),
        ("Model", "Body",        "MODEL_VIEW"),
        ("Plan",  "Facetation",  "REFLECTED_PLAN_VIEW"),
        ("Plan",  "Facetation",  "MODEL_VIEW"),
        ("Model", "Facetation",  "REFLECTED_PLAN_VIEW"),
        ("Model", "Facetation",  "MODEL_VIEW"),
    ],
}


def _find_repr_in_representations(representations, target_view):
    """Search a list of IfcShapeRepresentation for the highest-priority context match."""
    search = _PLAN_SEARCH.get(target_view, [])
    for ctx_type, ctx_id, tv in search:
        for shape_repr in representations:
            ctx = shape_repr.ContextOfItems
            if (ctx.ContextType == ctx_type
                    and ctx.ContextIdentifier == ctx_id
                    and getattr(ctx, "TargetView", None) == tv):
                return shape_repr
    return None


def find_plan_repr(element, target_view):
    """Return IfcShapeRepresentation for this element's Plan context.

    Lookup order (mirrors IFC override semantics):
    1. Element's own Representation (override wins).
    2. Type's RepresentationMaps (inherited geometry).

    Returns (repr, from_type) where from_type=True means geometry is inherited.
    """
    # 1. Element override
    if hasattr(element, "Representation") and element.Representation is not None:
        r = _find_repr_in_representations(
            element.Representation.Representations, target_view
        )
        if r is not None:
            return r, False

    # 2. Type fallback
    ifc_type = ifcopenshell.util.element.get_type(element)
    if ifc_type is not None:
        rep_maps = getattr(ifc_type, "RepresentationMaps", None) or []
        type_reprs = [rm.MappedRepresentation for rm in rep_maps]
        r = _find_repr_in_representations(type_reprs, target_view)
        if r is not None:
            return r, True

    return None, False


def is_mapped_repr(plan_repr):
    """True if all items in the repr are IfcMappedItem (geometry from type)."""
    items = plan_repr.Items
    return bool(items) and all(item.is_a("IfcMappedItem") for item in items)


def get_type_block_name(element):
    """Return (type_entity, block_name) if element has a type, else (None, None)."""
    ifc_type = ifcopenshell.util.element.get_type(element)
    if ifc_type is None:
        return None, None
    name = getattr(ifc_type, "Name", None) or ifc_type.GlobalId
    return ifc_type, name


def get_material_name(element):
    try:
        mats = ifcopenshell.util.element.get_materials(element, should_inherit=True)
        if mats:
            return mats[0].Name or ""
    except Exception:
        pass
    return ""


def world_matrix_col_major(element):
    """Column-major flat list of the element world placement matrix."""
    try:
        placement = getattr(element, "ObjectPlacement", None)
        if placement is None:
            return list(np.eye(4).flatten(order="F"))
        m = ifcopenshell.util.placement.get_local_placement(placement)
        return list(m.flatten(order="F"))
    except Exception:
        return list(np.eye(4).flatten(order="F"))


def _arc_3pts_spec(p1, p2, p3):
    """Return (cx, cy, r, dxf_start_deg, dxf_end_deg) for an arc through 3 2D points.

    dxf_start/end are in degrees [0,360) for a DXF ARC entity that goes CCW from
    start to end.  Returns None if the three points are collinear.
    """
    import math
    ax, ay = float(p1[0]), float(p1[1])
    bx, by = float(p2[0]), float(p2[1])
    cx, cy = float(p3[0]), float(p3[1])
    D = 2 * (ax*(by-cy) + bx*(cy-ay) + cx*(ay-by))
    if abs(D) < 1e-12:
        return None  # collinear
    a2b = ax*ax+ay*ay; b2b = bx*bx+by*by; c2b = cx*cx+cy*cy
    ux = (a2b*(by-cy) + b2b*(cy-ay) + c2b*(ay-by)) / D
    uy = (a2b*(cx-bx) + b2b*(ax-cx) + c2b*(bx-ax)) / D
    r  = math.sqrt((ax-ux)**2 + (ay-uy)**2)
    a1_raw = math.degrees(math.atan2(ay-uy, ax-ux)) % 360
    a2_raw = math.degrees(math.atan2(cy-uy, cx-ux)) % 360
    am_raw = math.degrees(math.atan2(by-uy, bx-ux)) % 360
    # Is p2 on the CCW arc from p1 to p3?
    if a1_raw < a2_raw:
        ccw = a1_raw <= am_raw <= a2_raw
    else:
        ccw = am_raw >= a1_raw or am_raw <= a2_raw
    if ccw:
        return (ux, uy, r, a1_raw, a2_raw)
    else:
        # CW arc → swap so DXF draws the same arc CCW from p3 to p1
        return (ux, uy, r, a2_raw, a1_raw)


def _trimmed_conic_spec(item):
    """Return arc or circle spec for IfcTrimmedCurve with IfcCircle / IfcEllipse basis.

    Return value:
      (cx, cy, r, dxf_start, dxf_end) — arc spec  (DXF ARC goes CCW start→end)
      (cx, cy, r)                      — full circle spec
      None                             — IfcEllipse with unequal axes (caller tessellates)
    """
    import math
    basis = item.BasisCurve
    if basis.is_a("IfcCircle"):
        r = ra = rb = float(basis.Radius)
    elif basis.is_a("IfcEllipse"):
        ra = float(basis.SemiAxis1)
        rb = float(basis.SemiAxis2)
        if abs(ra - rb) > 0.001 * max(ra, rb):
            return None  # true ellipse — caller uses tessellation fallback
        r = (ra + rb) / 2
    else:
        return None

    pos = basis.Position
    loc = pos.Location.Coordinates
    cx, cy = float(loc[0]), float(loc[1])
    if hasattr(pos, 'RefDirection') and pos.RefDirection:
        d  = pos.RefDirection.DirectionRatios
        xn = math.sqrt(float(d[0])**2 + float(d[1])**2)
        xax = [float(d[0])/xn, float(d[1])/xn]
    else:
        xax = [1.0, 0.0]
    yax = [-xax[1], xax[0]]
    ref_ang_deg = math.degrees(math.atan2(xax[1], xax[0]))

    def pt_to_param_deg(trims):
        for t in trims:
            if hasattr(t, 'Coordinates'):
                dx = float(t.Coordinates[0]) - cx
                dy = float(t.Coordinates[1]) - cy
                xl = (dx*xax[0] + dy*xax[1]) / ra
                yl = (dx*yax[0] + dy*yax[1]) / rb
                return math.degrees(math.atan2(yl, xl))
        return None

    def val_to_param_deg(trims):
        for t in trims:
            if not hasattr(t, 'Coordinates'):
                return float(t)  # IFC practice: degrees, not radians
        return None

    a1 = val_to_param_deg(item.Trim1) or pt_to_param_deg(item.Trim1)
    a2 = val_to_param_deg(item.Trim2) or pt_to_param_deg(item.Trim2)
    if a1 is None or a2 is None:
        return None

    # Convert from circle-local parameter to element-local 2D angle
    a1_world = a1 + ref_ang_deg
    a2_world = a2 + ref_ang_deg

    # Full circle check
    if abs((a1_world % 360) - (a2_world % 360)) < 0.01:
        return (cx, cy, r)

    # DXF ARC always draws CCW from start to end
    if item.SenseAgreement:  # CCW arc: DXF start=a1, end=a2
        return (cx, cy, r, a1_world % 360, a2_world % 360)
    else:                    # CW arc: swap so DXF still goes CCW
        return (cx, cy, r, a2_world % 360, a1_world % 360)


def _trimmed_conic_flat(item, n_seg=16):
    """Tessellated fallback for IfcTrimmedCurve (used only for true IfcEllipse)."""
    import math
    basis = item.BasisCurve
    if not (basis.is_a("IfcCircle") or basis.is_a("IfcEllipse")):
        return [], []
    pos = basis.Position
    loc = pos.Location.Coordinates
    cx, cy = float(loc[0]), float(loc[1])
    if hasattr(pos, 'RefDirection') and pos.RefDirection:
        d  = pos.RefDirection.DirectionRatios
        xn = math.sqrt(float(d[0])**2 + float(d[1])**2)
        xax = [float(d[0])/xn, float(d[1])/xn]
    else:
        xax = [1.0, 0.0]
    yax = [-xax[1], xax[0]]
    ra = float(basis.Radius if basis.is_a("IfcCircle") else basis.SemiAxis1)
    rb = float(basis.Radius if basis.is_a("IfcCircle") else basis.SemiAxis2)

    def pt_to_param(trims):
        for t in trims:
            if hasattr(t, 'Coordinates'):
                dx = float(t.Coordinates[0]) - cx
                dy = float(t.Coordinates[1]) - cy
                xl = (dx*xax[0] + dy*xax[1]) / ra
                yl = (dx*yax[0] + dy*yax[1]) / rb
                return math.atan2(yl, xl)
        return None

    def val_to_param(trims):
        for t in trims:
            if not hasattr(t, 'Coordinates'):
                return math.radians(float(t))
        return None

    a1 = val_to_param(item.Trim1) or pt_to_param(item.Trim1)
    a2 = val_to_param(item.Trim2) or pt_to_param(item.Trim2)
    if a1 is None or a2 is None:
        return [], []
    if item.SenseAgreement:
        if a2 <= a1: a2 += 2*math.pi
    else:
        if a2 >= a1: a2 -= 2*math.pi
    verts = []
    for i in range(n_seg + 1):
        a  = a1 + (a2 - a1) * i / n_seg
        xl = ra * math.cos(a)
        yl = rb * math.sin(a)
        verts.extend([cx + xl*xax[0] + yl*yax[0],
                       cy + xl*xax[1] + yl*yax[1], 0.0])
    return verts, [k for k in range(n_seg) for k in (k, k+1)]


def _apply_arc_spec(spec, op):
    """Apply IfcCartesianTransformationOperator to an arc or circle spec."""
    if op is None or spec is None:
        return spec
    import math
    c = op.LocalOrigin.Coordinates
    ox, oy = float(c[0]), float(c[1])
    sc = float(op.Scale) if getattr(op, 'Scale', None) is not None else 1.0
    if op.Axis1:
        xd = op.Axis1.DirectionRatios
        xa = [float(xd[0]), float(xd[1])]
    else:
        xa = [1.0, 0.0]
    xn = math.sqrt(xa[0]**2 + xa[1]**2)
    xa = [xa[0]/xn, xa[1]/xn]
    ya = [-xa[1], xa[0]]
    rot_deg = math.degrees(math.atan2(xa[1], xa[0]))

    if len(spec) == 3:  # circle (cx, cy, r)
        cx, cy, r = spec
        new_cx = ox + (cx*xa[0] + cy*ya[0]) * sc
        new_cy = oy + (cx*xa[1] + cy*ya[1]) * sc
        return (new_cx, new_cy, r * sc)
    else:               # arc (cx, cy, r, start_deg, end_deg)
        cx, cy, r, s, e = spec
        new_cx = ox + (cx*xa[0] + cy*ya[0]) * sc
        new_cy = oy + (cx*xa[1] + cy*ya[1]) * sc
        return (new_cx, new_cy, r * sc, (s + rot_deg) % 360, (e + rot_deg) % 360)


def _apply_cart_transform_op(verts_flat, op):
    """Apply IfcCartesianTransformationOperator3D to a flat vertex list."""
    if op is None:
        return verts_flat
    c = op.LocalOrigin.Coordinates
    ox, oy, oz = float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0

    def _dir(attr):
        if attr is None:
            return None
        r = attr.DirectionRatios
        return np.array([float(r[0]), float(r[1]), float(r[2]) if len(r) > 2 else 0.0])

    xa = _dir(op.Axis1) if op.Axis1 else np.array([1.0, 0.0, 0.0])
    ya = _dir(op.Axis2) if op.Axis2 else np.array([0.0, 1.0, 0.0])
    sc = float(op.Scale) if getattr(op, "Scale", None) is not None else 1.0
    xa = xa / np.linalg.norm(xa) * sc
    ya = ya / np.linalg.norm(ya) * sc
    za = np.cross(xa / sc, ya / sc) * sc  # unit Z, then scaled

    result = []
    for i in range(len(verts_flat) // 3):
        lx, ly, lz = verts_flat[i*3], verts_flat[i*3+1], verts_flat[i*3+2]
        result.extend([
            ox + lx*xa[0] + ly*ya[0] + lz*za[0],
            oy + lx*xa[1] + ly*ya[1] + lz*za[1],
            oz + lx*xa[2] + ly*ya[2] + lz*za[2],
        ])
    return result


def _extract_curves_from_items(items, mapping_target=None):
    """Walk IFC shape items, return (verts_flat, edges_flat, arcs, circles).

    arcs:    [(cx, cy, r, dxf_start_deg, dxf_end_deg), ...]  — true DXF ARC specs
    circles: [(cx, cy, r), ...]                               — true DXF CIRCLE specs
    IfcEllipse with unequal axes falls back to tessellated verts/edges.
    """
    verts   = []   # flat [x0,y0,z0, ...]
    edges   = []   # flat [i0,j0, ...]
    arcs    = []   # arc specs
    circles = []   # circle specs

    def _merge_sub(sub_v, sub_e, sub_arcs, sub_circles):
        if sub_v:
            base = len(verts) // 3
            verts.extend(sub_v)
            for k in range(0, len(sub_e), 2):
                edges.extend([sub_e[k] + base, sub_e[k+1] + base])
        arcs.extend(sub_arcs)
        circles.extend(sub_circles)

    for item in items:
        if item.is_a("IfcMappedItem"):
            src = item.MappingSource
            _merge_sub(*_extract_curves_from_items(
                src.MappedRepresentation.Items,
                mapping_target=item.MappingTarget,
            ))

        elif item.is_a("IfcAnnotationFillArea"):
            _merge_sub(*_extract_curves_from_items([item.OuterBoundary]))

        elif item.is_a("IfcGeometricCurveSet") or item.is_a("IfcGeometricSet"):
            _merge_sub(*_extract_curves_from_items(list(item.Elements)))

        elif item.is_a("IfcCompositeCurve"):
            segs = [s.ParentCurve for s in item.Segments]
            _merge_sub(*_extract_curves_from_items(segs))

        elif item.is_a("IfcIndexedPolyCurve"):
            pts_ent = item.Points
            if pts_ent.is_a("IfcCartesianPointList2D"):
                pts = [[float(p[0]), float(p[1]), 0.0] for p in pts_ent.CoordList]
            elif pts_ent.is_a("IfcCartesianPointList3D"):
                pts = [[float(p[0]), float(p[1]), float(p[2])] for p in pts_ent.CoordList]
            else:
                continue

            base = len(verts) // 3
            for p in pts:
                verts.extend(p)

            segs = item.Segments
            if segs:
                for seg in segs:
                    # ifcopenshell wraps index list in an outer tuple: seg[0] = (i0, i1, ...)
                    idxs = [int(i) for i in seg[0]]   # 1-based
                    if seg.is_a("IfcLineIndex"):
                        for k in range(len(idxs) - 1):
                            edges.extend([base + idxs[k]-1, base + idxs[k+1]-1])
                    elif seg.is_a("IfcArcIndex"):
                        p1 = pts[idxs[0]-1]; p2 = pts[idxs[1]-1]; p3 = pts[idxs[2]-1]
                        spec = _arc_3pts_spec(p1, p2, p3)
                        if spec is not None:
                            arcs.append(spec)
                        # else: collinear → skip (degenerate arc)
            else:
                # Closed polygon without explicit segments: connect in order
                n = len(pts)
                for k in range(n):
                    edges.extend([base + k, base + (k+1) % n])

        elif item.is_a("IfcPolyline"):
            base = len(verts) // 3
            for pt in item.Points:
                c = pt.Coordinates
                verts.extend([float(c[0]), float(c[1]),
                               float(c[2]) if len(c) > 2 else 0.0])
            n = len(item.Points)
            for k in range(n - 1):
                edges.extend([base + k, base + k + 1])

        elif item.is_a("IfcTrimmedCurve"):
            spec = _trimmed_conic_spec(item)
            if spec is not None:
                if len(spec) == 3:
                    circles.append(spec)
                else:
                    arcs.append(spec)
            else:
                # Fallback: tessellate (IfcEllipse with unequal axes)
                tv, te = _trimmed_conic_flat(item)
                if tv:
                    tb = len(verts) // 3
                    verts.extend(tv)
                    edges.extend([tb + i for i in te])

        elif item.is_a("IfcCircle"):
            c2d = item.Position.Location.Coordinates
            cx, cy = float(c2d[0]), float(c2d[1])
            circles.append((cx, cy, float(item.Radius)))

    if mapping_target is not None:
        if verts:
            verts = _apply_cart_transform_op(verts, mapping_target)
        arcs    = [_apply_arc_spec(s, mapping_target) for s in arcs]
        circles = [_apply_arc_spec(s, mapping_target) for s in circles]

    return verts, edges, arcs, circles


def _extract_local_curves(element, plan_repr):
    """Extract plan curves in element-local coords.

    Returns (verts_flat, edges_flat, arcs, circles).
    Tries manual item walking first; falls back to ifcopenshell geometry engine
    (which produces tessellated lines only, no arc/circle specs).
    """
    items = plan_repr.Items
    if items:
        v, e, arcs, circles = _extract_curves_from_items(list(items))
        if v or e or arcs or circles:
            return v, e, arcs, circles

    # Fallback: ifcopenshell geometry engine with the specific context
    ctx_id = plan_repr.ContextOfItems.id()
    s = ifcopenshell.geom.settings()
    s.set('use-world-coords', False)
    s.set('context-ids', [ctx_id])
    shape = ifcopenshell.geom.create_shape(s, element)
    return list(shape.geometry.verts), list(shape.geometry.edges), [], []


# ── wall section modes ────────────────────────────────────────────────────────
#
# flat    — current: project edges to model space as LINE entities (no hatch)
# shapely — project edges to 2D, shapely.polygonize → union by (class, material)
#           → LWPOLYLINE outline + hatch fill
#
WALL_MODES = ("flat", "shapely")


def _project_wall_edges_2d(verts_flat, edges_flat, world_matrix_flat, cam_inv_col_major):
    """Project wall local edges to 2D drawing space; return shapely LineString list."""
    world_m = np.array(world_matrix_flat, dtype=float).reshape(4, 4, order='F')
    cam_inv = np.array(cam_inv_col_major, dtype=float).reshape(4, 4, order='F')
    combined = cam_inv @ world_m

    n = len(verts_flat) // 3
    verts = np.array(verts_flat[:n * 3], dtype=float).reshape(n, 3)
    pts_h = np.hstack([verts, np.ones((n, 1))])
    pts_2d = (combined @ pts_h.T).T[:, :2]

    lines = []
    for k in range(0, len(edges_flat) - 1, 2):
        i, j = int(edges_flat[k]), int(edges_flat[k + 1])
        x0, y0 = float(pts_2d[i, 0]), float(pts_2d[i, 1])
        x1, y1 = float(pts_2d[j, 0]), float(pts_2d[j, 1])
        dist2 = (x0 - x1) ** 2 + (y0 - y1) ** 2
        if dist2 > 1e-18:
            lines.append(shapely.LineString([(x0, y0), (x1, y1)]))
    return lines


# ---------------------------------------------------------------------------
# Wall profile extraction helpers (IfcExtrudedAreaSolid → 2D polygon)
# ---------------------------------------------------------------------------

def _profile_to_pts_2d(profile):
    """Return list of (x, y) from an IFC profile definition, or None."""
    import math
    if profile.is_a("IfcRectangleProfileDef"):
        x, y = float(profile.XDim) / 2, float(profile.YDim) / 2
        return [(-x, -y), (x, -y), (x, y), (-x, y)]
    if profile.is_a("IfcArbitraryClosedProfileDef"):
        curve = profile.OuterCurve
        if curve.is_a("IfcPolyline"):
            pts = [(float(p.Coordinates[0]), float(p.Coordinates[1]))
                   for p in curve.Points]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            return pts if len(pts) >= 3 else None
        if curve.is_a("IfcIndexedPolyCurve"):
            cl = curve.Points.CoordList
            pts = [(float(c[0]), float(c[1])) for c in cl]
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            return pts if len(pts) >= 3 else None
    if profile.is_a("IfcCircleProfileDef"):
        r = float(profile.Radius)
        return [(r * math.cos(2 * math.pi * i / 32),
                 r * math.sin(2 * math.pi * i / 32)) for i in range(32)]
    return None


def _apply_axis2placement3d(pts_2d, placement):
    """Apply IfcAxis2Placement3D to 2D profile points → 3D element-local array."""
    pts = np.array(pts_2d, dtype=float)
    if placement is None:
        return np.hstack([pts, np.zeros((len(pts), 1))])

    loc = placement.Location.Coordinates
    origin = np.array([float(loc[0]), float(loc[1]),
                       float(loc[2]) if len(loc) > 2 else 0.0])

    if placement.RefDirection:
        rd = placement.RefDirection.DirectionRatios
        x_ax = np.array([float(rd[0]), float(rd[1]),
                         float(rd[2]) if len(rd) > 2 else 0.0])
    else:
        x_ax = np.array([1.0, 0.0, 0.0])
    x_ax = x_ax / (np.linalg.norm(x_ax) or 1.0)

    if placement.Axis:
        ax = placement.Axis.DirectionRatios
        z_ax = np.array([float(ax[0]), float(ax[1]),
                         float(ax[2]) if len(ax) > 2 else 0.0])
    else:
        z_ax = np.array([0.0, 0.0, 1.0])
    z_ax = z_ax / (np.linalg.norm(z_ax) or 1.0)

    y_ax = np.cross(z_ax, x_ax)
    n = np.linalg.norm(y_ax)
    y_ax = y_ax / n if n > 1e-9 else np.array([0.0, 1.0, 0.0])

    xs = pts[:, 0:1]
    ys = pts[:, 1:2]
    return origin + xs * x_ax + ys * y_ax  # shape (N, 3)


def _extrusion_parallel_to_camera(item, wm_flat, camera_dir):
    """Return True if item's ExtrudedDirection is within ~15° of camera_dir in world space."""
    if not item.is_a("IfcExtrudedAreaSolid"):
        return False
    ed = item.ExtrudedDirection.DirectionRatios
    local_dir = np.array([float(ed[0]), float(ed[1]),
                          float(ed[2]) if len(ed) > 2 else 0.0])

    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    world_dir = (world_m[:3, :3] @ local_dir)
    n = np.linalg.norm(world_dir)
    if n < 1e-9:
        return False
    world_dir = world_dir / n

    cam = np.array(camera_dir, dtype=float)
    cn = np.linalg.norm(cam)
    if cn < 1e-9:
        return False
    cam = cam / cn

    return abs(float(np.dot(world_dir, cam))) > 0.966  # cos(15°)


def _extruded_plan_polygon(item, wm_flat, cam_inv_col_major, camera_dir):
    """Convert IfcExtrudedAreaSolid (or BooleanResult wrapping one) to plan 2D polygon.

    Rejects items whose extrusion direction is not sufficiently parallel to the
    camera (non-vertical walls, ramps, slabs at angle): caller should fall back
    to Bucket C / OCC for those cases.
    """
    # Unwrap boolean wrappers to get the base extrusion
    depth = 0
    while item.is_a("IfcBooleanClippingResult") or item.is_a("IfcBooleanResult"):
        item = item.FirstOperand
        depth += 1
        if depth > 8:
            return None

    if not item.is_a("IfcExtrudedAreaSolid"):
        return None

    # Only use profile extraction when extrusion is (nearly) parallel to view.
    # For non-vertical extrusions (ramps, sloped geometry), fall through to OCC.
    if not _extrusion_parallel_to_camera(item, wm_flat, camera_dir):
        return None

    pts_2d = _profile_to_pts_2d(item.SweptArea)
    if not pts_2d:
        return None

    pts_3d = _apply_axis2placement3d(pts_2d, item.Position)

    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    cam_inv = np.array(cam_inv_col_major, dtype=float).reshape(4, 4, order='F')
    combined = cam_inv @ world_m

    pts_h = np.hstack([pts_3d, np.ones((len(pts_3d), 1))])
    pts_draw = (combined @ pts_h.T).T[:, :2]

    try:
        poly = shapely.Polygon(pts_draw.tolist())
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly if poly.area > 1e-6 else None
    except Exception:
        return None


def _wall_profile_polygon(element, wm_flat, cam_inv_col_major, camera_dir):
    """Extract wall plan polygon from the first usable IfcExtrudedAreaSolid."""
    if not hasattr(element, 'Representation') or element.Representation is None:
        return None
    for repr_ in element.Representation.Representations:
        for item in repr_.Items:
            poly = _extruded_plan_polygon(item, wm_flat, cam_inv_col_major, camera_dir)
            if poly is not None:
                return poly
    return None


def _opening_footprint_polygon(opening, cam_inv_col_major):
    """Get plan footprint of an IfcOpeningElement via geom tessellation.

    Returns (polygon_2d, z_min, z_max) in world space, or (None, None, None).
    The Z values are used to classify openings relative to the section cut plane:
    - z_min < cut_z < z_max  → opening straddles section plane → subtract from wall
    - z_min >= cut_z          → opening is entirely above section → overhead indicator
    - z_max <= cut_z          → opening is entirely below section → ignore
    """
    try:
        settings = ifcopenshell.geom.settings()
        settings.set('use-world-coords', True)
        shape = ifcopenshell.geom.create_shape(settings, opening)
        verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
        z_min = float(verts[:, 2].min())
        z_max = float(verts[:, 2].max())

        cam_inv = np.array(cam_inv_col_major, dtype=float).reshape(4, 4, order='F')
        ones = np.ones((len(verts), 1))
        pts_2d = (cam_inv @ np.hstack([verts, ones]).T).T[:, :2]
        hull = shapely.convex_hull(shapely.MultiPoint(pts_2d.tolist()))
        if hull.geom_type == 'Polygon' and hull.area > 1e-6:
            return hull, z_min, z_max
    except Exception:
        pass
    return None, None, None


def _wall_z_range(element, wm_flat):
    """Return (z_min, z_max) in world space from the wall's IfcExtrudedAreaSolid.

    Used to classify the wall as cut (straddles cut_z) or viewed (below cut_z).
    Returns (None, None) if the geometry is not an IfcExtrudedAreaSolid.
    """
    if not hasattr(element, 'Representation') or element.Representation is None:
        return None, None

    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')

    for repr_ in element.Representation.Representations:
        for item in repr_.Items:
            cur, depth = item, 0
            while (cur.is_a("IfcBooleanClippingResult") or
                   cur.is_a("IfcBooleanResult")):
                cur = cur.FirstOperand
                depth += 1
                if depth > 8:
                    break
            if not cur.is_a("IfcExtrudedAreaSolid"):
                continue

            # Profile Z offset in element-local space
            pos_z = 0.0
            if cur.Position and cur.Position.Location:
                loc = cur.Position.Location.Coordinates
                if len(loc) > 2:
                    pos_z = float(loc[2])

            solid_depth = float(cur.Depth)

            # Extrusion direction in world space
            ed = cur.ExtrudedDirection.DirectionRatios
            local_dir = np.array([float(ed[0]), float(ed[1]),
                                  float(ed[2]) if len(ed) > 2 else 0.0])
            world_dir = world_m[:3, :3] @ local_dir  # rotation only
            dz = float(world_dir[2]) * solid_depth

            # Base Z in world
            local_base = np.array([0.0, 0.0, pos_z, 1.0])
            z_base = float((world_m @ local_base)[2])

            return min(z_base, z_base + dz), max(z_base, z_base + dz)

    return None, None


def _extract_wall_polygon_with_openings(element, wm_flat, cam_inv_col_major,
                                         cut_z, camera_dir):
    """Wall plan polygon = IfcExtrudedAreaSolid profile minus ALL openings.

    All openings are subtracted regardless of their height relative to cut_z —
    standard 2D drafting convention: the gap in the hatch shows the opening exists.

    Returns (wall_poly, is_section):
        wall_poly   — shapely Polygon/MultiPolygon, or None on failure
        is_section  — True if the wall straddles the cut plane (cut → hatch)
                      False if the wall is entirely below the cut (view → outline only)
    """
    wall_poly = _wall_profile_polygon(element, wm_flat, cam_inv_col_major, camera_dir)
    if wall_poly is None or wall_poly.area < 1e-4:
        return None, True

    # Determine section vs view based on wall Z range
    z_min, z_max = _wall_z_range(element, wm_flat)
    if z_min is None:
        is_section = True  # unknown → default to section
    else:
        is_section = z_min <= cut_z <= z_max

    # Subtract ALL openings (overhead or not — traditional 2D drafting)
    opening_polys = []
    for rel in getattr(element, 'HasOpenings', []):
        op = rel.RelatedOpeningElement
        if not hasattr(op, 'ObjectPlacement') or op.ObjectPlacement is None:
            continue
        op_poly, _zmin, _zmax = _opening_footprint_polygon(op, cam_inv_col_major)
        if op_poly is not None and op_poly.area > 1e-6:
            opening_polys.append(op_poly)

    if opening_polys:
        try:
            openings = shapely.ops.unary_union(opening_polys)
            result = wall_poly.difference(openings)
            if not result.is_empty and result.area > 1e-6:
                wall_poly = result
        except Exception:
            pass

    return wall_poly, is_section


def _add_wall_polygons(req, wall_polys_by_key):
    """Union per-wall polygons by (ifc_class, material, is_section), add WallPolygon to req.

    wall_polys_by_key: {(ifc_class, material, is_section) → [shapely poly, ...]}
    """
    # Snap tolerance: expand each polygon by this amount before union so that
    # walls with sub-mm gaps (IFC modelling tolerance) merge correctly, then
    # shrink back to recover the original outline.
    SNAP_TOL = 0.0005  # 0.5 mm in metres

    for (ifc_class, material, is_section), polys in wall_polys_by_key.items():
        if not polys:
            continue
        try:
            expanded = [p.buffer(SNAP_TOL, join_style=2) for p in polys]
            merged   = shapely.ops.unary_union(expanded).buffer(-SNAP_TOL, join_style=2)
        except Exception:
            merged = polys[0] if len(polys) == 1 else None
        if merged is None:
            continue

        if merged.geom_type == 'MultiPolygon':
            result_polys = list(merged.geoms)
        elif merged.geom_type == 'Polygon':
            result_polys = [merged]
        else:
            result_polys = [g for g in merged.geoms
                            if g.geom_type == 'Polygon']

        for poly in result_polys:
            outer = [[float(x), float(y)] for x, y in poly.exterior.coords[:-1]]
            holes = [[[float(x), float(y)] for x, y in ring.coords[:-1]]
                     for ring in poly.interiors]
            try:
                req.add_wall_polygon(ifc_class, material, outer, holes, is_section)
            except Exception:
                pass


# ── export ────────────────────────────────────────────────────────────────────

def load_layer_styles(styles_path=None):
    """Load layer styles from a JSON file.

    lineweight in the JSON is in mm (e.g. 0.35); it is snapped to the nearest
    valid DXF lineweight value (in hundredths of mm) before passing to Rust.

    Returns a list of (layer_name, color, lineweight_hundredths, linetype) tuples.
    """
    # Valid DXF lineweight values in hundredths of mm
    _DXF_LW = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50,
               53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)

    def _snap_lw(mm_value):
        hundredths = round(float(mm_value) * 100)
        return min(_DXF_LW, key=lambda v: abs(v - hundredths))

    if styles_path is None:
        styles_path = os.path.join(SCRIPT_DIR, "layer_styles.json")
    if not os.path.isfile(styles_path):
        return []
    try:
        with open(styles_path, encoding="utf-8") as f:
            data = json.load(f)
        result = []
        for layer_name, props in data.items():
            if layer_name.startswith("_"):
                continue
            color      = int(props.get("color", 7))
            lineweight = _snap_lw(props.get("lineweight", 0.25))
            linetype   = str(props.get("linetype", "Continuous"))
            result.append((layer_name, color, lineweight, linetype))
        return result
    except Exception as exc:
        print(f"  (layer_styles.json load failed: {exc})")
        return []


def export_drawing(ifc, drawing, pset, output_path, wall_mode="shapely",
                   styles_path=None):
    import time
    if wall_mode == "shapely" and not SHAPELY_AVAILABLE:
        print("  (shapely not available → falling back to flat wall mode)")
        wall_mode = "flat"
    target_view = pset.get("TargetView", "PLAN_VIEW")
    human_scale = pset.get("HumanScale", "NTS")
    print(f"  TargetView : {target_view}   Scale: {human_scale}   WallMode: {wall_mode}")

    col_major        = camera_matrix_inv_col_major(drawing)
    cam_dir, cam_pos = camera_dir_pos(drawing)

    # Camera rotation matrix (3×3) for projecting arc centres/angles to block-local space.
    # project_local applies only the rotation part of matrix_inv; same here.
    _cam_inv_np = np.array(col_major, dtype=float).reshape(4, 4, order='F')
    _cam_R      = _cam_inv_np[:3, :3]
    _cam_x_proj = _cam_R @ np.array([1.0, 0.0, 0.0])
    _cam_rot_deg = float(np.degrees(np.arctan2(float(_cam_x_proj[1]), float(_cam_x_proj[0]))))

    proj = ifc_dxf.PyCameraProjection(col_major)
    req  = ifc_dxf.PyDrawingRequest(
        proj, cam_dir, cam_pos, target_view, output_path, "AC1027"
    )

    layer_styles = load_layer_styles(styles_path)
    if layer_styles:
        req.set_layer_styles(layer_styles)
        print(f"  Layer styles: {len(layer_styles)} overrides")

    elements = get_elements(ifc, drawing, pset)
    print(f"  Elements   : {len(elements)}")

    settings_c = ifcopenshell.geom.settings()
    settings_c.set('use-world-coords', True)
    settings_c.set('iterator-output', 2)  # Serialization → brep_data

    # IFC classes that bypass BLOCK/INSERT and go through wall-merge path.
    WALL_MERGE_CLASSES = frozenset({"IfcWall", "IfcWallStandardCase"})

    # Track blocks already defined (type-shared or element-specific)
    seen_blocks = {}   # block_name → True
    # For shapely mode: per-wall polygon (polygonized in isolation), grouped by key
    wall_polys_by_key = {}  # (ifc_class, material) → [shapely.Polygon, ...]

    bucket_wall = bucket_b = bucket_c = skipped = 0
    skipped_classes = {}
    bucket_b_classes = {}
    bucket_c_classes = {}

    for element in elements:
        ifc_class = element.is_a()
        material  = get_material_name(element)
        gid       = element.GlobalId
        wm        = world_matrix_col_major(element)

        # ── Wall path (flat or shapely) ───────────────────────────────────────
        if ifc_class in WALL_MERGE_CLASSES:
            if wall_mode == "shapely":
                # Profile-based: IfcExtrudedAreaSolid + opening subtraction (2D).
                # Falls through to Bucket B/C when extrusion is non-vertical or
                # geometry is not IfcExtrudedAreaSolid (OCC path, not yet implemented).
                try:
                    poly, is_section = _extract_wall_polygon_with_openings(
                        element, wm, col_major, cam_pos[2], cam_dir
                    )
                    if poly is not None:
                        key = (ifc_class, material, is_section)
                        wall_polys_by_key.setdefault(key, []).append(poly)
                        bucket_wall += 1
                        continue
                except Exception:
                    pass
            else:
                # flat mode: project edges as LINE entities
                plan_repr, _ = find_plan_repr(element, target_view)
                if plan_repr is not None:
                    try:
                        verts, edges, _arcs, _circles = _extract_local_curves(element, plan_repr)
                        if verts and edges:
                            req.add_wall_flat(ifc_class, material, verts, edges, wm)
                            bucket_wall += 1
                            continue
                    except Exception:
                        pass
            # fall through to standard Bucket B/C if wall geometry unavailable

        # ── Bucket B: 2D plan native representation ──────────────────────────
        # find_plan_repr checks element first (override), then type (inherited).
        plan_repr, from_type = find_plan_repr(element, target_view)
        if plan_repr is not None:
            try:
                # Block name: use type name when geometry comes from the type
                # (either via IfcMappedItem on the element, or directly from the
                # type's RepresentationMaps).  Element override → use GlobalId.
                if from_type or is_mapped_repr(plan_repr):
                    _, block_name = get_type_block_name(element)
                    if block_name is None:
                        block_name = gid  # no type assigned — shouldn't happen here
                else:
                    block_name = gid

                # Define block geometry once per block_name
                if block_name not in seen_blocks:
                    verts, edges, arcs, circles = _extract_local_curves(element, plan_repr)
                    if verts or edges or arcs or circles:
                        req.add_native_curves(block_name, ifc_class, material,
                                              verts or [], edges or [])
                        # Append true DXF ARC/CIRCLE entities to the block.
                        # Arc centres and angles are projected to block-local space
                        # (camera rotation applied; INSERT handles element rotation).
                        for spec in arcs:
                            cx_e, cy_e, r, a_s, a_e = spec
                            c_blk = _cam_R @ np.array([cx_e, cy_e, 0.0])
                            req.add_block_arc(
                                block_name, ifc_class, material,
                                float(c_blk[0]), float(c_blk[1]), r,
                                (a_s + _cam_rot_deg) % 360,
                                (a_e + _cam_rot_deg) % 360,
                            )
                        for spec in circles:
                            cx_e, cy_e, r = spec
                            c_blk = _cam_R @ np.array([cx_e, cy_e, 0.0])
                            req.add_block_circle(
                                block_name, ifc_class, material,
                                float(c_blk[0]), float(c_blk[1]), r,
                            )
                        seen_blocks[block_name] = True

                # Add INSERT for this element instance
                if block_name in seen_blocks:
                    req.add_block_insert(block_name, ifc_class, wm)
                    bucket_b += 1
                    bucket_b_classes[ifc_class] = bucket_b_classes.get(ifc_class, 0) + 1
                    continue
            except Exception:
                pass  # fall through to Bucket C

        # ── Bucket C fallback: 3D body wireframe ─────────────────────────────
        try:
            shape = ifcopenshell.geom.create_shape(settings_c, element)
            brep  = shape.geometry.brep_data
            req.add_body_brep(gid, ifc_class, material, brep, tuple(wm))
            bucket_c += 1
            bucket_c_classes[ifc_class] = bucket_c_classes.get(ifc_class, 0) + 1
        except Exception:
            skipped += 1
            skipped_classes[ifc_class] = skipped_classes.get(ifc_class, 0) + 1

    print(f"  Wall       : {bucket_wall}  Bucket B: {bucket_b}  Bucket C: {bucket_c}  Skipped: {skipped}")
    if skipped_classes:
        print(f"  Skipped by class : { {k: v for k, v in sorted(skipped_classes.items())} }")
    if bucket_b_classes:
        print(f"  Bucket B by class: { {k: v for k, v in sorted(bucket_b_classes.items())} }")
    if bucket_c_classes:
        print(f"  Bucket C by class: { {k: v for k, v in sorted(bucket_c_classes.items())} }")

    if wall_mode == "shapely" and wall_polys_by_key:
        n_polys = sum(len(v) for v in wall_polys_by_key.values())
        n_sec   = sum(len(v) for (_, _, s), v in wall_polys_by_key.items() if s)
        n_view  = n_polys - n_sec
        print(f"  Wall polys : {n_polys} total ({n_sec} section, {n_view} view)"
              f"  in {len(wall_polys_by_key)} groups")
        _add_wall_polygons(req, wall_polys_by_key)

    t0 = time.perf_counter()
    ifc_dxf.py_generate(req, None)
    elapsed = time.perf_counter() - t0
    size_kb = os.path.getsize(output_path) // 1024
    print(f"  DXF gen    : {elapsed:.2f}s")
    print(f"  >> {output_path}  ({size_kb} KB)")
    return output_path


# ── optional preview via ezdxf ────────────────────────────────────────────────

def render_preview(dxf_path):
    """Render the DXF to a PNG using ezdxf + matplotlib (optional)."""
    try:
        import ezdxf
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as e:
        print(f"  (preview skipped — {e})")
        return

    try:
        from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy
        preview_path = dxf_path.replace(".dxf", "_preview.png")
        pdf_path     = dxf_path.replace(".dxf", ".pdf")
        doc = ezdxf.readfile(dxf_path)
        fig = plt.figure(figsize=(20, 16))
        ax  = fig.add_axes([0, 0, 1, 1])
        ax.set_facecolor("white")
        ctx = RenderContext(doc)
        out = MatplotlibBackend(ax)
        cfg = Configuration.defaults()
        cfg = cfg.with_changes(background_policy=BackgroundPolicy.WHITE)
        Frontend(ctx, out, config=cfg).draw_layout(doc.modelspace(), finalize=True)
        fig.savefig(preview_path, dpi=150, bbox_inches="tight",
                    facecolor="white")
        fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"  Preview    : {preview_path}")
        print(f"  PDF        : {pdf_path}")
    except Exception as exc:
        print(f"  (preview failed: {exc})")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ifc_dxf standalone test — IFC → DXF (no Blender needed)"
    )
    parser.add_argument("--ifc",     default=DEFAULT_IFC,
                        help="Path to IFC file")
    parser.add_argument("--drawing", type=int, default=0,
                        help="Drawing index to export (default 0)")
    parser.add_argument("--out",     default=None,
                        help="Output .dxf path (default: alongside the IFC)")
    parser.add_argument("--list",    action="store_true",
                        help="List available drawings and exit")
    parser.add_argument("--no-preview", action="store_true",
                        help="Skip PNG preview rendering")
    parser.add_argument("--wall-mode", default="shapely",
                        choices=WALL_MODES,
                        help="Wall section mode: shapely (default) or flat")
    args = parser.parse_args()

    print(f"Loading IFC: {args.ifc}")
    ifc = ifcopenshell.open(args.ifc)

    drawings = find_drawings(ifc)
    if not drawings:
        print("ERROR: no drawings found (no EPset_Drawing pset in any IfcAnnotation).")
        sys.exit(1)

    print(f"\n{len(drawings)} drawing(s) found:")
    for i, (ann, pset) in enumerate(drawings):
        marker = ">>" if i == args.drawing else "  "
        tv = pset.get("TargetView", "?")
        hs = pset.get("HumanScale", "?")
        print(f"  {marker} [{i}]  {ann.Name or ann.GlobalId!r}  ({tv}  {hs})")

    if args.list:
        return

    if args.drawing >= len(drawings):
        print(f"\nERROR: drawing index {args.drawing} out of range (0–{len(drawings)-1}).")
        sys.exit(1)

    drawing, pset = drawings[args.drawing]
    name = (drawing.Name or drawing.GlobalId).replace("/", "_").replace(" ", "_")
    print(f"\nExporting [{args.drawing}]: {drawing.Name or drawing.GlobalId}")

    if args.out:
        out_path = args.out
    else:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{name}.dxf")

    export_drawing(ifc, drawing, pset, out_path, wall_mode=args.wall_mode)

    if not args.no_preview:
        render_preview(out_path)


if __name__ == "__main__":
    main()
