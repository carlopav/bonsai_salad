"""Wall/opening/slab geometry extraction (Shapely-based section profiles).

Curve extraction (_extract_local_curves and friends) has moved to
../curves.py so it can be shared with the accurate pipeline's plan-symbol
handling (see ../plan_symbols.py). Depends on: camera.py (world_matrix_col_major).
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

from ..camera import world_matrix_col_major

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


def _get_material_layer_strips(element):
    """Return list of (material_name, y_start, y_end) in element-local Y space.

    Reads IfcMaterialLayerSetUsage: layers are stacked along local AXIS2 (Y)
    starting at OffsetFromReferenceLine.  Returns [] if no layer set found.
    """
    try:
        for rel in getattr(element, 'HasAssociations', []):
            if not rel.is_a("IfcRelAssociatesMaterial"):
                continue
            usage = rel.RelatingMaterial
            if not usage.is_a("IfcMaterialLayerSetUsage"):
                continue
            sign   = -1.0 if getattr(usage, 'DirectionSense', 'POSITIVE') == 'NEGATIVE' else 1.0
            y      = float(usage.OffsetFromReferenceLine)
            strips = []
            for layer in usage.ForLayerSet.MaterialLayers:
                name      = (getattr(layer.Material, 'Name', None) or "") if layer.Material else ""
                thickness = float(layer.LayerThickness)
                y_next    = y + sign * thickness
                strips.append((name, min(y, y_next), max(y, y_next)))
                y = y_next
            return strips
    except Exception:
        pass
    return []


def _decompose_wall_to_layer_polygons(full_poly, element, wm_flat, cam_inv_col_major):
    """Decompose full wall plan polygon into per-material-layer sub-polygons.

    Each material layer occupies a strip along the wall's local Y axis (thickness
    direction). The strip is intersected with full_poly to get the layer footprint.

    Returns list of (material_name, shapely_polygon), or None if no layer set.
    """
    strips = _get_material_layer_strips(element)
    if not strips:
        return None

    world_m  = np.array(wm_flat,          dtype=float).reshape(4, 4, order='F')
    cam_inv  = np.array(cam_inv_col_major, dtype=float).reshape(4, 4, order='F')
    combined = cam_inv @ world_m

    # Local Y axis (thickness direction) and X axis (length direction) in camera 2D
    dy = (combined @ np.array([0.0, 1.0, 0.0, 0.0]))[:2]
    dx = (combined @ np.array([1.0, 0.0, 0.0, 0.0]))[:2]
    dy_n = np.linalg.norm(dy)
    dx_n = np.linalg.norm(dx)
    if dy_n < 1e-9 or dx_n < 1e-9:
        return None
    dy = dy / dy_n
    dx = dx / dx_n

    # Wall origin in camera 2D
    orig = (combined @ np.array([0.0, 0.0, 0.0, 1.0]))[:2]

    BIG = 1e6
    result = []
    for mat_name, y_start, y_end in strips:
        p0 = orig + y_start * dy  # near edge of strip
        p1 = orig + y_end   * dy  # far edge of strip
        # Strip polygon: infinite in wall length direction, bounded in Y
        s1 = tuple(p0 + dx * BIG)
        s2 = tuple(p0 - dx * BIG)
        s3 = tuple(p1 - dx * BIG)
        s4 = tuple(p1 + dx * BIG)
        try:
            strip_poly  = shapely.Polygon([s1, s2, s3, s4])
            layer_poly  = full_poly.intersection(strip_poly)
            if not layer_poly.is_empty and layer_poly.area > 1e-6:
                result.append((mat_name, layer_poly))
        except Exception:
            pass

    return result if result else None


def _wall_layer_subdivision_lines(full_poly, element, wm_flat, cam_inv_col_major):
    """Return shapely LineStrings marking the boundaries between adjacent
    IfcMaterialLayerSetUsage layers, clipped to full_poly.

    Only internal boundaries are returned (the two outer wall faces are
    already drawn as the wall outline). Returns None if there are fewer
    than two layers.
    """
    strips = _get_material_layer_strips(element)
    if len(strips) < 2:
        return None

    counts: dict[float, int] = {}
    for _, y_start, y_end in strips:
        for y in (round(y_start, 9), round(y_end, 9)):
            counts[y] = counts.get(y, 0) + 1
    internal_ys = sorted(y for y, c in counts.items() if c >= 2)
    if not internal_ys:
        return None

    world_m  = np.array(wm_flat,          dtype=float).reshape(4, 4, order='F')
    cam_inv  = np.array(cam_inv_col_major, dtype=float).reshape(4, 4, order='F')
    combined = cam_inv @ world_m

    dy = (combined @ np.array([0.0, 1.0, 0.0, 0.0]))[:2]
    dx = (combined @ np.array([1.0, 0.0, 0.0, 0.0]))[:2]
    dy_n = np.linalg.norm(dy)
    dx_n = np.linalg.norm(dx)
    if dy_n < 1e-9 or dx_n < 1e-9:
        return None
    dy = dy / dy_n
    dx = dx / dx_n

    orig = (combined @ np.array([0.0, 0.0, 0.0, 1.0]))[:2]

    BIG = 1e6
    result = []
    for y in internal_ys:
        p = orig + y * dy
        line = shapely.LineString([tuple(p + dx * BIG), tuple(p - dx * BIG)])
        try:
            clipped = full_poly.intersection(line)
        except Exception:
            continue
        if clipped.is_empty:
            continue
        if clipped.geom_type == 'LineString':
            result.append(clipped)
        elif clipped.geom_type == 'MultiLineString':
            result.extend(list(clipped.geoms))

    return result if result else None


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
