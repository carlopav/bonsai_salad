# Bonsai Salad — ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Export a Bonsai drawing to DXF using ezdxf (pure Python, no Rust).
# Designed to be eventually proposed for upstream integration into
# IfcOpenShell / Bonsai (bonsai/bim/module/drawing/).

import os
import sys
import math
import json

import bpy
import numpy as np

try:
    import shapely
    import shapely.ops
    _SHAPELY = True
except ImportError:
    _SHAPELY = False


# ---------------------------------------------------------------------------
# Bonsai / IFC helpers
# ---------------------------------------------------------------------------

def _get_ifc():
    try:
        from bonsai import tool
        return tool.Ifc.get()
    except Exception:
        return None


def _get_active_drawing():
    try:
        from bonsai import tool
        item = tool.Drawing.get_active_drawing_item()
        if item is None:
            return None
        return tool.Ifc.get().by_id(item.ifc_definition_id)
    except Exception:
        return None


def _get_camera_obj(drawing):
    try:
        from bonsai import tool
        return tool.Ifc.get_object(drawing)
    except Exception:
        return None


def _get_target_view(drawing):
    try:
        from bonsai import tool
        return tool.Drawing.get_drawing_target_view(drawing)
    except Exception:
        return "PLAN_VIEW"


def _camera_matrices(camera_obj):
    """Return (col_major_inv, cam_dir, cam_pos) from Blender camera object.

    col_major_inv: flat 16-float column-major inverse of the camera world matrix.
    cam_dir: camera look direction [-Z in world space].
    cam_pos: camera world position.
    """
    from bonsai import tool
    m = tool.Drawing.get_camera_matrix(camera_obj)  # mathutils.Matrix
    m_inv = m.inverted()
    col_major = [v for col in m_inv.col for v in col]
    cam_dir = list(-m.col[2].xyz.normalized())
    cam_pos = list(m.col[3].xyz)
    return col_major, cam_dir, cam_pos


def _get_material_name(element):
    try:
        import ifcopenshell.util.element as ifc_elem
        mats = ifc_elem.get_materials(element, should_inherit=True)
        if mats:
            return mats[0].Name or ""
    except Exception:
        pass
    return ""


def _world_matrix_flat(element):
    """Column-major flat 16-float world placement matrix of the element."""
    try:
        import ifcopenshell.util.placement as ifc_place
        placement = getattr(element, "ObjectPlacement", None)
        if placement is None:
            return list(np.eye(4).flatten(order="F"))
        return list(ifc_place.get_local_placement(placement).flatten(order="F"))
    except Exception:
        return list(np.eye(4).flatten(order="F"))


# ---------------------------------------------------------------------------
# Plan representation search
# ---------------------------------------------------------------------------

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


def _find_plan_repr(element, target_view):
    """Return (IfcShapeRepresentation, from_type). from_type=True = inherited from type."""
    import ifcopenshell.util.element as ifc_elem

    search = _PLAN_SEARCH.get(target_view, [])

    def _search(reprs):
        for ctx_type, ctx_id, tv in search:
            for r in reprs:
                ctx = r.ContextOfItems
                if (ctx.ContextType == ctx_type
                        and ctx.ContextIdentifier == ctx_id
                        and getattr(ctx, "TargetView", None) == tv):
                    return r
        return None

    if hasattr(element, "Representation") and element.Representation is not None:
        r = _search(element.Representation.Representations)
        if r is not None:
            return r, False

    ifc_type = ifc_elem.get_type(element)
    if ifc_type is not None:
        type_reprs = [rm.MappedRepresentation
                      for rm in (getattr(ifc_type, "RepresentationMaps", None) or [])]
        r = _search(type_reprs)
        if r is not None:
            return r, True

    return None, False


def _is_mapped_repr(plan_repr):
    items = plan_repr.Items
    return bool(items) and all(item.is_a("IfcMappedItem") for item in items)


def _get_type_block_name(element):
    import ifcopenshell.util.element as ifc_elem
    ifc_type = ifc_elem.get_type(element)
    if ifc_type is None:
        return None, None
    name = (getattr(ifc_type, "Name", None) or ifc_type.is_a())
    return ifc_type, f"{name}_{ifc_type.GlobalId[:8]}"


# ---------------------------------------------------------------------------
# IFC curve extraction — returns (verts, edges, arcs, circles)
# ---------------------------------------------------------------------------

def _apply_cart_transform_op(verts_flat, op):
    if op is None:
        return verts_flat
    c = op.LocalOrigin.Coordinates
    ox, oy, oz = float(c[0]), float(c[1]), (float(c[2]) if len(c) > 2 else 0.0)

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
    za = np.cross(xa / sc, ya / sc) * sc
    result = []
    for i in range(len(verts_flat) // 3):
        lx, ly, lz = verts_flat[i*3], verts_flat[i*3+1], verts_flat[i*3+2]
        result.extend([
            ox + lx*xa[0] + ly*ya[0] + lz*za[0],
            oy + lx*xa[1] + ly*ya[1] + lz*za[1],
            oz + lx*xa[2] + ly*ya[2] + lz*za[2],
        ])
    return result


def _arc_3pts_spec(p1, p2, p3):
    """(cx, cy, r, dxf_start_deg, dxf_end_deg) for arc through 3 2D points, or None."""
    ax, ay = float(p1[0]), float(p1[1])
    bx, by = float(p2[0]), float(p2[1])
    cx, cy = float(p3[0]), float(p3[1])
    D = 2 * (ax*(by-cy) + bx*(cy-ay) + cx*(ay-by))
    if abs(D) < 1e-12:
        return None
    a2b = ax*ax+ay*ay; b2b = bx*bx+by*by; c2b = cx*cx+cy*cy
    ux = (a2b*(by-cy) + b2b*(cy-ay) + c2b*(ay-by)) / D
    uy = (a2b*(cx-bx) + b2b*(ax-cx) + c2b*(bx-ax)) / D
    r   = math.sqrt((ax-ux)**2 + (ay-uy)**2)
    a1  = math.degrees(math.atan2(ay-uy, ax-ux)) % 360
    a2  = math.degrees(math.atan2(cy-uy, cx-ux)) % 360
    am  = math.degrees(math.atan2(by-uy, bx-ux)) % 360
    ccw = (a1 < a2 and a1 <= am <= a2) or (a1 >= a2 and (am >= a1 or am <= a2))
    return (ux, uy, r, a1, a2) if ccw else (ux, uy, r, a2, a1)


def _trimmed_conic_spec(item):
    """Arc spec (cx,cy,r,s,e) or circle spec (cx,cy,r) for IfcTrimmedCurve, or None."""
    basis = item.BasisCurve
    if basis.is_a("IfcCircle"):
        r = ra = rb = float(basis.Radius)
    elif basis.is_a("IfcEllipse"):
        ra = float(basis.SemiAxis1)
        rb = float(basis.SemiAxis2)
        if abs(ra - rb) > 0.001 * max(ra, rb):
            return None  # true ellipse → tessellate
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
    ref_ang = math.degrees(math.atan2(xax[1], xax[0]))

    def _pt(trims):
        for t in trims:
            if hasattr(t, 'Coordinates'):
                dx = float(t.Coordinates[0]) - cx
                dy = float(t.Coordinates[1]) - cy
                return math.degrees(math.atan2(
                    (dx*yax[0] + dy*yax[1]) / rb,
                    (dx*xax[0] + dy*xax[1]) / ra))
        return None

    def _val(trims):
        for t in trims:
            if not hasattr(t, 'Coordinates'):
                return float(t)
        return None

    # Prefer CartesianPoint (unambiguous); fallback to ParameterValue.
    # Do NOT use `or` — 0.0° is falsy but valid.
    a1 = _pt(item.Trim1)
    if a1 is None:
        a1 = _val(item.Trim1)
    a2 = _pt(item.Trim2)
    if a2 is None:
        a2 = _val(item.Trim2)
    if a1 is None or a2 is None:
        return None

    a1w = a1 + ref_ang
    a2w = a2 + ref_ang
    if abs((a1w % 360) - (a2w % 360)) < 0.01:
        return (cx, cy, r)  # full circle
    if item.SenseAgreement:
        return (cx, cy, r, a1w % 360, a2w % 360)
    else:
        return (cx, cy, r, a2w % 360, a1w % 360)


def _trimmed_conic_flat(item, n_seg=16):
    """Tessellated fallback for IfcTrimmedCurve (true IfcEllipse with unequal axes)."""
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

    def _pt(trims):
        for t in trims:
            if hasattr(t, 'Coordinates'):
                dx, dy = float(t.Coordinates[0]) - cx, float(t.Coordinates[1]) - cy
                return math.atan2((dx*yax[0]+dy*yax[1])/rb, (dx*xax[0]+dy*xax[1])/ra)
        return None

    def _val(trims):
        for t in trims:
            if not hasattr(t, 'Coordinates'):
                return math.radians(float(t))
        return None

    a1 = _pt(item.Trim1)
    if a1 is None:
        a1 = _val(item.Trim1)
    a2 = _pt(item.Trim2)
    if a2 is None:
        a2 = _val(item.Trim2)
    if a1 is None or a2 is None:
        return [], []
    if item.SenseAgreement:
        if a2 <= a1: a2 += 2*math.pi
    else:
        if a2 >= a1: a2 -= 2*math.pi
    verts = []
    for i in range(n_seg + 1):
        a = a1 + (a2 - a1) * i / n_seg
        xl, yl = ra*math.cos(a), rb*math.sin(a)
        verts.extend([cx + xl*xax[0] + yl*yax[0], cy + xl*xax[1] + yl*yax[1], 0.0])
    return verts, [k for k in range(n_seg) for k in (k, k+1)]


def _apply_arc_spec(spec, op):
    """Apply IfcCartesianTransformationOperator to an arc/circle spec."""
    if op is None or spec is None:
        return spec
    c = op.LocalOrigin.Coordinates
    ox, oy = float(c[0]), float(c[1])
    sc = float(op.Scale) if getattr(op, 'Scale', None) is not None else 1.0
    xa = [float(op.Axis1.DirectionRatios[0]), float(op.Axis1.DirectionRatios[1])] \
         if op.Axis1 else [1.0, 0.0]
    xn = math.sqrt(xa[0]**2 + xa[1]**2)
    xa = [xa[0]/xn, xa[1]/xn]
    ya = [-xa[1], xa[0]]
    rot = math.degrees(math.atan2(xa[1], xa[0]))
    if len(spec) == 3:
        cx, cy, r = spec
        return (ox+(cx*xa[0]+cy*ya[0])*sc, oy+(cx*xa[1]+cy*ya[1])*sc, r*sc)
    cx, cy, r, s, e = spec
    return (ox+(cx*xa[0]+cy*ya[0])*sc, oy+(cx*xa[1]+cy*ya[1])*sc,
            r*sc, (s+rot)%360, (e+rot)%360)


def _apply_ellipse_spec(spec, op):
    """Apply IfcCartesianTransformationOperator to a DXF ellipse spec.

    spec = (cx, cy, maj_x, maj_y, ratio, t1_rad, t2_rad)
    """
    if op is None or spec is None:
        return spec
    c = op.LocalOrigin.Coordinates
    ox, oy = float(c[0]), float(c[1])
    sc = float(op.Scale) if getattr(op, 'Scale', None) is not None else 1.0
    xa = [float(op.Axis1.DirectionRatios[0]), float(op.Axis1.DirectionRatios[1])] \
         if op.Axis1 else [1.0, 0.0]
    xn = math.sqrt(xa[0]**2 + xa[1]**2)
    xa = [xa[0]/xn, xa[1]/xn]
    ya = [-xa[1], xa[0]]
    cx, cy, maj_x, maj_y, ratio, t1, t2 = spec
    new_cx  = ox + (cx*xa[0] + cy*ya[0]) * sc
    new_cy  = oy + (cx*xa[1] + cy*ya[1]) * sc
    new_mjx = (maj_x*xa[0] + maj_y*ya[0]) * sc
    new_mjy = (maj_x*xa[1] + maj_y*ya[1]) * sc
    return (new_cx, new_cy, new_mjx, new_mjy, ratio, t1, t2)


def _trimmed_ellipse_spec(item):
    """Return DXF ELLIPSE spec for IfcTrimmedCurve with IfcEllipse (unequal axes).

    Returns (cx, cy, maj_x, maj_y, ratio, t1_rad, t2_rad) or None.
    DXF ELLIPSE draws CCW from t1_rad to t2_rad in the major-axis frame.

    NOTE: Bonsai exports door swings as IfcEllipse even when ra≈rb (both axes
    differ only due to door thickness). This should be reported upstream as a
    bug — door arcs should be IfcCircle. This function is a workaround.
    """
    basis = item.BasisCurve
    if not basis.is_a("IfcEllipse"):
        return None
    ra = float(basis.SemiAxis1)  # along RefDirection (xax)
    rb = float(basis.SemiAxis2)  # perpendicular (yax)

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

    # DXF requires ratio = minor/major <= 1
    if ra >= rb:
        major = ra;  minor = rb
        major_dir = xax;             minor_dir = yax
    else:
        major = rb;  minor = ra
        major_dir = yax;             minor_dir = [-xax[0], -xax[1]]

    ratio = minor / major

    def pt_to_param(trims):
        for t in trims:
            if hasattr(t, 'Coordinates'):
                dx = float(t.Coordinates[0]) - cx
                dy = float(t.Coordinates[1]) - cy
                cos_t = (major_dir[0]*dx + major_dir[1]*dy) / major
                sin_t = (minor_dir[0]*dx + minor_dir[1]*dy) / minor
                return math.atan2(sin_t, cos_t)
        return None

    def val_to_param(trims):
        for t in trims:
            if not hasattr(t, 'Coordinates'):
                v = math.radians(float(t))
                if ra >= rb:
                    return v
                else:
                    return math.atan2(-math.cos(v), math.sin(v))
        return None

    t1 = pt_to_param(item.Trim1)
    if t1 is None:
        t1 = val_to_param(item.Trim1)
    t2 = pt_to_param(item.Trim2)
    if t2 is None:
        t2 = val_to_param(item.Trim2)
    if t1 is None or t2 is None:
        return None

    if item.SenseAgreement:
        if t2 <= t1:
            t2 += 2 * math.pi
    else:
        t1, t2 = t2, t1
        if t2 <= t1:
            t2 += 2 * math.pi

    return (cx, cy, major_dir[0]*major, major_dir[1]*major, ratio, t1, t2)


def _composite_curve_seg_endpoints(seg):
    """Return (start_pt, end_pt) as [x,y] lists for an IfcCompositeCurveSegment, or (None,None)."""
    curve = seg.ParentCurve
    sense = seg.SenseAgreement

    def _rev(p0, p1):
        return (p0, p1) if sense else (p1, p0)

    if curve.is_a("IfcPolyline"):
        pts = curve.Points
        if len(pts) < 2:
            return None, None
        p0 = [float(pts[0].Coordinates[0]),  float(pts[0].Coordinates[1])]
        p1 = [float(pts[-1].Coordinates[0]), float(pts[-1].Coordinates[1])]
        return _rev(p0, p1)

    if curve.is_a("IfcIndexedPolyCurve"):
        cl = curve.Points.CoordList
        if len(cl) < 2:
            return None, None
        p0 = [float(cl[0][0]),  float(cl[0][1])]
        p1 = [float(cl[-1][0]), float(cl[-1][1])]
        return _rev(p0, p1)

    if curve.is_a("IfcTrimmedCurve"):
        def _cart(trims):
            for t in trims:
                if hasattr(t, 'Coordinates'):
                    return [float(t.Coordinates[0]), float(t.Coordinates[1])]
            return None
        p0 = _cart(curve.Trim1)
        p1 = _cart(curve.Trim2)
        if p0 is None or p1 is None:
            return None, None
        return _rev(p0, p1)

    return None, None


def _extract_curves_from_items(items, mapping_target=None):
    """Walk IFC items, return (verts_flat, edges_flat, arcs, circles, ellipses)."""
    verts, edges, arcs, circles, ellipses = [], [], [], [], []

    def _merge(sv, se, sa, sc_, sel):
        if sv:
            base = len(verts) // 3
            verts.extend(sv)
            for k in range(0, len(se), 2):
                edges.extend([se[k]+base, se[k+1]+base])
        arcs.extend(sa); circles.extend(sc_); ellipses.extend(sel)

    for item in items:
        if item.is_a("IfcMappedItem"):
            src = item.MappingSource
            _merge(*_extract_curves_from_items(
                src.MappedRepresentation.Items,
                mapping_target=item.MappingTarget))

        elif item.is_a("IfcAnnotationFillArea"):
            _merge(*_extract_curves_from_items([item.OuterBoundary]))

        elif item.is_a("IfcGeometricCurveSet") or item.is_a("IfcGeometricSet"):
            _merge(*_extract_curves_from_items(list(item.Elements)))

        elif item.is_a("IfcCompositeCurve"):
            # Collect segment endpoints so bare IfcCircle segments can find their
            # arc boundaries from adjacent segments (connected-curve semantics).
            segs_cc = list(item.Segments)
            n_cc = len(segs_cc)
            seg_eps = [_composite_curve_seg_endpoints(s) for s in segs_cc]

            for idx_cc, seg_cc in enumerate(segs_cc):
                curve_cc = seg_cc.ParentCurve
                sense_cc = seg_cc.SenseAgreement

                if curve_cc.is_a("IfcCircle"):
                    prev_ep = seg_eps[(idx_cc - 1) % n_cc][1]
                    next_sp = seg_eps[(idx_cc + 1) % n_cc][0]
                    pos_cc = curve_cc.Position
                    loc_cc = pos_cc.Location.Coordinates
                    cx_cc, cy_cc = float(loc_cc[0]), float(loc_cc[1])
                    r_cc = float(curve_cc.Radius)
                    if prev_ep is not None and next_sp is not None:
                        a1_cc = math.degrees(math.atan2(
                            prev_ep[1] - cy_cc, prev_ep[0] - cx_cc)) % 360
                        a2_cc = math.degrees(math.atan2(
                            next_sp[1] - cy_cc, next_sp[0] - cx_cc)) % 360
                        if abs((a1_cc - a2_cc) % 360) < 0.01:
                            circles.append((cx_cc, cy_cc, r_cc))
                        elif sense_cc:
                            arcs.append((cx_cc, cy_cc, r_cc, a1_cc, a2_cc))
                        else:
                            arcs.append((cx_cc, cy_cc, r_cc, a2_cc, a1_cc))
                    else:
                        circles.append((cx_cc, cy_cc, r_cc))
                else:
                    _merge(*_extract_curves_from_items([curve_cc]))

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
                    idxs = [int(i) for i in seg[0]]  # seg[0] = actual index tuple
                    if seg.is_a("IfcLineIndex"):
                        for k in range(len(idxs) - 1):
                            edges.extend([base+idxs[k]-1, base+idxs[k+1]-1])
                    elif seg.is_a("IfcArcIndex"):
                        spec = _arc_3pts_spec(pts[idxs[0]-1], pts[idxs[1]-1], pts[idxs[2]-1])
                        if spec:
                            arcs.append(spec)
            else:
                n = len(pts)
                for k in range(n):
                    edges.extend([base+k, base+(k+1)%n])

        elif item.is_a("IfcPolyline"):
            base = len(verts) // 3
            for pt in item.Points:
                c = pt.Coordinates
                verts.extend([float(c[0]), float(c[1]),
                               float(c[2]) if len(c) > 2 else 0.0])
            n = len(item.Points)
            for k in range(n-1):
                edges.extend([base+k, base+k+1])

        elif item.is_a("IfcTrimmedCurve"):
            spec = _trimmed_conic_spec(item)
            if spec is not None:
                (circles if len(spec) == 3 else arcs).append(spec)
            elif item.BasisCurve.is_a("IfcEllipse"):
                espec = _trimmed_ellipse_spec(item)
                if espec is not None:
                    ellipses.append(espec)
                else:
                    tv, te = _trimmed_conic_flat(item)
                    if tv:
                        tb = len(verts) // 3
                        verts.extend(tv)
                        edges.extend([tb+i for i in te])
            else:
                tv, te = _trimmed_conic_flat(item)
                if tv:
                    tb = len(verts) // 3
                    verts.extend(tv)
                    edges.extend([tb+i for i in te])

        elif item.is_a("IfcCircle"):
            c2d = item.Position.Location.Coordinates
            circles.append((float(c2d[0]), float(c2d[1]), float(item.Radius)))

    if mapping_target is not None:
        if verts:
            verts = _apply_cart_transform_op(verts, mapping_target)
        arcs     = [_apply_arc_spec(s, mapping_target) for s in arcs]
        circles  = [_apply_arc_spec(s, mapping_target) for s in circles]
        ellipses = [_apply_ellipse_spec(s, mapping_target) for s in ellipses]

    return verts, edges, arcs, circles, ellipses


def _extract_local_curves(element, plan_repr):
    """Extract plan curves in element-local coords. Returns (verts, edges, arcs, circles, ellipses)."""
    import ifcopenshell.geom as geom
    items = plan_repr.Items
    if items:
        v, e, a, c, el = _extract_curves_from_items(list(items))
        if v or e or a or c or el:
            return v, e, a, c, el
    ctx_id = plan_repr.ContextOfItems.id()
    s = geom.settings()
    s.set('use-world-coords', False)
    s.set('context-ids', [ctx_id])
    shape = geom.create_shape(s, element)
    return list(shape.geometry.verts), list(shape.geometry.edges), [], [], []


# ---------------------------------------------------------------------------
# Wall profile extraction helpers
# ---------------------------------------------------------------------------


def _profile_to_pts_2d(profile):
    if profile.is_a("IfcRectangleProfileDef"):
        x, y = float(profile.XDim)/2, float(profile.YDim)/2
        return [(-x,-y),(x,-y),(x,y),(-x,y)]
    if profile.is_a("IfcArbitraryClosedProfileDef"):
        curve = profile.OuterCurve
        if curve.is_a("IfcPolyline"):
            pts = [(float(p.Coordinates[0]), float(p.Coordinates[1])) for p in curve.Points]
            if len(pts) > 1 and pts[0] == pts[-1]: pts = pts[:-1]
            return pts if len(pts) >= 3 else None
        if curve.is_a("IfcIndexedPolyCurve"):
            pts = [(float(c[0]), float(c[1])) for c in curve.Points.CoordList]
            if len(pts) > 1 and pts[0] == pts[-1]: pts = pts[:-1]
            return pts if len(pts) >= 3 else None
    if profile.is_a("IfcCircleProfileDef"):
        r = float(profile.Radius)
        return [(r*math.cos(2*math.pi*i/32), r*math.sin(2*math.pi*i/32)) for i in range(32)]
    return None


def _apply_axis2placement3d(pts_2d, placement):
    pts = np.array(pts_2d, dtype=float)
    if placement is None:
        return np.hstack([pts, np.zeros((len(pts),1))])
    loc = placement.Location.Coordinates
    origin = np.array([float(loc[0]), float(loc[1]),
                       float(loc[2]) if len(loc) > 2 else 0.0])
    x_ax = np.array([float(v) for v in (placement.RefDirection.DirectionRatios
                     if placement.RefDirection else [1,0,0])])
    x_ax = x_ax[:3] / (np.linalg.norm(x_ax[:3]) or 1.0)
    z_ax = np.array([float(v) for v in (placement.Axis.DirectionRatios
                     if placement.Axis else [0,0,1])])
    z_ax = z_ax[:3] / (np.linalg.norm(z_ax[:3]) or 1.0)
    y_ax = np.cross(z_ax, x_ax)
    n = np.linalg.norm(y_ax)
    y_ax = y_ax/n if n > 1e-9 else np.array([0.,1.,0.])
    return origin + pts[:,0:1]*x_ax + pts[:,1:2]*y_ax


def _extrusion_parallel_to_camera(item, wm_flat, camera_dir):
    if not item.is_a("IfcExtrudedAreaSolid"):
        return False
    ed = item.ExtrudedDirection.DirectionRatios
    local_dir = np.array([float(ed[0]), float(ed[1]),
                          float(ed[2]) if len(ed) > 2 else 0.0])
    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    world_dir = world_m[:3,:3] @ local_dir
    n = np.linalg.norm(world_dir)
    if n < 1e-9: return False
    cam = np.array(camera_dir, dtype=float)
    cn = np.linalg.norm(cam)
    if cn < 1e-9: return False
    return abs(float(np.dot(world_dir/n, cam/cn))) > 0.966


def _extruded_plan_polygon(item, wm_flat, cam_inv_np, camera_dir):
    depth = 0
    while item.is_a("IfcBooleanClippingResult") or item.is_a("IfcBooleanResult"):
        item = item.FirstOperand; depth += 1
        if depth > 8: return None
    if not item.is_a("IfcExtrudedAreaSolid"): return None
    if not _extrusion_parallel_to_camera(item, wm_flat, camera_dir): return None
    pts_2d = _profile_to_pts_2d(item.SweptArea)
    if not pts_2d: return None
    pts_3d = _apply_axis2placement3d(pts_2d, item.Position)
    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    combined = cam_inv_np @ world_m
    pts_h = np.hstack([pts_3d, np.ones((len(pts_3d),1))])
    pts_draw = (combined @ pts_h.T).T[:,:2]
    try:
        poly = shapely.Polygon(pts_draw.tolist())
        if not poly.is_valid: poly = poly.buffer(0)
        return poly if poly.area > 1e-6 else None
    except Exception:
        return None


def _wall_profile_polygon(element, wm_flat, cam_inv_np, camera_dir):
    if not hasattr(element,'Representation') or element.Representation is None:
        return None
    for repr_ in element.Representation.Representations:
        for item in repr_.Items:
            poly = _extruded_plan_polygon(item, wm_flat, cam_inv_np, camera_dir)
            if poly is not None: return poly
    return None


def _opening_footprint_polygon(opening, cam_inv_np):
    import ifcopenshell.geom as geom
    try:
        s = geom.settings()
        s.set('use-world-coords', True)
        shape = geom.create_shape(s, opening)
        verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
        z_min, z_max = float(verts[:,2].min()), float(verts[:,2].max())
        pts_2d = (cam_inv_np @ np.hstack([verts, np.ones((len(verts),1))]).T).T[:,:2]
        hull = shapely.convex_hull(shapely.MultiPoint(pts_2d.tolist()))
        if hull.geom_type == 'Polygon' and hull.area > 1e-6:
            return hull, z_min, z_max
    except Exception:
        pass
    return None, None, None


def _wall_z_range(element, wm_flat):
    if not hasattr(element,'Representation') or element.Representation is None:
        return None, None
    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    for repr_ in element.Representation.Representations:
        for item in repr_.Items:
            cur, d = item, 0
            while cur.is_a("IfcBooleanClippingResult") or cur.is_a("IfcBooleanResult"):
                cur = cur.FirstOperand; d += 1
                if d > 8: break
            if not cur.is_a("IfcExtrudedAreaSolid"): continue
            pos_z = float(cur.Position.Location.Coordinates[2]) \
                    if cur.Position and cur.Position.Location and len(cur.Position.Location.Coordinates) > 2 \
                    else 0.0
            ed = cur.ExtrudedDirection.DirectionRatios
            local_dir = np.array([float(ed[0]), float(ed[1]),
                                   float(ed[2]) if len(ed) > 2 else 0.0])
            dz = float((world_m[:3,:3] @ local_dir)[2]) * float(cur.Depth)
            z_base = float((world_m @ np.array([0.,0.,pos_z,1.]))[2])
            return min(z_base, z_base+dz), max(z_base, z_base+dz)
    return None, None


def _extract_wall_polygon_with_openings(element, wm_flat, cam_inv_np, cut_z, camera_dir):
    wall_poly = _wall_profile_polygon(element, wm_flat, cam_inv_np, camera_dir)
    if wall_poly is None or wall_poly.area < 1e-4:
        return None, True
    z_min, z_max = _wall_z_range(element, wm_flat)
    is_section = True if z_min is None else (z_min <= cut_z <= z_max)
    opening_polys = []
    for rel in getattr(element, 'HasOpenings', []):
        op = rel.RelatedOpeningElement
        if not hasattr(op, 'ObjectPlacement') or op.ObjectPlacement is None:
            continue
        op_poly, _zmin, _zmax = _opening_footprint_polygon(op, cam_inv_np)
        if op_poly is not None and op_poly.area > 1e-6:
            opening_polys.append(op_poly)
    if opening_polys:
        try:
            result = wall_poly.difference(shapely.ops.unary_union(opening_polys))
            if not result.is_empty and result.area > 1e-6:
                wall_poly = result
        except Exception:
            pass
    return wall_poly, is_section


# ---------------------------------------------------------------------------
# DXF writing helpers
# ---------------------------------------------------------------------------

_DXF_LW = (0,5,9,13,15,18,20,25,30,35,40,50,53,60,70,80,90,100,106,120,140,158,200,211)


def _snap_lw(mm):
    hundredths = round(float(mm) * 100)
    return min(_DXF_LW, key=lambda v: abs(v - hundredths))


def _load_layer_styles(styles_path):
    if not os.path.isfile(styles_path):
        return []
    try:
        with open(styles_path, encoding="utf-8") as f:
            data = json.load(f)
        return [(k, int(v.get("color",7)), _snap_lw(v.get("lineweight",0.25)),
                 str(v.get("linetype","Continuous")))
                for k, v in data.items() if not k.startswith("_")]
    except Exception:
        return []


def _slab_footprint_world(elem, wm_flat):
    """Return Shapely Polygon of slab footprint in world XY, or None."""
    if not hasattr(elem, 'Representation') or elem.Representation is None:
        return None
    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    for repr_ in elem.Representation.Representations:
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
            pts_2d = _profile_to_pts_2d(cur.SweptArea)
            if not pts_2d:
                continue
            pts_3d = _apply_axis2placement3d(pts_2d, cur.Position)
            pts_h = np.hstack([pts_3d, np.ones((len(pts_3d), 1))])
            pts_world = (world_m @ pts_h.T).T[:, :2]
            try:
                poly = shapely.Polygon(pts_world.tolist())
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.area > 1e-4:
                    return poly
            except Exception:
                continue
    return None


def _compute_floor_slabs(ifc, cut_z):
    """Return list of (z_top, Shapely Polygon) for IfcSlab/IfcCovering(FLOOR) below cut_z.

    Only slabs whose footprint can be extracted as a polygon are included.
    """
    slabs = []
    for cls in ("IfcSlab", "IfcCovering"):
        for elem in ifc.by_type(cls):
            if cls == "IfcCovering":
                if getattr(elem, "PredefinedType", None) != "FLOOR":
                    continue
            try:
                wm = _world_matrix_flat(elem)
                _, z_max = _wall_z_range(elem, wm)
                if z_max is None or z_max > cut_z:
                    continue
                poly = _slab_footprint_world(elem, wm)
                if poly is not None:
                    slabs.append((z_max, poly))
            except Exception:
                pass
    return slabs


def _is_occluded_by_slab(x_world, y_world, z_origin, floor_slabs):
    """Return True if (x_world, y_world) at z_origin lies under any floor slab.

    Combines Z check (element below slab top) with XY footprint containment.
    Elements outside all slab footprints are never excluded.
    """
    pt = shapely.Point(x_world, y_world)
    for z_top, poly in floor_slabs:
        if z_origin < z_top - 1e-3 and poly.covers(pt):
            return True
    return False


def _project_local_to_lines(verts, edges, cam_R):
    n = len(verts) // 3
    if n == 0 or len(edges) < 2:
        return []
    va = np.array(verts[:n*3], dtype=float).reshape(n, 3)
    pts = (cam_R @ va.T).T[:, :2]
    lines = []
    for k in range(0, len(edges)-1, 2):
        i, j = int(edges[k]), int(edges[k+1])
        p0 = (float(pts[i,0]), float(pts[i,1]))
        p1 = (float(pts[j,0]), float(pts[j,1]))
        if (p0[0]-p1[0])**2 + (p0[1]-p1[1])**2 > 1e-18:
            lines.append((p0, p1))
    return lines


def _compute_insert(wm_flat, cam_inv_np):
    wm = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    pos_h = cam_inv_np @ (wm @ np.array([0.,0.,0.,1.]))
    lx = wm[:3,:3] @ np.array([1.,0.,0.])
    rot = float(np.degrees(np.arctan2(float(lx[1]), float(lx[0]))))
    return (float(pos_h[0]), float(pos_h[1])), rot


def _setup_dxf_layers(doc, layer_styles):
    for name, color, lw, linetype in layer_styles:
        try:
            layer = doc.layers.get(name)
        except Exception:
            layer = doc.layers.add(name)
        layer.color = color
        layer.lineweight = lw
        try:
            layer.dxf.linetype = linetype
        except Exception:
            pass


from collections import namedtuple as _namedtuple

_ElementRecord = _namedtuple("_ElementRecord", [
    "element", "bucket", "layer", "plan_repr", "from_type",
])


def _classify_elements(elements, cut_z, cam_inv_np, cam_dir, target_view,
                       floor_slabs, section_classes):
    """Classify every element into a bucket and assign its DXF layer.

    All classification rules live here; no geometry is extracted.
    Returns list[_ElementRecord].

    Buckets:
      B — section classes (walls …): layer = IfcWall_Section / _View
      A — 2D native repr found: layer = IfcWindow / IfcWindow_Overhead / …
      C — no usable repr or occluded: skipped
    """
    records      = []
    overhead_ids = set()

    # Pass 1: section classes → Bucket B, collect overhead fill IDs
    for elem in elements:
        cls = elem.is_a()
        if cls not in section_classes:
            continue
        wm = _world_matrix_flat(elem)
        z_min, z_max = _wall_z_range(elem, wm)
        is_cut = z_min is None or (z_min <= cut_z <= z_max)
        layer  = f"{cls}_Section" if is_cut else f"{cls}_View"

        for rel in getattr(elem, 'HasOpenings', []):
            op = rel.RelatedOpeningElement
            if not hasattr(op, 'ObjectPlacement') or op.ObjectPlacement is None:
                continue
            wm_op = _world_matrix_flat(op)
            z_min_op, _ = _wall_z_range(op, wm_op)
            if z_min_op is None:
                z_min_op = float(wm_op[14])
            if z_min_op > cut_z + 1e-3:
                for fill_rel in getattr(op, 'HasFillings', []):
                    filling = getattr(fill_rel, 'RelatedBuildingElement', None)
                    if filling is not None:
                        overhead_ids.add(filling.id())

        records.append(_ElementRecord(elem, "B", layer, None, False))

    # Pass 2: everything else → Bucket A or C
    for elem in elements:
        cls = elem.is_a()
        if cls in section_classes:
            continue
        wm = _world_matrix_flat(elem)

        if floor_slabs:
            x_w, y_w, z_w = float(wm[12]), float(wm[13]), float(wm[14])
            if _is_occluded_by_slab(x_w, y_w, z_w, floor_slabs):
                records.append(_ElementRecord(elem, "C", cls, None, False))
                continue

        plan_repr, from_type = _find_plan_repr(elem, target_view)
        if plan_repr is not None:
            layer = f"{cls}_Overhead" if elem.id() in overhead_ids else cls
            records.append(_ElementRecord(elem, "A", layer, plan_repr, from_type))
        else:
            records.append(_ElementRecord(elem, "C", cls, None, False))

    return records


def _parse_scale_factor(scale_str):
    """Parse EPset_Drawing 'Scale' (e.g. '1/100') → scale factor 0.01."""
    if not scale_str:
        return None
    from fractions import Fraction
    try:
        f = Fraction(str(scale_str))
        if f > 0:
            return float(f)
    except (ValueError, ZeroDivisionError):
        pass
    return None


def _write_dxf(output_path, block_defs, block_order, block_inserts,
               flat_edges, wall_polys_by_key,
               template_path=None, dxf_version="R2010", scale_factor=0.01):
    import ezdxf
    from ezdxf import units

    SNAP_TOL = 0.0005
    _BB = {"layer": "0", "color": 0, "linetype": "BYBLOCK", "lineweight": -2}

    if template_path and os.path.isfile(template_path):
        doc = ezdxf.readfile(template_path)
        msp = doc.modelspace()
        msp.delete_all_entities()
    else:
        doc = ezdxf.new(dxf_version)
        doc.units = units.M
        msp = doc.modelspace()

    doc.header["$LTSCALE"] = float(scale_factor)

    for block_name in block_order:
        bd  = block_defs[block_name]
        blk = doc.blocks.new(name=block_name)
        blk.block.dxf.description = bd.get("globalid", "")
        for p0, p1 in bd["lines"]:
            blk.add_line(p0, p1, dxfattribs=_BB)
        for cx, cy, r, a_s, a_e in bd["arcs"]:
            blk.add_arc((cx, cy), r, a_s, a_e, dxfattribs=_BB)
        for cx, cy, r in bd["circles"]:
            blk.add_circle((cx, cy), r, dxfattribs=_BB)
        for cx, cy, maj_x, maj_y, ratio, t1, t2 in bd.get("ellipses", []):
            blk.add_ellipse(
                center=(cx, cy, 0),
                major_axis=(maj_x, maj_y, 0),
                ratio=ratio,
                start_param=t1,
                end_param=t2,
                dxfattribs=_BB,
            )
        for pos, rot, layer in block_inserts.get(block_name, []):
            msp.add_blockref(block_name, pos,
                             dxfattribs={"rotation": rot, "layer": layer})

    for p0, p1, layer in flat_edges:
        msp.add_line(p0, p1, dxfattribs={"layer": layer})

    wall_geom_groups = []
    for (ifc_class, material, layer), polys in wall_polys_by_key.items():
        if not polys: continue
        try:
            expanded = [p.buffer(SNAP_TOL, join_style=2) for p in polys]
            merged   = shapely.ops.unary_union(expanded).buffer(-SNAP_TOL, join_style=2)
        except Exception:
            merged = polys[0] if len(polys) == 1 else None
        if merged is None: continue
        is_section    = layer.endswith("_Section")
        outline_layer = layer
        hatch_layer   = f"{ifc_class}_Hatches"
        geoms = list(merged.geoms) if merged.geom_type == 'MultiPolygon' else [merged]
        wall_geom_groups.append((outline_layer, hatch_layer, is_section, geoms))

    for outline_layer, hatch_layer, is_section, geoms in wall_geom_groups:
        if not is_section: continue
        for poly in geoms:
            if poly.geom_type != 'Polygon': continue
            exterior = [(float(x), float(y)) for x, y in poly.exterior.coords[:-1]]
            holes    = [[(float(x), float(y)) for x, y in ring.coords[:-1]]
                        for ring in poly.interiors]
            if len(exterior) < 3: continue
            hatch = msp.add_hatch(dxfattribs={"layer": hatch_layer, "color": 256})
            hatch.set_solid_fill(color=256)
            hatch.paths.add_polyline_path(exterior, is_closed=True, flags=1)
            for hole in holes:
                hatch.paths.add_polyline_path(hole, is_closed=True, flags=16)

    for outline_layer, hatch_layer, is_section, geoms in wall_geom_groups:
        for poly in geoms:
            if poly.geom_type != 'Polygon': continue
            exterior = [(float(x), float(y)) for x, y in poly.exterior.coords[:-1]]
            holes    = [[(float(x), float(y)) for x, y in ring.coords[:-1]]
                        for ring in poly.interiors]
            msp.add_lwpolyline(exterior, dxfattribs={"closed": True, "layer": outline_layer})
            for hole in holes:
                msp.add_lwpolyline(hole, dxfattribs={"closed": True, "layer": outline_layer})

    xs, ys = [], []
    for _, _, _, geoms in wall_geom_groups:
        for poly in geoms:
            if poly.geom_type == 'Polygon':
                xs.extend(x for x, y in poly.exterior.coords)
                ys.extend(y for x, y in poly.exterior.coords)
    for inserts in block_inserts.values():
        for pos, _rot, _layer in inserts:
            xs.append(float(pos[0])); ys.append(float(pos[1]))
    if xs and ys:
        pad = max((max(xs) - min(xs)) * 0.05, (max(ys) - min(ys)) * 0.05, 0.5)
        xmin, xmax = min(xs) - pad, max(xs) + pad
        ymin, ymax = min(ys) - pad, max(ys) + pad
        doc.header["$EXTMIN"] = (xmin, ymin, 0)
        doc.header["$EXTMAX"] = (xmax, ymax, 0)
        cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
        doc.set_modelspace_vport(height=ymax - ymin, center=(cx, cy))

    doc.saveas(output_path)


# ---------------------------------------------------------------------------
# Blender operator
# ---------------------------------------------------------------------------

class ExportDrawingToDxfOperator(bpy.types.Operator):
    """Export the active Bonsai drawing to DXF (ezdxf, pure Python)."""

    bl_idname = "bim.export_drawing_to_dxf"
    bl_label  = "Export Drawing to DXF"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    dxf_version: bpy.props.EnumProperty(
        name="DXF Version",
        items=[
            ("R2010", "R2010 (AC1024)", ""),
            ("R2013", "R2013 (AC1027)", ""),
            ("R2018", "R2018 (AC1032)", ""),
        ],
        default="R2010",
    )

    @classmethod
    def poll(cls, context):
        return _get_ifc() is not None and _get_active_drawing() is not None

    def invoke(self, context, event):
        ifc_path = bpy.data.filepath or ""
        self.filepath = os.path.join(os.path.dirname(ifc_path) if ifc_path else "",
                                     "drawing.dxf")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        drawing = _get_active_drawing()
        if drawing is None:
            self.report({"ERROR"}, "No active Bonsai drawing found.")
            return {"CANCELLED"}

        camera_obj = _get_camera_obj(drawing)
        if camera_obj is None:
            self.report({"ERROR"}, "Drawing has no camera object.")
            return {"CANCELLED"}

        output_path = bpy.path.abspath(self.filepath)
        if not output_path.lower().endswith((".dxf", ".dwg")):
            output_path += ".dxf"

        try:
            col_major, cam_dir, cam_pos = _camera_matrices(camera_obj)
        except Exception as exc:
            self.report({"ERROR"}, f"Camera matrix error: {exc}")
            return {"CANCELLED"}

        _cam_inv_np  = np.array(col_major, dtype=float).reshape(4, 4, order='F')
        _cam_R       = _cam_inv_np[:3, :3]
        _cam_x_proj  = _cam_R @ np.array([1., 0., 0.])
        _cam_rot_deg = float(np.degrees(np.arctan2(float(_cam_x_proj[1]),
                                                    float(_cam_x_proj[0]))))
        target_view  = _get_target_view(drawing)
        wall_mode    = "shapely" if _SHAPELY else "flat"
        cut_z        = cam_pos[2]

        try:
            import ifcopenshell.util.element as _ifc_elem
            _pset = _ifc_elem.get_psets(drawing).get("EPset_Drawing", {})
            _scale_str = _pset.get("Scale", "")
        except Exception:
            _scale_str = ""
        scale_factor = _parse_scale_factor(_scale_str) or 0.01

        import bpy as _bpy
        _tpl = getattr(getattr(_bpy.context.scene, "ifc_dxf", None), "template_path", "") or ""
        if _tpl:
            template_path = _bpy.path.abspath(_tpl)
        else:
            from . import get_template_path
            template_path = get_template_path() or ""

        try:
            from bonsai import tool
            elements = tool.Drawing.get_drawing_elements(drawing)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not get drawing elements: {exc}")
            return {"CANCELLED"}

        _SECTION_CLASSES = frozenset({"IfcWall", "IfcWallStandardCase"})

        ifc       = _get_ifc()
        floor_slabs = _compute_floor_slabs(ifc, cut_z) if _SHAPELY else []

        # Re-add fill elements above the frustum: openings entirely above cut_z
        # are overhead → their fillings need to appear on _Overhead layers but
        # may have been excluded by Bonsai's camera-view culling (frustum Z_max == cut_z).
        element_ids = {e.id() for e in elements}
        overhead_extras = set()
        for elem in list(elements):
            if elem.is_a() not in _SECTION_CLASSES:
                continue
            for rel in getattr(elem, 'HasOpenings', []):
                op = rel.RelatedOpeningElement
                if not hasattr(op, 'ObjectPlacement') or op.ObjectPlacement is None:
                    continue
                wm_op = _world_matrix_flat(op)
                z_min_op, _ = _wall_z_range(op, wm_op)
                if z_min_op is None:
                    z_min_op = float(wm_op[14])
                if z_min_op > cut_z + 1e-3:
                    for fill_rel in getattr(op, 'HasFillings', []):
                        filling = getattr(fill_rel, 'RelatedBuildingElement', None)
                        if filling is not None and filling.id() not in element_ids:
                            overhead_extras.add(filling)
        if overhead_extras:
            elements = set(elements) | overhead_extras
            print(f"  Overhead+  : re-added {len(overhead_extras)} fill elements above frustum")

        block_defs    = {}
        block_order   = []
        block_inserts = {}   # name → [(pos_2d, rot_deg, layer), ...]
        flat_edges    = []
        wall_polys_by_key = {}  # (ifc_class, material, layer) → [Polygon, ...]
        seen_blocks   = {}

        records = _classify_elements(elements, cut_z, _cam_inv_np, cam_dir,
                                     target_view, floor_slabs, _SECTION_CLASSES)

        n_overhead = sum(1 for r in records if r.bucket == "A" and r.layer.endswith("_Overhead"))
        if n_overhead:
            print(f"  Overhead   : {n_overhead} elements fill openings above cut plane")

        # ── Bucket B ──────────────────────────────────────────────────────────
        for rec in (r for r in records if r.bucket == "B"):
            element   = rec.element
            ifc_class = element.is_a()
            material  = _get_material_name(element)
            wm        = _world_matrix_flat(element)
            processed = False

            if wall_mode == "shapely":
                try:
                    poly, _ = _extract_wall_polygon_with_openings(
                        element, wm, _cam_inv_np, cut_z, cam_dir)
                    if poly is not None:
                        wall_polys_by_key.setdefault(
                            (ifc_class, material, rec.layer), []).append(poly)
                        processed = True
                except Exception:
                    pass
            else:
                plan_repr_b, _ = _find_plan_repr(element, target_view)
                if plan_repr_b is not None:
                    try:
                        verts, edges, _a, _c, _el = _extract_local_curves(element, plan_repr_b)
                        if verts and edges:
                            wm_np = np.array(wm, dtype=float).reshape(4, 4, order='F')
                            n  = len(verts) // 3
                            va = np.array(verts[:n*3]).reshape(n, 3)
                            pd = (_cam_inv_np @ np.hstack([va, np.ones((n,1))]).T).T[:, :2]
                            for k in range(0, len(edges)-1, 2):
                                i, j = int(edges[k]), int(edges[k+1])
                                p0 = (float(pd[i,0]), float(pd[i,1]))
                                p1 = (float(pd[j,0]), float(pd[j,1]))
                                if (p0[0]-p1[0])**2+(p0[1]-p1[1])**2 > 1e-18:
                                    flat_edges.append((p0, p1, rec.layer))
                            processed = True
                    except Exception:
                        pass

        # ── Bucket A ──────────────────────────────────────────────────────────
        for rec in (r for r in records if r.bucket == "A"):
            element   = rec.element
            ifc_class = element.is_a()
            material  = _get_material_name(element)
            gid       = element.GlobalId
            wm        = _world_matrix_flat(element)

            try:
                ifc_type, block_name = _get_type_block_name(element)
                if block_name is None:
                    block_name = f"{ifc_class}_{gid[:8]}"
                    block_gid  = gid
                else:
                    block_gid  = ifc_type.GlobalId

                if block_name not in seen_blocks:
                    verts, edges, arcs, circles, ellipses = _extract_local_curves(
                        element, rec.plan_repr)
                    if verts or edges or arcs or circles or ellipses:
                        lines = _project_local_to_lines(verts or [], edges or [], _cam_R)
                        arcs_blk = []
                        for cx_e, cy_e, r, a_s, a_e in arcs:
                            c = _cam_R @ np.array([cx_e, cy_e, 0.])
                            arcs_blk.append((float(c[0]), float(c[1]), r,
                                             (a_s+_cam_rot_deg)%360,
                                             (a_e+_cam_rot_deg)%360))
                        circles_blk = [
                            (float((_cam_R @ np.array([cx,cy,0.]))[0]),
                             float((_cam_R @ np.array([cx,cy,0.]))[1]), r)
                            for cx, cy, r in circles
                        ]
                        ellipses_blk = []
                        for cx, cy, maj_x, maj_y, ratio, t1, t2 in ellipses:
                            c   = _cam_R @ np.array([cx, cy, 0.])
                            maj = _cam_R @ np.array([maj_x, maj_y, 0.])
                            ellipses_blk.append((float(c[0]), float(c[1]),
                                                  float(maj[0]), float(maj[1]),
                                                  ratio, t1, t2))
                        block_defs[block_name] = {
                            "ifc_class": ifc_class, "material": material,
                            "lines": lines, "arcs": arcs_blk,
                            "circles": circles_blk, "ellipses": ellipses_blk,
                            "globalid": block_gid,
                        }
                        block_order.append(block_name)
                        seen_blocks[block_name] = True

                if block_name in seen_blocks:
                    pos, rot = _compute_insert(wm, _cam_inv_np)
                    block_inserts.setdefault(block_name, []).append((pos, rot, rec.layer))
            except Exception:
                pass

        # ── Bucket C: counted only ─────────────────────────────────────────

        try:
            _write_dxf(output_path, block_defs, block_order, block_inserts,
                       flat_edges, wall_polys_by_key,
                       template_path=template_path,
                       dxf_version=self.dxf_version, scale_factor=scale_factor)
        except Exception as exc:
            self.report({"ERROR"}, f"DXF export failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported to {output_path}")
        return {"FINISHED"}


class IfcDxfProperties(bpy.types.PropertyGroup):
    template_path: bpy.props.StringProperty(
        name="DXF Template",
        description="Path to the ifc_dxf_template.dxf used as base for exports",
        subtype="FILE_PATH",
        default="",
    )


classes = [IfcDxfProperties, ExportDrawingToDxfOperator]
