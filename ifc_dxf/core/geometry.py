"""Curve/BRep/wall-polygon/slab geometry extraction.

Depends on: camera.py (world_matrix_col_major, _wall_z_range is defined here
but depends on numpy only).
"""

import math

import numpy as np
import ifcopenshell.geom

try:
    import shapely
    import shapely.ops
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

from .camera import world_matrix_col_major

# Maximum nesting depth when unwrapping IfcBooleanResult chains to reach the
# base IfcExtrudedAreaSolid.  Each level typically corresponds to one opening
# subtracted from the wall body.  Real-world walls rarely exceed ~20; 64 gives
# ample headroom while guarding against corrupt/circular data.
BOOLEAN_UNWRAP_DEPTH_LIMIT = 64

# Dihedral-angle threshold for "feature edge" extraction from tessellated
# surfaces (IfcPolygonalFaceSet, IfcTriangulatedFaceSet).  Edges shared by two
# faces whose normals differ by more than this angle are drawn; flatter edges
# are discarded.  Naked (perimeter) edges are always drawn regardless.
# 10° retains ridges and valleys while suppressing nearly-flat triangulation.
MESH_CREASE_ANGLE_DEG = 15.0


# ---------------------------------------------------------------------------
# IFC curve extraction helpers
# ---------------------------------------------------------------------------

def _arc_3pts_spec(p1, p2, p3):
    """Return (cx, cy, r, dxf_start_deg, dxf_end_deg) for an arc through 3 2D points.

    dxf_start/end are in degrees [0,360) for a DXF ARC entity that goes CCW from
    start to end.  Returns None if the three points are collinear.
    """
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
        # CW arc -> swap so DXF draws the same arc CCW from p3 to p1
        return (ux, uy, r, a2_raw, a1_raw)


def _trimmed_conic_spec(item):
    """Return arc or circle spec for IfcTrimmedCurve with IfcCircle / IfcEllipse basis.

    Return value:
      (cx, cy, r, dxf_start, dxf_end) -- arc spec  (DXF ARC goes CCW start->end)
      (cx, cy, r)                      -- full circle spec
      None                             -- IfcEllipse with unequal axes (caller tessellates)
    """
    basis = item.BasisCurve
    if basis.is_a("IfcCircle"):
        r = ra = rb = float(basis.Radius)
    elif basis.is_a("IfcEllipse"):
        ra = float(basis.SemiAxis1)
        rb = float(basis.SemiAxis2)
        if abs(ra - rb) > 0.001 * max(ra, rb):
            return None  # true ellipse -- caller uses tessellation fallback
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

    # Prefer CartesianPoint (unambiguous); fallback to ParameterValue.
    # Do NOT use `or` -- 0.0 deg is falsy but valid.
    a1 = pt_to_param_deg(item.Trim1)
    if a1 is None:
        a1 = val_to_param_deg(item.Trim1)
    a2 = pt_to_param_deg(item.Trim2)
    if a2 is None:
        a2 = val_to_param_deg(item.Trim2)
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


def _trimmed_ellipse_spec(item):
    """Return DXF ELLIPSE spec for IfcTrimmedCurve with IfcEllipse (unequal axes).

    Returns (cx, cy, maj_x, maj_y, ratio, t1_rad, t2_rad) or None.
    DXF ELLIPSE draws CCW from t1_rad to t2_rad in the major-axis frame.

    NOTE: Bonsai exports door swings as IfcEllipse even when ra~rb (both axes
    differ only due to door thickness). This should be reported upstream as a
    bug -- door arcs should be IfcCircle. This function is a workaround.
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

    # DXF requires ratio = minor/major <= 1; choose larger axis as major
    if ra >= rb:
        major = ra;  minor = rb
        major_dir = xax;             minor_dir = yax
    else:
        major = rb;  minor = ra
        major_dir = yax;             minor_dir = [-xax[0], -xax[1]]  # rotate_ccw(yax) = -xax

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
        """IFC ParameterValue (degrees) -> DXF ellipse param (radians)."""
        for t in trims:
            if not hasattr(t, 'Coordinates'):
                v = math.radians(float(t))
                # IFC param v: P = ra*cos(v)*xax + rb*sin(v)*yax
                if ra >= rb:
                    return v  # DXF major=xax, same parameterization
                else:
                    # DXF major=yax, minor=-xax:
                    # cos(t)=sin(v), sin(t)=-cos(v) => t=atan2(-cos(v), sin(v))
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
        # CCW from t1 to t2; ensure end > start
        if t2 <= t1:
            t2 += 2 * math.pi
    else:
        # CW in IFC -> swap for DXF CCW
        t1, t2 = t2, t1
        if t2 <= t1:
            t2 += 2 * math.pi

    return (cx, cy, major_dir[0]*major, major_dir[1]*major, ratio, t1, t2)


def _trimmed_conic_flat(item, n_seg=16):
    """Tessellated fallback for IfcTrimmedCurve (used only for true IfcEllipse)."""
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


def _apply_ellipse_spec(spec, op):
    """Apply IfcCartesianTransformationOperator to a DXF ellipse spec.

    spec = (cx, cy, maj_x, maj_y, ratio, t1_rad, t2_rad)
    """
    if op is None or spec is None:
        return spec
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

    cx, cy, maj_x, maj_y, ratio, t1, t2 = spec
    new_cx  = ox + (cx*xa[0] + cy*ya[0]) * sc
    new_cy  = oy + (cx*xa[1] + cy*ya[1]) * sc
    new_mjx = (maj_x*xa[0] + maj_y*ya[0]) * sc
    new_mjy = (maj_x*xa[1] + maj_y*ya[1]) * sc
    return (new_cx, new_cy, new_mjx, new_mjy, ratio, t1, t2)


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


def _composite_curve_seg_endpoints(seg):
    """Return (start_pt, end_pt) of an IfcCompositeCurveSegment as [x,y] lists, or (None,None)."""
    curve = seg.ParentCurve
    sense = seg.SenseAgreement  # True = natural direction

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
        # TrimmedCurve.SenseAgreement tells the traversal direction of the basis curve;
        # it does NOT swap the meaning of Trim1/Trim2 as endpoints.
        return _rev(p0, p1)

    return None, None


def _extract_curves_from_items(items, mapping_target=None):
    """Walk IFC shape items, return (verts_flat, edges_flat, arcs, circles, ellipses).

    arcs:     [(cx, cy, r, dxf_start_deg, dxf_end_deg), ...]  -- true DXF ARC specs
    circles:  [(cx, cy, r), ...]                               -- true DXF CIRCLE specs
    ellipses: [(cx, cy, maj_x, maj_y, ratio, t1_rad, t2_rad)] -- true DXF ELLIPSE specs
    """
    verts    = []   # flat [x0,y0,z0, ...]
    edges    = []   # flat [i0,j0, ...]
    arcs     = []   # arc specs
    circles  = []   # circle specs
    ellipses = []   # ellipse specs

    def _merge_sub(sub_v, sub_e, sub_arcs, sub_circles, sub_ellipses):
        if sub_v:
            base = len(verts) // 3
            verts.extend(sub_v)
            for k in range(0, len(sub_e), 2):
                edges.extend([sub_e[k] + base, sub_e[k+1] + base])
        arcs.extend(sub_arcs)
        circles.extend(sub_circles)
        ellipses.extend(sub_ellipses)

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
            # Collect segment endpoints first so bare IfcCircle segments
            # can find their arc boundaries from adjacent segments.
            segs = list(item.Segments)
            n_segs = len(segs)
            seg_eps = [_composite_curve_seg_endpoints(s) for s in segs]

            for idx, seg in enumerate(segs):
                curve = seg.ParentCurve
                sense = seg.SenseAgreement

                if curve.is_a("IfcCircle"):
                    # Arc endpoints come from the previous segment's end
                    # and the next segment's start.
                    prev_ep = seg_eps[(idx - 1) % n_segs][1]
                    next_sp = seg_eps[(idx + 1) % n_segs][0]

                    pos = curve.Position
                    loc = pos.Location.Coordinates
                    cx_c, cy_c = float(loc[0]), float(loc[1])
                    r_c = float(curve.Radius)

                    if prev_ep is not None and next_sp is not None:
                        a1c = math.degrees(math.atan2(
                            prev_ep[1] - cy_c, prev_ep[0] - cx_c)) % 360
                        a2c = math.degrees(math.atan2(
                            next_sp[1] - cy_c, next_sp[0] - cx_c)) % 360
                        if abs((a1c - a2c) % 360) < 0.01:
                            circles.append((cx_c, cy_c, r_c))
                        elif sense:
                            arcs.append((cx_c, cy_c, r_c, a1c, a2c))
                        else:
                            arcs.append((cx_c, cy_c, r_c, a2c, a1c))
                    else:
                        circles.append((cx_c, cy_c, r_c))
                else:
                    _merge_sub(*_extract_curves_from_items([curve]))

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
                        # else: collinear -> skip (degenerate arc)
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
            elif item.BasisCurve.is_a("IfcEllipse"):
                espec = _trimmed_ellipse_spec(item)
                if espec is not None:
                    ellipses.append(espec)
                else:
                    tv, te = _trimmed_conic_flat(item)
                    if tv:
                        tb = len(verts) // 3
                        verts.extend(tv)
                        edges.extend([tb + i for i in te])
            else:
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
        arcs     = [_apply_arc_spec(s, mapping_target) for s in arcs]
        circles  = [_apply_arc_spec(s, mapping_target) for s in circles]
        ellipses = [_apply_ellipse_spec(s, mapping_target) for s in ellipses]

    return verts, edges, arcs, circles, ellipses


def _extract_local_curves(element, plan_repr):
    """Extract plan curves in element-local coords.

    Returns (verts_flat, edges_flat, arcs, circles, ellipses).
    Tries manual item walking first; falls back to ifcopenshell geometry engine
    (which produces tessellated lines only, no arc/circle/ellipse specs).
    """
    items = plan_repr.Items
    if items:
        v, e, arcs, circles, ellipses = _extract_curves_from_items(list(items))
        if v or e or arcs or circles or ellipses:
            return v, e, arcs, circles, ellipses

    # Fallback: ifcopenshell geometry engine with the specific context
    ctx_id = plan_repr.ContextOfItems.id()
    s = ifcopenshell.geom.settings()
    s.set('use-world-coords', False)
    s.set('context-ids', [ctx_id])
    shape = ifcopenshell.geom.create_shape(s, element)
    g = shape.geometry
    verts = list(g.verts)
    faces = list(g.faces)

    if not faces:
        # 2D wire geometry (no faces) — return all edges as-is
        return verts, list(g.edges), [], [], []

    # 3D tessellated mesh: return only naked + crease edges (dihedral > threshold).
    # This avoids flooding the DXF with interior triangulation lines.
    from collections import defaultdict
    cos_t = math.cos(math.radians(MESH_CREASE_ANGLE_DEG))
    v_np = np.array(verts).reshape(-1, 3)
    f_np = np.array(faces).reshape(-1, 3)
    e0 = v_np[f_np[:, 1]] - v_np[f_np[:, 0]]
    e1 = v_np[f_np[:, 2]] - v_np[f_np[:, 0]]
    fn = np.cross(e0, e1)
    norms = np.linalg.norm(fn, axis=1, keepdims=True)
    fn = np.where(norms > 1e-12, fn / norms, np.array([[0.0, 0.0, 1.0]]))

    edge_faces = defaultdict(list)
    for fi, (a, b, c) in enumerate(f_np.tolist()):
        for i, j in ((a, b), (b, c), (c, a)):
            edge_faces[(min(i, j), max(i, j))].append(fi)

    sel = []
    for (i, j), fs in edge_faces.items():
        if len(fs) != 2 or np.dot(fn[fs[0]], fn[fs[1]]) < cos_t:
            sel += [i, j]
    return verts, sel, [], [], []


# ---------------------------------------------------------------------------
# Wall profile extraction helpers (IfcExtrudedAreaSolid -> 2D polygon)
# ---------------------------------------------------------------------------

def _profile_to_pts_2d(profile):
    """Return list of (x, y) from an IFC profile definition, or None."""
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
    """Apply IfcAxis2Placement3D to 2D profile points -> 3D element-local array."""
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


def _curve_to_pts_2d(curve):
    """Extract (x, y) list from IfcPolyline or IfcIndexedPolyCurve boundary."""
    if curve.is_a("IfcPolyline"):
        pts = [(float(p.Coordinates[0]), float(p.Coordinates[1])) for p in curve.Points]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        return pts if len(pts) >= 3 else None
    if curve.is_a("IfcIndexedPolyCurve"):
        cl = curve.Points.CoordList
        pts = [(float(c[0]), float(c[1])) for c in cl]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        return pts if len(pts) >= 3 else None
    return None


def _clip_polygon_2d_with_halfspace(poly, second_op, wm_flat, cam_inv_col_major):
    """Subtract an IfcHalfSpaceSolid or IfcPolygonalBoundedHalfSpace from poly (2D).

    The clipping plane normal is transformed to camera space: if its Z component
    is significant the plane is tilted relative to the view and the 2D
    approximation is skipped (projection and clipping do not commute for tilted
    planes).

    Called once per boolean node when walking the IfcBooleanClippingResult chain.
    Returns the clipped polygon, or the original if the operand is unsupported.
    """
    if not (second_op.is_a("IfcHalfSpaceSolid") or
            second_op.is_a("IfcPolygonalBoundedHalfSpace")):
        return poly

    base = second_op.BaseSurface
    if not base.is_a("IfcPlane"):
        return poly

    world_m  = np.array(wm_flat,          dtype=float).reshape(4, 4, order='F')
    cam_inv  = np.array(cam_inv_col_major, dtype=float).reshape(4, 4, order='F')
    combined = cam_inv @ world_m

    placement = base.Position
    if placement.Axis:
        ax = placement.Axis.DirectionRatios
        normal_l = np.array([float(ax[0]), float(ax[1]),
                              float(ax[2]) if len(ax) > 2 else 0.0, 0.0])
    else:
        normal_l = np.array([0.0, 0.0, 1.0, 0.0])

    normal_c = combined @ normal_l  # plane normal in camera space (direction, w=0)

    # Perpendicularity check: a vertical plane has normal_c[2] ≈ 0.
    # Skip tilted planes — the 2D clip would be incorrect.
    if abs(float(normal_c[2])) > 0.1:
        return poly

    if second_op.is_a("IfcPolygonalBoundedHalfSpace"):
        pts_2d = _curve_to_pts_2d(second_op.PolygonalBoundary)
        if pts_2d is None:
            return poly
        pts_3d = _apply_axis2placement3d(pts_2d, second_op.Position)
        pts_h  = np.hstack([pts_3d, np.ones((len(pts_3d), 1))])
        pts_c  = (combined @ pts_h.T).T[:, :2]
        try:
            clip = shapely.Polygon(pts_c.tolist())
            if not clip.is_valid:
                clip = clip.buffer(0)
            if not clip.exterior.is_ccw:
                clip = shapely.Polygon(list(clip.exterior.coords)[::-1])
            result = poly.difference(clip)
            if not result.is_empty and result.area > 1e-6:
                return result
        except Exception:
            pass
        return poly

    # IfcHalfSpaceSolid (unbounded): build a half-plane rectangle in camera 2D.
    loc = placement.Location.Coordinates
    origin_l = np.array([float(loc[0]), float(loc[1]),
                          float(loc[2]) if len(loc) > 2 else 0.0, 1.0])
    origin_c = combined @ origin_l
    nx, ny = float(normal_c[0]), float(normal_c[1])
    norm2d = math.sqrt(nx * nx + ny * ny)
    if norm2d < 1e-9:
        return poly
    nx, ny = nx / norm2d, ny / norm2d
    if second_op.AgreementFlag:
        nx, ny = -nx, -ny
    ox, oy = float(origin_c[0]), float(origin_c[1])
    BIG = 1e6
    px, py = -ny, nx  # perpendicular to normal in 2D
    p1 = (ox + px * BIG, oy + py * BIG)
    p2 = (ox - px * BIG, oy - py * BIG)
    p3 = (p2[0] + nx * BIG, p2[1] + ny * BIG)
    p4 = (p1[0] + nx * BIG, p1[1] + ny * BIG)
    try:
        clip = shapely.Polygon([p1, p2, p3, p4])
        result = poly.difference(clip)
        if not result.is_empty and result.area > 1e-6:
            return result
    except Exception:
        pass
    return poly


def _extrusion_parallel_to_camera(item, wm_flat, camera_dir):
    """Return True if item's ExtrudedDirection is within ~15 deg of camera_dir in world space."""
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

    return abs(float(np.dot(world_dir, cam))) > 0.966  # cos(15 deg)


def _extruded_plan_polygon(item, wm_flat, cam_inv_col_major, camera_dir):
    """Convert IfcExtrudedAreaSolid (or BooleanResult wrapping one) to plan 2D polygon.

    Rejects items whose extrusion direction is not sufficiently parallel to the
    camera (non-vertical walls, ramps, slabs at angle): caller should fall back
    to Bucket C / OCC for those cases.

    IfcBooleanClippingResult chains are unwrapped to collect clipping operands
    (IfcHalfSpaceSolid, IfcPolygonalBoundedHalfSpace); each clip is applied as
    a 2D Shapely difference after projecting to camera space.
    """
    # Walk the boolean chain: collect clipping nodes, reach the base extrusion.
    chain = []
    cur = item
    depth = 0
    while cur.is_a("IfcBooleanClippingResult") or cur.is_a("IfcBooleanResult"):
        chain.append(cur)
        cur = cur.FirstOperand
        depth += 1
        if depth > BOOLEAN_UNWRAP_DEPTH_LIMIT:
            return None

    if not cur.is_a("IfcExtrudedAreaSolid"):
        return None

    # Only use profile extraction when extrusion is (nearly) parallel to view.
    if not _extrusion_parallel_to_camera(cur, wm_flat, camera_dir):
        return None

    pts_2d = _profile_to_pts_2d(cur.SweptArea)
    if not pts_2d:
        return None

    pts_3d = _apply_axis2placement3d(pts_2d, cur.Position)

    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    cam_inv = np.array(cam_inv_col_major, dtype=float).reshape(4, 4, order='F')
    combined = cam_inv @ world_m

    pts_h = np.hstack([pts_3d, np.ones((len(pts_3d), 1))])
    pts_draw = (combined @ pts_h.T).T[:, :2]

    try:
        poly = shapely.Polygon(pts_draw.tolist())
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.area <= 1e-6:
            return None
    except Exception:
        return None

    # Apply boolean clippings in reverse chain order (outermost last = applied last)
    for node in reversed(chain):
        poly = _clip_polygon_2d_with_halfspace(poly, node.SecondOperand,
                                               wm_flat, cam_inv_col_major)

    return poly if poly.area > 1e-6 else None


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
                if depth > BOOLEAN_UNWRAP_DEPTH_LIMIT:
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

    All openings are subtracted regardless of their height relative to cut_z --
    standard 2D drafting convention: the gap in the hatch shows the opening exists.

    Returns (wall_poly, is_section):
        wall_poly   -- shapely Polygon/MultiPolygon, or None on failure
        is_section  -- True if the wall straddles the cut plane (cut -> hatch)
                       False if the wall is entirely below the cut (view -> outline only)
    """
    wall_poly = _wall_profile_polygon(element, wm_flat, cam_inv_col_major, camera_dir)
    if wall_poly is None or wall_poly.area < 1e-4:
        return None, True

    # Determine section vs view based on wall Z range
    z_min, z_max = _wall_z_range(element, wm_flat)
    if z_min is None:
        is_section = True  # unknown -> default to section
    else:
        is_section = z_min <= cut_z <= z_max

    # Subtract ALL openings (overhead or not -- traditional 2D drafting)
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


# ---------------------------------------------------------------------------
# Slab occlusion helpers
# ---------------------------------------------------------------------------

def _slab_footprint_world(elem, wm_flat):
    """Return Shapely Polygon of slab footprint in world XY, or None.

    Extracts the IfcExtrudedAreaSolid profile and transforms it to world space.
    Only the XY plane projection is used (footprint from above).
    """
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
                if depth > BOOLEAN_UNWRAP_DEPTH_LIMIT:
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
                wm = world_matrix_col_major(elem)
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

    Uses XY footprint containment: element must be spatially below the slab
    (z_origin < z_top) AND its XY origin must fall within the slab polygon.
    Elements outside the slab footprint (courtyards, atria) are never excluded.
    """
    pt = shapely.Point(x_world, y_world)
    for z_top, poly in floor_slabs:
        if z_origin < z_top - 1e-3 and poly.covers(pt):
            return True
    return False
