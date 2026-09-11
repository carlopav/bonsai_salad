"""Exact IFC curve extraction -> DXF-ready specs (lines, arcs, circles, ellipses).

Shared between pipelines: no Shapely, no wall/section-specific logic. Only
depends on ifcopenshell (direct item walking, with a create_shape fallback
for meshes) and numpy (crease-edge detection on tessellated faces).

Factored out of the shared geometry.py so both the approximate and accurate
pipelines can build BLOCK/INSERT plan symbols from native 2D representations
(see plan_symbols.py).
"""

import math

import numpy as np
import ifcopenshell.geom


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


def _extract_local_curves(element, plan_repr, crease_angle_deg=15.0, unit_scale=1.0):
    """Extract plan curves in element-local coords, in the project unit.

    Returns (verts_flat, edges_flat, arcs, circles, ellipses).
    Tries manual item walking first (IFC values, already in the project unit);
    falls back to ifcopenshell geometry engine, which produces tessellated
    lines only (no arc/circle/ellipse specs) in metres -- divided by
    `unit_scale`, metres per project unit.
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
    verts = [c / unit_scale for c in g.verts]
    faces = list(g.faces)

    if not faces:
        # 2D wire geometry (no faces) — return all edges as-is
        return verts, list(g.edges), [], [], []

    # 3D tessellated mesh: return only naked + crease edges (dihedral > threshold).
    # This avoids flooding the DXF with interior triangulation lines.
    from collections import defaultdict
    cos_t = math.cos(math.radians(crease_angle_deg))
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
