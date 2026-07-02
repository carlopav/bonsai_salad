"""Approximate pipeline: IFC-native traversal + Shapely wall-section extraction.

No OCC / HLR. Uses native 2D representations where available (exact arcs,
circles, ellipses) and derives wall/column sections by projecting extrusion
profiles and subtracting openings. See ifc_dxf/README.md, Pipeline A.
"""

import os
import time
from collections import namedtuple

import numpy as np

from ..camera import (
    camera_matrix_inv_col_major,
    camera_dir_pos,
    world_matrix_col_major,
)
from ..ifc_query import (
    find_plan_repr,
    is_mapped_repr,
    get_material_name,
    get_elements,
    _get_drawing_annotations,
)
from ..curves import _extract_local_curves
from .geometry import (
    SHAPELY_AVAILABLE,
    _wall_z_range,
    _extract_wall_polygon_with_openings,
    _compute_floor_slabs,
    _is_occluded_by_slab,
    _slab_footprint_world,
    _decompose_wall_to_layer_polygons,
    _wall_layer_subdivision_lines,
)
from ..dxf_template import _parse_scale_factor
from ..dxf_writer import _write_dxf
from ..plan_symbols import place_plan_symbol


# ---------------------------------------------------------------------------
# ElementRecord: one classified element
# ---------------------------------------------------------------------------

ElementRecord = namedtuple("ElementRecord", [
    "element",    # IFC element
    "bucket",     # "A", "B", "C"
    "layer",      # DXF layer: "IfcWindow", "IfcWindow_Overhead", "IfcWall_Section", ...
    "plan_repr",  # IfcShapeRepresentation (Bucket A only, else None)
    "from_type",  # bool: repr inherited from type -> shared block name
])


# ---------------------------------------------------------------------------
# classify_elements
# ---------------------------------------------------------------------------

def classify_elements(elements, cut_z, col_major, cam_dir, target_view,
                      floor_slabs, section_classes):
    """Classify every element into a bucket and assign its DXF layer.

    All classification rules live here; no geometry is extracted.
    Returns list[ElementRecord] in processing order (B first, then A/C).

    Buckets:
      B -- section classes (walls, ...): layer = IfcWall_Section / _View
      A -- 2D native repr found: layer = IfcWindow / IfcWindow_Overhead / ...
      C -- no usable repr or occluded: skipped
    """
    records = []
    overhead_ids = set()

    # Pass 1: section classes -> Bucket B, collect overhead fill IDs
    for elem in elements:
        cls = elem.is_a()
        if cls not in section_classes:
            continue
        wm = world_matrix_col_major(elem)
        z_min, z_max = _wall_z_range(elem, wm)
        is_cut = z_min is None or (z_min <= cut_z <= z_max)
        layer = f"{cls}_Section" if is_cut else f"{cls}_View"

        # Openings entirely above cut_z -> filling element is overhead
        for rel in getattr(elem, 'HasOpenings', []):
            op = rel.RelatedOpeningElement
            if not hasattr(op, 'ObjectPlacement') or op.ObjectPlacement is None:
                continue
            wm_op = world_matrix_col_major(op)
            z_min_op, _ = _wall_z_range(op, wm_op)
            if z_min_op is None:
                z_min_op = float(wm_op[14])  # fallback: placement Z origin
            if z_min_op > cut_z + 1e-3:
                for fill_rel in getattr(op, 'HasFillings', []):
                    filling = getattr(fill_rel, 'RelatedBuildingElement', None)
                    if filling is not None:
                        overhead_ids.add(filling.id())

        records.append(ElementRecord(elem, "B", layer, None, False))

    # Pass 2: everything else -> Bucket A or C
    for elem in elements:
        cls = elem.is_a()
        if cls in section_classes:
            continue
        wm = world_matrix_col_major(elem)

        # Rule: slab occlusion
        if floor_slabs:
            x_w, y_w, z_w = float(wm[12]), float(wm[13]), float(wm[14])
            if _is_occluded_by_slab(x_w, y_w, z_w, floor_slabs):
                records.append(ElementRecord(elem, "C", cls, None, False))
                continue

        plan_repr, from_type = find_plan_repr(elem, target_view)
        if plan_repr is not None:
            # Rule: overhead fill -> _Overhead layer (dashed)
            layer = f"{cls}_Overhead" if elem.id() in overhead_ids else cls
            records.append(ElementRecord(elem, "A", layer, plan_repr, from_type))
        else:
            records.append(ElementRecord(elem, "C", cls, None, False))

    return records


# ---------------------------------------------------------------------------
# export_drawing -- public API entry point
# ---------------------------------------------------------------------------

WALL_MODES = ("flat", "shapely")


def export_drawing(ifc, drawing, pset, output_path, wall_mode="shapely",
                   template_path=None, crease_angle_deg=15.0,
                   export_material_layers=False):
    """Export a single Bonsai drawing to DXF using the approximate pipeline.

    Parameters
    ----------
    ifc:           ifcopenshell.file object
    drawing:       IfcAnnotation with EPset_Drawing
    pset:          dict from EPset_Drawing (HumanScale, Scale, TargetView, ...)
    output_path:   destination .dxf file path
    wall_mode:     "shapely" (polygon+hatch) or "flat" (line entities)
    template_path: path to DXF template (None -> use script-dir template or minimal)
    """
    if wall_mode == "shapely" and not SHAPELY_AVAILABLE:
        print("  (shapely not available -> falling back to flat wall mode)")
        wall_mode = "flat"
    target_view = pset.get("TargetView", "PLAN_VIEW")
    human_scale = pset.get("HumanScale", "NTS")
    print(f"  TargetView : {target_view}   Scale: {human_scale}   WallMode: {wall_mode}")

    if template_path is None:
        # Try to find template in ifc_dxf/templates/
        pkg_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        template_path = os.path.join(pkg_dir, "templates", "ifc_dxf_template_metric.dxf")
    if os.path.isfile(template_path):
        print(f"  Template   : {os.path.basename(template_path)}")
    else:
        print(f"  Template   : (not found, using minimal fallback)")
        template_path = None

    col_major        = camera_matrix_inv_col_major(drawing)
    cam_dir, cam_pos = camera_dir_pos(drawing)

    _cam_inv_np  = np.array(col_major, dtype=float).reshape(4, 4, order='F')
    _cam_R       = _cam_inv_np[:3, :3]
    _cam_x_proj  = _cam_R @ np.array([1.0, 0.0, 0.0])
    _cam_rot_deg = float(np.degrees(np.arctan2(float(_cam_x_proj[1]),
                                               float(_cam_x_proj[0]))))

    elements = get_elements(ifc, drawing, pset)
    print(f"  Elements   : {len(elements)}")

    # Classes processed in Bucket B (section from 3D solid)
    _SECTION_CLASSES = frozenset({"IfcWall", "IfcWallStandardCase", "IfcColumn"})

    # Classes whose plan view is best represented as a 2D footprint polygon
    # (closed LWPOLYLINE) rather than a BLOCK/INSERT. Scoped tightly to avoid
    # accidentally capturing the Body IfcExtrudedAreaSolid of doors/windows.
    _FOOTPRINT_CLASSES = frozenset({"IfcSlab", "IfcCovering", "IfcRoof"})

    cut_z = cam_pos[2]
    floor_slabs = _compute_floor_slabs(ifc, cut_z) if SHAPELY_AVAILABLE else []
    if floor_slabs:
        print(f"  Floor slabs: {len(floor_slabs)} footprints for slab occlusion")

    # Re-add fill elements above the frustum: openings entirely above cut_z are
    # overhead -> their fillings (windows/doors) need to appear on _Overhead layers
    # but are excluded by frustum Z culling (frustum Z_max == cut_z).
    element_ids = {e.id() for e in elements}
    overhead_extras = set()
    for elem in elements:
        if elem.is_a() not in _SECTION_CLASSES:
            continue
        for rel in getattr(elem, 'HasOpenings', []):
            op = rel.RelatedOpeningElement
            if not hasattr(op, 'ObjectPlacement') or op.ObjectPlacement is None:
                continue
            wm_op = world_matrix_col_major(op)
            z_min_op, _ = _wall_z_range(op, wm_op)
            if z_min_op is None:
                z_min_op = float(wm_op[14])
            if z_min_op > cut_z + 1e-3:
                for fill_rel in getattr(op, 'HasFillings', []):
                    filling = getattr(fill_rel, 'RelatedBuildingElement', None)
                    if filling is not None and filling.id() not in element_ids:
                        overhead_extras.add(filling)
    if overhead_extras:
        elements = elements | overhead_extras
        print(f"  Overhead+  : re-added {len(overhead_extras)} fill elements above frustum")

    block_defs    = {}   # name -> {ifc_class, material, lines, arcs, circles, ellipses}
    block_order   = []
    block_inserts = {}   # name -> [(pos_2d, rot_deg, layer), ...]
    flat_edges    = []   # [(p0, p1, layer)] -- wall_mode='flat' only
    wall_polys_by_key = {}  # (ifc_class, material, layer, z_top) -> [Polygon, ...]
    footprint_polys = []  # [(gid, layer, exterior_pts, [hole_pts])] -- LWPOLYLINE+GROUP
    wall_layer_polys_by_key = {}  # (ifc_class, mat_name, layer, z_top) -> [Polygon]
    wall_subdivision_lines = []  # [LineString, ...]
    seen_blocks   = {}

    bucket_a = bucket_b = bucket_c = 0
    bucket_a_classes = {}
    bucket_b_classes = {}
    bucket_c_classes = {}

    records = classify_elements(elements, cut_z, col_major, cam_dir, target_view,
                                floor_slabs, _SECTION_CLASSES)

    n_overhead = sum(1 for r in records if r.bucket == "A" and r.layer.endswith("_Overhead"))
    if n_overhead:
        print(f"  Overhead   : {n_overhead} elements fill openings above cut plane")

    # Bucket B: section classes (walls, ...)
    for rec in (r for r in records if r.bucket == "B"):
        element   = rec.element
        ifc_class = element.is_a()
        material  = get_material_name(element)
        wm        = world_matrix_col_major(element)
        processed = False

        if wall_mode == "shapely":
            try:
                poly, _ = _extract_wall_polygon_with_openings(
                    element, wm, col_major, cut_z, cam_dir
                )
                if poly is not None:
                    if rec.layer.endswith("_View"):
                        _, z_max = _wall_z_range(element, wm)
                        z_top_key = round(z_max, 3) if z_max is not None else None
                    else:
                        z_top_key = None
                    key = (ifc_class, material, rec.layer, z_top_key)
                    wall_polys_by_key.setdefault(key, []).append(poly)
                    if export_material_layers and rec.layer.endswith("_Section"):
                        layer_polys = _decompose_wall_to_layer_polygons(
                            poly, element, wm, col_major
                        )
                        if layer_polys:
                            for mat_name, lp in layer_polys:
                                lkey = (ifc_class, mat_name, rec.layer, z_top_key)
                                wall_layer_polys_by_key.setdefault(lkey, []).append(lp)
                        subdivision_lines = _wall_layer_subdivision_lines(
                            poly, element, wm, col_major
                        )
                        if subdivision_lines:
                            wall_subdivision_lines.extend(subdivision_lines)
                    bucket_b += 1
                    bucket_b_classes[ifc_class] = bucket_b_classes.get(ifc_class, 0) + 1
                    processed = True
            except Exception:
                pass
        else:
            plan_repr_b, _ = find_plan_repr(element, target_view)
            if plan_repr_b is not None:
                try:
                    verts, edges, _a, _c, _el = _extract_local_curves(element, plan_repr_b,
                                                                       crease_angle_deg)
                    if verts and edges:
                        n        = len(verts) // 3
                        va       = np.array(verts[:n*3]).reshape(n, 3)
                        pd       = (_cam_inv_np @ np.hstack([va, np.ones((n, 1))]).T).T[:, :2]
                        for k in range(0, len(edges) - 1, 2):
                            i, j = int(edges[k]), int(edges[k+1])
                            p0 = (float(pd[i, 0]), float(pd[i, 1]))
                            p1 = (float(pd[j, 0]), float(pd[j, 1]))
                            if (p0[0]-p1[0])**2 + (p0[1]-p1[1])**2 > 1e-18:
                                flat_edges.append((p0, p1, rec.layer))
                        bucket_b += 1
                        bucket_b_classes[ifc_class] = bucket_b_classes.get(ifc_class, 0) + 1
                        processed = True
                except Exception:
                    pass

        if not processed:
            bucket_c += 1
            bucket_c_classes[ifc_class] = bucket_c_classes.get(ifc_class, 0) + 1

    # Bucket A: 2D native representation
    for rec in (r for r in records if r.bucket == "A"):
        element   = rec.element
        ifc_class = element.is_a()
        gid       = element.GlobalId
        wm        = world_matrix_col_major(element)
        placed    = False

        try:
            # Step 1: footprint LWPOLYLINE+GROUP — for _FOOTPRINT_CLASSES (slabs,
            # coverings, roofs) without a shared type block. Scoped to these classes
            # so that doors/windows (which also have IfcExtrudedAreaSolid in their
            # Body repr) keep their 2D plan symbol with arcs. Skipped whenever the
            # plan geometry actually comes from the type (shared block preferred --
            # see place_plan_symbol's from_type/is_mapped_repr rule).
            if (SHAPELY_AVAILABLE and ifc_class in _FOOTPRINT_CLASSES
                    and not (rec.from_type or is_mapped_repr(rec.plan_repr))):
                fpoly = _slab_footprint_world(element, wm)
                if fpoly is not None:
                    z_elem = float(wm[14])  # col-major index 14 = row2,col3 = world Z
                    ext_pts = []
                    for wx, wy in fpoly.exterior.coords[:-1]:
                        cpt = _cam_inv_np @ np.array([wx, wy, z_elem, 1.0])
                        ext_pts.append((float(cpt[0]), float(cpt[1])))
                    hole_pts = []
                    for ring in fpoly.interiors:
                        hole = []
                        for wx, wy in ring.coords[:-1]:
                            cpt = _cam_inv_np @ np.array([wx, wy, z_elem, 1.0])
                            hole.append((float(cpt[0]), float(cpt[1])))
                        hole_pts.append(hole)
                    if len(ext_pts) >= 3:
                        footprint_polys.append((gid, rec.layer, ext_pts, hole_pts))
                        bucket_a += 1
                        bucket_a_classes[ifc_class] = bucket_a_classes.get(ifc_class, 0) + 1
                        placed = True

            # Step 2: BLOCK/INSERT via native 2D plan representation (shared type
            # block or unique per instance) — shared with the accurate pipeline.
            if not placed:
                placed = place_plan_symbol(
                    element, rec.layer, target_view, crease_angle_deg,
                    _cam_R, _cam_inv_np, _cam_rot_deg,
                    block_defs, block_order, block_inserts, seen_blocks,
                )
                if placed:
                    bucket_a += 1
                    bucket_a_classes[ifc_class] = bucket_a_classes.get(ifc_class, 0) + 1
        except Exception:
            pass

        if not placed:
            bucket_c += 1
            bucket_c_classes[ifc_class] = bucket_c_classes.get(ifc_class, 0) + 1

    # Bucket C: count only
    for rec in (r for r in records if r.bucket == "C"):
        ifc_class = rec.element.is_a()
        bucket_c += 1
        bucket_c_classes[ifc_class] = bucket_c_classes.get(ifc_class, 0) + 1

    print(f"  Bucket A   : {bucket_a}  Bucket B: {bucket_b}  Bucket C: {bucket_c}")
    if bucket_a_classes:
        print(f"  Bucket A   : { {k: v for k, v in sorted(bucket_a_classes.items())} }")
    if bucket_b_classes:
        print(f"  Bucket B   : { {k: v for k, v in sorted(bucket_b_classes.items())} }")
    if bucket_c_classes:
        print(f"  Bucket C   : { {k: v for k, v in sorted(bucket_c_classes.items())} }")

    if wall_mode == "shapely" and wall_polys_by_key:
        n_polys = sum(len(v) for v in wall_polys_by_key.values())
        n_sec   = sum(len(v) for (_, _, lyr, _), v in wall_polys_by_key.items()
                      if lyr.endswith("_Section"))
        print(f"  Wall polys : {n_polys} total ({n_sec} section, {n_polys-n_sec} view)"
              f"  in {len(wall_polys_by_key)} groups")

    annotations = _get_drawing_annotations(ifc, drawing)
    if annotations:
        print(f"  Annotations: {len(annotations)}"
              f" ({sum(1 for a in annotations if a.ObjectType=='DIMENSION')} dims)")

    t0 = time.perf_counter()
    scale_factor_val = _parse_scale_factor(pset.get("Scale", "")) or 0.01
    if footprint_polys:
        print(f"  Footprints : {len(footprint_polys)} LWPOLYLINE groups")
    _write_dxf(output_path, block_defs, block_order, block_inserts,
               flat_edges, wall_polys_by_key,
               annotations=annotations, cam_inv_np=_cam_inv_np,
               template_path=template_path, scale_factor=scale_factor_val,
               drawing_name=getattr(drawing, "Name", None),
               drawing_identification=getattr(drawing, "Identification", None),
               drawing_scale=human_scale,
               footprint_polys=footprint_polys,
               wall_layer_polys=wall_layer_polys_by_key or None,
               wall_subdivision_lines=wall_subdivision_lines or None)
    elapsed = time.perf_counter() - t0
    size_kb = os.path.getsize(output_path) // 1024
    print(f"  DXF gen    : {elapsed:.2f}s")
    print(f"  >> {output_path}  ({size_kb} KB)")
    return output_path
