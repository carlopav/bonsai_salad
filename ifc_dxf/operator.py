# Bonsai Salad — ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Export a Bonsai drawing to DXF/DWG using the native ifc_dxf Rust engine.
# Designed to be eventually proposed for upstream integration into
# IfcOpenShell / Bonsai (bonsai/bim/module/drawing/).
#
# Architecture mirrors Bonsai's existing SVG export pipeline:
#   DrawingExporter (operator.py) → camera params + element iterator
#   → ifc_dxf Rust engine (bucket classify + project + write DXF)
#
# Bucket classification:
#   B — 2D native Plan context (Plan/Body, Plan/Axis) → NativeCurves
#   C — 3D Body → wireframe BRep (HLR/cut pending OCC feature)
#   A — IfcExtrudedAreaSolid, extrusion ∥ camera → TODO
#
# DXF output: 1:1 real-world metres, $INSUNITS = 6.
# Each IFC element → DXF BLOCK (GlobalId name) + INSERT on IFC class layer.

import os
import sys

import bpy
import mathutils

try:
    import shapely
    import shapely.ops
    _SHAPELY = True
except ImportError:
    _SHAPELY = False


def _get_ifc():
    try:
        from bonsai import tool
        return tool.Ifc.get()
    except Exception:
        return None


def _get_ifc_dxf():
    """Import the compiled ifc_dxf Rust extension (.pyd / .so)."""
    pkg_dir = os.path.dirname(__file__)
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)
    try:
        import ifc_dxf
        return ifc_dxf
    except ImportError:
        return None


def _get_active_drawing():
    """Return the IFC IfcAnnotation entity for the active Bonsai drawing."""
    try:
        from bonsai import tool
        item = tool.Drawing.get_active_drawing_item()
        if item is None:
            return None
        return tool.Ifc.get().by_id(item.ifc_definition_id)
    except Exception:
        return None


def _get_camera_obj(drawing):
    """Return the Blender camera object for a Bonsai drawing."""
    try:
        from bonsai import tool
        return tool.Ifc.get_object(drawing)
    except Exception:
        return None


def _build_camera_projection(ifc_dxf_mod, camera_obj):
    """Build PyCameraProjection (1:1 metres) from a Blender camera object.

    Uses Bonsai's get_camera_matrix which normalises the matrix and handles
    inversely-scaled RCP cameras.
    """
    from bonsai import tool

    m = tool.Drawing.get_camera_matrix(camera_obj)
    m_inv = m.inverted()
    col_major = [v for col in m_inv.col for v in col]

    return ifc_dxf_mod.PyCameraProjection(col_major)


def _camera_dir_pos(camera_obj):
    """Return (camera_dir, camera_pos) consistent with the projection matrix."""
    from bonsai import tool
    m = tool.Drawing.get_camera_matrix(camera_obj)
    cam_dir = (-m.col[2]).xyz.normalized()
    cam_pos = m.col[3].xyz
    return list(cam_dir), list(cam_pos)


def _get_target_view(drawing):
    try:
        from bonsai import tool
        return tool.Drawing.get_drawing_target_view(drawing)
    except Exception:
        return "PLAN_VIEW"


def _get_material_name(element):
    """Return a material name string for hatch grouping."""
    try:
        import ifcopenshell.util.element as ifc_elem
        mats = ifc_elem.get_materials(element, should_inherit=True)
        if mats:
            return mats[0].Name or ""
    except Exception:
        pass
    return ""


def _identity_matrix():
    return [1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            0, 0, 0, 1]


def _world_matrix(element):
    """Return the element world placement as a flat 16-float column-major list."""
    try:
        import ifcopenshell.util.placement as ifc_place
        placement = getattr(element, "ObjectPlacement", None)
        if placement is None:
            return _identity_matrix()
        return list(ifc_place.get_local_placement(placement).flatten(order="F"))
    except Exception:
        return _identity_matrix()


# ---------------------------------------------------------------------------
# Bucket B: 2D plan native representation
# ---------------------------------------------------------------------------

# Each entry: (ContextType, ContextIdentifier, TargetView).
# Order = priority: first match wins.
# Mirrors Bonsai SVG context selection for Body/Facetation tiers;
# adds FootPrint/Axis as fallback for elements without an OCC section.
_PLAN_CONTEXT_SEARCH = {
    "PLAN_VIEW": [
        ("Plan",  "Body",        "PLAN_VIEW"),
        ("Plan",  "Body",        "MODEL_VIEW"),
        ("Model", "Body",        "PLAN_VIEW"),
        ("Model", "Body",        "MODEL_VIEW"),
        ("Model", "FootPrint",   "PLAN_VIEW"),
        ("Model", "Axis",        "PLAN_VIEW"),
        # Facetation — degraded (tessellated mesh), last resort
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
    # Outer loop = priority tier; inner loop = representations.
    # This guarantees a higher-priority (ctx_type, ctx_id, tv) wins over
    # a lower-priority entry even if the repr list order differs.
    search = _PLAN_CONTEXT_SEARCH.get(target_view, [])
    for ctx_type, ctx_id, tv in search:
        for shape_repr in representations:
            ctx = shape_repr.ContextOfItems
            if (ctx.ContextType == ctx_type
                    and ctx.ContextIdentifier == ctx_id
                    and getattr(ctx, "TargetView", None) == tv):
                return shape_repr
    return None


def _find_plan_repr(element, target_view):
    """Find Plan representation: element override first, then type fallback.

    Returns (repr, from_type): from_type=True means geometry is from the type.
    """
    import ifcopenshell.util.element as ifc_elem

    # 1. Element override
    if hasattr(element, "Representation") and element.Representation is not None:
        r = _find_repr_in_representations(
            element.Representation.Representations, target_view
        )
        if r is not None:
            return r, False

    # 2. Type fallback
    ifc_type = ifc_elem.get_type(element)
    if ifc_type is not None:
        rep_maps = getattr(ifc_type, "RepresentationMaps", None) or []
        type_reprs = [rm.MappedRepresentation for rm in rep_maps]
        r = _find_repr_in_representations(type_reprs, target_view)
        if r is not None:
            return r, True

    return None, False


def _is_mapped_repr(plan_repr):
    """True if all items in the repr are IfcMappedItem (geometry from type)."""
    items = plan_repr.Items
    return bool(items) and all(item.is_a("IfcMappedItem") for item in items)


def _apply_cart_transform_op(verts_flat, op):
    """Apply IfcCartesianTransformationOperator3D to a flat vertex list."""
    import numpy as np
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


def _extract_curves_from_items(items, mapping_target=None):
    """Walk IFC shape items and return (verts_flat, edges_flat) for plan curves."""
    verts = []
    edges = []

    for item in items:
        if item.is_a("IfcMappedItem"):
            src = item.MappingSource
            sub_v, sub_e = _extract_curves_from_items(
                src.MappedRepresentation.Items, mapping_target=item.MappingTarget
            )
            if sub_v:
                base = len(verts) // 3
                verts.extend(sub_v)
                for k in range(0, len(sub_e), 2):
                    edges.extend([sub_e[k] + base, sub_e[k+1] + base])

        elif item.is_a("IfcAnnotationFillArea"):
            sub_v, sub_e = _extract_curves_from_items([item.OuterBoundary])
            if sub_v:
                base = len(verts) // 3
                verts.extend(sub_v)
                for k in range(0, len(sub_e), 2):
                    edges.extend([sub_e[k] + base, sub_e[k+1] + base])

        elif item.is_a("IfcGeometricCurveSet") or item.is_a("IfcGeometricSet"):
            sub_v, sub_e = _extract_curves_from_items(list(item.Elements))
            if sub_v:
                base = len(verts) // 3
                verts.extend(sub_v)
                for k in range(0, len(sub_e), 2):
                    edges.extend([sub_e[k] + base, sub_e[k+1] + base])

        elif item.is_a("IfcCompositeCurve"):
            segs = [s.ParentCurve for s in item.Segments]
            sub_v, sub_e = _extract_curves_from_items(segs)
            if sub_v:
                base = len(verts) // 3
                verts.extend(sub_v)
                for k in range(0, len(sub_e), 2):
                    edges.extend([sub_e[k] + base, sub_e[k+1] + base])

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
                    idxs = [int(i) for i in seg]
                    if seg.is_a("IfcLineIndex"):
                        for k in range(len(idxs) - 1):
                            edges.extend([base + idxs[k]-1, base + idxs[k+1]-1])
                    elif seg.is_a("IfcArcIndex"):
                        edges.extend([base + idxs[0]-1, base + idxs[-1]-1])
            else:
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

    if mapping_target is not None and verts:
        verts = _apply_cart_transform_op(verts, mapping_target)

    return verts, edges


def _extract_local_curves(element, plan_repr):
    """Extract plan curves in element-local coords.

    For IfcMappedItem reprs: walks the chain manually — reliable regardless
    of ifcopenshell context-ids filtering behaviour.
    For direct geometry: falls back to ifcopenshell geometry engine.
    """
    import ifcopenshell.geom as geom
    items = plan_repr.Items

    if items and any(i.is_a("IfcMappedItem") for i in items):
        v, e = _extract_curves_from_items(list(items))
        if v and e:
            return v, e

    ctx_id = plan_repr.ContextOfItems.id()
    s = geom.settings()
    s.set('use-world-coords', False)
    s.set('context-ids', [ctx_id])
    shape = geom.create_shape(s, element)
    return list(shape.geometry.verts), list(shape.geometry.edges)


def _get_type_block_name(element):
    """Return (type_entity, block_name) if element has an IFC type, else (None, None)."""
    import ifcopenshell.util.element as ifc_elem
    ifc_type = ifc_elem.get_type(element)
    if ifc_type is None:
        return None, None
    name = getattr(ifc_type, "Name", None) or ifc_type.GlobalId
    return ifc_type, name


# IFC classes that go through the wall-merge path instead of BLOCK/INSERT.
_WALL_MERGE_CLASSES = frozenset({"IfcWall", "IfcWallStandardCase"})

# Wall section modes
# flat    — project edges to model space as LINE entities (no hatch)
# shapely — shapely.polygonize + union → LWPOLYLINE + hatch
WALL_MODES = ("flat", "shapely")


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
    import numpy as np
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
    return origin + xs * x_ax + ys * y_ax


def _extrusion_parallel_to_camera(item, wm_flat, camera_dir):
    """Return True if item's extrusion is within ~15° of camera direction."""
    import numpy as np
    if not item.is_a("IfcExtrudedAreaSolid"):
        return False
    ed = item.ExtrudedDirection.DirectionRatios
    local_dir = np.array([float(ed[0]), float(ed[1]),
                          float(ed[2]) if len(ed) > 2 else 0.0])
    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    world_dir = world_m[:3, :3] @ local_dir
    n = np.linalg.norm(world_dir)
    if n < 1e-9:
        return False
    world_dir = world_dir / n
    cam = np.array(camera_dir, dtype=float)
    cn = np.linalg.norm(cam)
    if cn < 1e-9:
        return False
    return abs(float(np.dot(world_dir, cam / cn))) > 0.966


def _extruded_plan_polygon(item, wm_flat, cam_inv_col_major, camera_dir):
    """Convert IfcExtrudedAreaSolid to plan 2D polygon; rejects non-vertical extrusions."""
    import numpy as np
    depth = 0
    while item.is_a("IfcBooleanClippingResult") or item.is_a("IfcBooleanResult"):
        item = item.FirstOperand
        depth += 1
        if depth > 8:
            return None
    if not item.is_a("IfcExtrudedAreaSolid"):
        return None
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
    """Get plan footprint + world Z extents of an IfcOpeningElement.

    Returns (polygon_2d, z_min, z_max) or (None, None, None).
    """
    import numpy as np
    import ifcopenshell.geom as geom
    try:
        s = geom.settings()
        s.set('use-world-coords', True)
        shape = geom.create_shape(s, opening)
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
    """Return (z_min, z_max) in world space from IfcExtrudedAreaSolid, or (None, None)."""
    import numpy as np
    if not hasattr(element, 'Representation') or element.Representation is None:
        return None, None
    world_m = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    for repr_ in element.Representation.Representations:
        for item in repr_.Items:
            cur, d = item, 0
            while cur.is_a("IfcBooleanClippingResult") or cur.is_a("IfcBooleanResult"):
                cur = cur.FirstOperand
                d += 1
                if d > 8: break
            if not cur.is_a("IfcExtrudedAreaSolid"):
                continue
            pos_z = 0.0
            if cur.Position and cur.Position.Location:
                loc = cur.Position.Location.Coordinates
                if len(loc) > 2:
                    pos_z = float(loc[2])
            solid_depth = float(cur.Depth)
            ed = cur.ExtrudedDirection.DirectionRatios
            local_dir = np.array([float(ed[0]), float(ed[1]),
                                  float(ed[2]) if len(ed) > 2 else 0.0])
            dz = float((world_m[:3, :3] @ local_dir)[2]) * solid_depth
            z_base = float((world_m @ np.array([0.0, 0.0, pos_z, 1.0]))[2])
            return min(z_base, z_base + dz), max(z_base, z_base + dz)
    return None, None


def _extract_wall_polygon_with_openings(element, wm_flat, cam_inv_col_major,
                                         cut_z, camera_dir):
    """Wall plan polygon = profile minus ALL openings.

    All openings are subtracted regardless of height (standard 2D drafting convention).
    Returns (wall_poly, is_section):
        is_section=True  → wall straddles cut_z → Section layer + hatch
        is_section=False → wall below cut_z    → View layer, no hatch
    """
    wall_poly = _wall_profile_polygon(element, wm_flat, cam_inv_col_major, camera_dir)
    if wall_poly is None or wall_poly.area < 1e-4:
        return None, True

    z_min, z_max = _wall_z_range(element, wm_flat)
    is_section = True if z_min is None else (z_min <= cut_z <= z_max)

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
    """Union per-wall polygons by (ifc_class, material, is_section), add WallPolygon to req."""
    for (ifc_class, material, is_section), polys in wall_polys_by_key.items():
        if not polys:
            continue
        try:
            merged = shapely.ops.unary_union(polys)
        except Exception:
            merged = polys[0] if len(polys) == 1 else None
        if merged is None:
            continue

        if merged.geom_type == 'MultiPolygon':
            result_polys = list(merged.geoms)
        elif merged.geom_type == 'Polygon':
            result_polys = [merged]
        else:
            result_polys = [g for g in merged.geoms if g.geom_type == 'Polygon']

        for poly in result_polys:
            outer = [[float(x), float(y)] for x, y in poly.exterior.coords[:-1]]
            holes = [[[float(x), float(y)] for x, y in ring.coords[:-1]]
                     for ring in poly.interiors]
            try:
                req.add_wall_polygon(ifc_class, material, outer, holes, is_section)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Element classifier (stateful: caller must pass seen_blocks dict)
# ---------------------------------------------------------------------------

def _classify_and_add(
    req, ifc_dxf_mod, element, target_view, seen_blocks,
    wall_mode="shapely", cam_inv_col_major=None, wall_lines_by_key=None,
    cut_z=0.0, camera_dir=None,
):
    """Classify one IFC element and add it to the drawing request.

    seen_blocks: dict[block_name → True] shared across all elements.
    wall_lines_by_key: dict[(ifc_class, material) → list] for shapely mode.
    cut_z: world-space Z of the section cut plane (cam_pos[2] for PLAN_VIEW).
    camera_dir: camera look direction [x, y, z] — used for extrusion parallel check.
    """
    import ifcopenshell.geom as geom

    ifc_class = element.is_a()
    material  = _get_material_name(element)
    gid       = element.GlobalId
    wm        = _world_matrix(element)

    # ── Wall path ──────────────────────────────────────────────────────────────
    if ifc_class in _WALL_MERGE_CLASSES:
        if wall_mode == "shapely" and _SHAPELY and cam_inv_col_major is not None:
            # Profile-based: IfcExtrudedAreaSolid + 2D opening subtraction.
            # Falls through to Bucket B/C for non-vertical / non-extruded walls.
            try:
                poly, is_section = _extract_wall_polygon_with_openings(
                    element, wm, cam_inv_col_major, cut_z,
                    camera_dir or [0.0, 0.0, -1.0]
                )
                if poly is not None:
                    key = (ifc_class, material, is_section)
                    wall_lines_by_key.setdefault(key, []).append(poly)
                    return
            except Exception:
                pass
        else:
            # flat mode: project edges as LINE entities
            plan_repr, _ = _find_plan_repr(element, target_view)
            if plan_repr is not None:
                try:
                    verts, edges = _extract_local_curves(element, plan_repr)
                    if verts and edges:
                        req.add_wall_flat(ifc_class, material, verts, edges, wm)
                        return
                except Exception:
                    pass
        # fall through to standard Bucket B/C if wall geometry unavailable

    # ── Bucket B: 2D plan native representation ──────────────────────────────
    plan_repr, from_type = _find_plan_repr(element, target_view)
    if plan_repr is not None:
        try:
            if from_type or _is_mapped_repr(plan_repr):
                _, block_name = _get_type_block_name(element)
                if block_name is None:
                    block_name = gid
            else:
                block_name = gid

            if block_name not in seen_blocks:
                verts, edges = _extract_local_curves(element, plan_repr)
                if verts and edges:
                    req.add_native_curves(block_name, ifc_class, material, verts, edges)
                    seen_blocks[block_name] = True

            if block_name in seen_blocks:
                req.add_block_insert(block_name, ifc_class, wm)
                return
        except Exception:
            pass

    # ── Bucket C fallback: 3D Body BRep wireframe ────────────────────────────
    try:
        settings = geom.settings()
        settings.set('use-world-coords', True)
        settings.set('iterator-output', 2)
        shape     = geom.create_shape(settings, element)
        brep_text = shape.geometry.brep_data
        req.add_body_brep(gid, ifc_class, material, brep_text, tuple(wm))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Blender operator
# ---------------------------------------------------------------------------

class ExportDrawingToDxfOperator(bpy.types.Operator):
    """Export the active Bonsai drawing to DXF using the native ifc_dxf engine."""

    bl_idname = "bim.export_drawing_to_dxf"
    bl_label = "Export Drawing to DXF"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    dxf_version: bpy.props.EnumProperty(
        name="DXF Version",
        items=[
            ("AC1027", "R2013 (AC1027)", ""),
            ("AC1032", "R2018 (AC1032)", ""),
            ("AC1021", "R2007 (AC1021)", ""),
            ("AC1015", "R2000 (AC1015)", ""),
        ],
        default="AC1027",
    )

    @classmethod
    def poll(cls, context):
        if _get_ifc() is None:
            return False
        if _get_ifc_dxf() is None:
            return False
        return _get_active_drawing() is not None

    def invoke(self, context, event):
        ifc = _get_ifc()
        if ifc:
            ifc_path    = bpy.data.filepath or ""
            default_dir = os.path.dirname(ifc_path) if ifc_path else ""
            self.filepath = os.path.join(default_dir, "drawing.dxf")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ifc_dxf_mod = _get_ifc_dxf()
        if ifc_dxf_mod is None:
            self.report(
                {"ERROR"},
                "ifc_dxf native module not found. "
                "Build it with: python build_ifc_dxf.py",
            )
            return {"CANCELLED"}

        drawing = _get_active_drawing()
        if drawing is None:
            self.report({"ERROR"}, "No active Bonsai drawing found.")
            return {"CANCELLED"}

        camera_obj = _get_camera_obj(drawing)
        if camera_obj is None:
            self.report({"ERROR"}, "Drawing has no camera object.")
            return {"CANCELLED"}

        try:
            proj = _build_camera_projection(ifc_dxf_mod, camera_obj)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not build camera projection: {exc}")
            return {"CANCELLED"}

        # Raw camera inverse for Python-side wall projection (Shapely mode)
        from bonsai import tool as _tool
        _m = _tool.Drawing.get_camera_matrix(camera_obj)
        cam_inv_col_major = [v for col in _m.inverted().col for v in col]

        camera_dir, camera_pos = _camera_dir_pos(camera_obj)
        target_view = _get_target_view(drawing)

        output_path = bpy.path.abspath(self.filepath)
        if not output_path.lower().endswith((".dxf", ".dwg")):
            output_path += ".dxf"

        req = ifc_dxf_mod.PyDrawingRequest(
            proj,
            camera_dir,
            camera_pos,
            target_view,
            output_path,
            self.dxf_version,
        )

        # Load layer styles from JSON next to the addon package
        _styles_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                    "layer_styles.json")
        try:
            import json as _json
            _DXF_LW = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50,
                       53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)
            def _snap(mm): return min(_DXF_LW, key=lambda v: abs(v - round(float(mm) * 100)))
            with open(_styles_path, encoding="utf-8") as _f:
                _data = _json.load(_f)
            _styles = [(k, int(v.get("color", 7)),
                        _snap(v.get("lineweight", 0.25)),
                        str(v.get("linetype", "Continuous")))
                       for k, v in _data.items() if not k.startswith("_")]
            if _styles:
                req.set_layer_styles(_styles)
        except Exception:
            pass

        try:
            from bonsai import tool
            elements = tool.Drawing.get_drawing_elements(drawing)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not get drawing elements: {exc}")
            return {"CANCELLED"}

        seen_blocks = {}
        wall_polys_by_key = {}
        wall_mode = "shapely" if _SHAPELY else "flat"

        for element in elements:
            _classify_and_add(
                req, ifc_dxf_mod, element, target_view, seen_blocks,
                wall_mode=wall_mode,
                cam_inv_col_major=cam_inv_col_major,
                wall_lines_by_key=wall_polys_by_key,
                cut_z=camera_pos[2],
                camera_dir=camera_dir,
            )

        if wall_mode == "shapely" and wall_polys_by_key:
            _add_wall_polygons(req, wall_polys_by_key)

        try:
            ifc_dxf_mod.py_generate(req, None)
        except Exception as exc:
            self.report({"ERROR"}, f"DXF generation failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"DXF exported to {output_path}")
        return {"FINISHED"}


classes = [ExportDrawingToDxfOperator]
