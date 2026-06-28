"""DXF writing pipeline: _write_dxf, export_drawing, load_layer_styles, classify_elements."""

import os
import json
import time
from collections import namedtuple

import numpy as np

try:
    import shapely
    import shapely.ops
    _SHAPELY_AVAILABLE = True
except ImportError:
    _SHAPELY_AVAILABLE = False

from .camera import (
    camera_matrix_inv_col_major,
    camera_dir_pos,
    world_matrix_col_major,
)
from .ifc_query import (
    find_plan_repr,
    get_type_block_name,
    get_material_name,
    get_elements,
    _get_drawing_annotations,
)
from .geometry import (
    SHAPELY_AVAILABLE,
    _wall_z_range,
    _extract_local_curves,
    _extract_wall_polygon_with_openings,
    _compute_floor_slabs,
    _is_occluded_by_slab,
)
from .dxf_template import (
    _project_local_to_lines,
    _compute_insert,
    _snap_lw,
    _parse_scale_factor,
    _setup_dxf_layers,
    _populate_scale_list,
    _resolve_text_font,
    _fill_cartiglio,
    _ensure_dim_style,
)
from .annotations import (
    _write_dimension_annotations,
    _write_text_annotations,
)


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
# load_layer_styles
# ---------------------------------------------------------------------------

def load_layer_styles(styles_path=None):
    """Load layer styles from a JSON file.

    lineweight in the JSON is in mm (e.g. 0.35); it is snapped to the nearest
    valid DXF lineweight value (in hundredths of mm).

    Returns a list of (layer_name, color, lineweight_hundredths, linetype) tuples.
    """
    if styles_path is None:
        # Default: alongside this package's parent directory
        styles_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "layer_styles.json",
        )
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


# ---------------------------------------------------------------------------
# _write_dxf
# ---------------------------------------------------------------------------

def _write_dxf(output_path, block_defs, block_order, block_inserts,
               flat_edges, wall_polys_by_key,
               annotations=None, cam_inv_np=None,
               template_path=None, scale_factor=0.01,
               drawing_name=None, drawing_identification=None,
               drawing_scale=None):
    """Write all collected drawing data to a DXF file using ezdxf.

    When template_path is provided the document is cloned from the template
    (layers, dimstyles, layouts already configured); otherwise a minimal
    document is created with basic layer defaults.

    block_defs:    name -> {ifc_class, material, lines, arcs, circles, ellipses}
    block_order:   list of block names in insertion order
    block_inserts: name -> [(pos_2d, rot_deg, layer), ...]
    flat_edges:    [(p0, p1, layer), ...]
    wall_polys_by_key: {(ifc_class, material, layer) -> [shapely Polygon, ...]}
    annotations:   list of IfcAnnotation elements (optional, Bucket D)
    cam_inv_np:    4x4 numpy camera inverse matrix (needed for annotations)
    template_path: path to ifc_dxf_template.dxf (None -> minimal fallback)
    scale_factor:  drawing scale as a pure ratio (0.01 for 1:100, 0.02 for 1:50)
    """
    import ezdxf
    from ezdxf import units

    SNAP_TOL = 0.0005  # 0.5 mm

    # Entities inside block definitions use BYBLOCK so that the INSERT entity
    # (on its layer) controls colour, linetype and lineweight.
    # color=0    -> BYBLOCK
    # linetype   -> "BYBLOCK"
    # lineweight -> -2 (BYBLOCK per DXF spec group-code 370)
    _BB = {"layer": "0", "color": 0, "linetype": "BYBLOCK", "lineweight": -2}

    if template_path and os.path.isfile(template_path):
        doc = ezdxf.readfile(template_path)
        msp = doc.modelspace()
        msp.delete_all_entities()
        _resolve_text_font(doc)
        doc.header["$LTSCALE"] = float(scale_factor)
        scale_handles = _populate_scale_list(doc, scale_factor)
        denom = int(round(1.0 / scale_factor))
        current_scale_handle = scale_handles.get(f"1:{denom}")
        _fill_cartiglio(doc, scale_factor,
                        scale_handle=current_scale_handle,
                        drawing_name=drawing_name,
                        drawing_identification=drawing_identification,
                        drawing_scale=drawing_scale)
    else:
        doc = ezdxf.new("R2010")
        doc.units = units.M
        msp = doc.modelspace()
        doc.header["$LTSCALE"] = float(scale_factor)
        scale_handles = _populate_scale_list(doc, scale_factor)
        denom = int(round(1.0 / scale_factor))
        current_scale_handle = scale_handles.get(f"1:{denom}")

    # block definitions + inserts
    for block_name in block_order:
        bd  = block_defs[block_name]
        blk = doc.blocks.new(name=block_name)
        # Store full IFC GlobalId in the block description (DXF group code 4).
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

        # Each insert carries its own layer (may be IfcWindow, IfcWindow_Overhead, ...)
        for pos, rot, layer in block_inserts.get(block_name, []):
            msp.add_blockref(block_name, pos,
                             dxfattribs={"rotation": rot, "layer": layer})

    # flat wall edges (wall_mode='flat')
    for p0, p1, layer in flat_edges:
        msp.add_line(p0, p1, dxfattribs={"layer": layer})

    # wall polygons (wall_mode='shapely')
    # Pre-process all wall groups into (outline_layer, hatch_layer, geoms_list).
    # Then write in two passes: hatches first (bottom of draw order), outlines on top.
    wall_geom_groups = []
    for (ifc_class, material, layer), polys in wall_polys_by_key.items():
        if not polys:
            continue
        try:
            expanded = [p.buffer(SNAP_TOL, join_style=2) for p in polys]
            merged   = shapely.ops.unary_union(expanded).buffer(-SNAP_TOL, join_style=2)
        except Exception:
            merged = polys[0] if len(polys) == 1 else None
        if merged is None:
            continue
        is_section    = layer.endswith("_Section")
        outline_layer = layer
        hatch_layer   = f"{ifc_class}_Hatches"
        geoms = list(merged.geoms) if merged.geom_type == 'MultiPolygon' else [merged]
        wall_geom_groups.append((outline_layer, hatch_layer, is_section, geoms))

    # Pass 1 -- hatches (drawn first -> below everything else)
    for outline_layer, hatch_layer, is_section, geoms in wall_geom_groups:
        if not is_section:
            continue
        for poly in geoms:
            if poly.geom_type != 'Polygon':
                continue
            exterior = [(float(x), float(y)) for x, y in poly.exterior.coords[:-1]]
            holes    = [[(float(x), float(y)) for x, y in ring.coords[:-1]]
                        for ring in poly.interiors]
            if len(exterior) < 3:
                continue
            hatch = msp.add_hatch(dxfattribs={"layer": hatch_layer, "color": 256})
            hatch.set_solid_fill(color=256)
            hatch.paths.add_polyline_path(exterior, is_closed=True, flags=1)
            for hole in holes:
                hatch.paths.add_polyline_path(hole, is_closed=True, flags=16)

    # Pass 2 -- outlines (drawn last -> on top of hatches)
    for outline_layer, hatch_layer, is_section, geoms in wall_geom_groups:
        for poly in geoms:
            if poly.geom_type != 'Polygon':
                continue
            exterior = [(float(x), float(y)) for x, y in poly.exterior.coords[:-1]]
            holes    = [[(float(x), float(y)) for x, y in ring.coords[:-1]]
                        for ring in poly.interiors]
            msp.add_lwpolyline(exterior,
                               dxfattribs={"closed": True, "layer": outline_layer})
            for hole in holes:
                msp.add_lwpolyline(hole,
                                   dxfattribs={"closed": True, "layer": outline_layer})

    # zoom extents
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

    # Bucket D: annotations
    if annotations and cam_inv_np is not None:
        _write_dimension_annotations(msp, doc, annotations, cam_inv_np, scale_factor)
        _write_text_annotations(msp, doc, annotations, cam_inv_np, scale_factor,
                                current_scale_handle)

    doc.saveas(output_path)


# ---------------------------------------------------------------------------
# export_drawing -- public API entry point
# ---------------------------------------------------------------------------

WALL_MODES = ("flat", "shapely")


def export_drawing(ifc, drawing, pset, output_path, wall_mode="shapely",
                   template_path=None):
    """Export a single Bonsai drawing to DXF.

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
        pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    _SECTION_CLASSES = frozenset({"IfcWall", "IfcWallStandardCase"})

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
    wall_polys_by_key = {}  # (ifc_class, material, layer) -> [Polygon, ...]
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
                    key = (ifc_class, material, rec.layer)
                    wall_polys_by_key.setdefault(key, []).append(poly)
                    bucket_b += 1
                    bucket_b_classes[ifc_class] = bucket_b_classes.get(ifc_class, 0) + 1
                    processed = True
            except Exception:
                pass
        else:
            plan_repr_b, _ = find_plan_repr(element, target_view)
            if plan_repr_b is not None:
                try:
                    verts, edges, _a, _c, _el = _extract_local_curves(element, plan_repr_b)
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
        material  = get_material_name(element)
        gid       = element.GlobalId
        wm        = world_matrix_col_major(element)
        placed    = False

        try:
            ifc_type, block_name = get_type_block_name(element)
            if block_name is None:
                # No shared type: use "{IfcClass}_{GlobalId[:8]}" -- readable + unique
                block_name  = f"{ifc_class}_{gid[:8]}"
                block_gid   = gid
            else:
                block_gid   = ifc_type.GlobalId

            if block_name not in seen_blocks:
                verts, edges, arcs, circles, ellipses = _extract_local_curves(
                    element, rec.plan_repr
                )
                if verts or edges or arcs or circles or ellipses:
                    lines = _project_local_to_lines(verts or [], edges or [], _cam_R)
                    arcs_blk = [
                        (float((_cam_R @ np.array([cx, cy, 0.]))[0]),
                         float((_cam_R @ np.array([cx, cy, 0.]))[1]),
                         r, (a_s + _cam_rot_deg) % 360, (a_e + _cam_rot_deg) % 360)
                        for cx, cy, r, a_s, a_e in arcs
                    ]
                    circles_blk = [
                        (float((_cam_R @ np.array([cx, cy, 0.]))[0]),
                         float((_cam_R @ np.array([cx, cy, 0.]))[1]), r)
                        for cx, cy, r in circles
                    ]
                    ellipses_blk = []
                    for cx, cy, maj_x, maj_y, ratio, t1, t2 in ellipses:
                        c   = _cam_R @ np.array([cx, cy, 0.0])
                        maj = _cam_R @ np.array([maj_x, maj_y, 0.0])
                        ellipses_blk.append((float(c[0]), float(c[1]),
                                              float(maj[0]), float(maj[1]), ratio, t1, t2))
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
                bucket_a += 1
                bucket_a_classes[ifc_class] = bucket_a_classes.get(ifc_class, 0) + 1
                placed = True
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
        n_sec   = sum(len(v) for (_, _, lyr), v in wall_polys_by_key.items()
                      if lyr.endswith("_Section"))
        print(f"  Wall polys : {n_polys} total ({n_sec} section, {n_polys-n_sec} view)"
              f"  in {len(wall_polys_by_key)} groups")

    annotations = _get_drawing_annotations(ifc, drawing)
    if annotations:
        print(f"  Annotations: {len(annotations)}"
              f" ({sum(1 for a in annotations if a.ObjectType=='DIMENSION')} dims)")

    t0 = time.perf_counter()
    scale_factor_val = _parse_scale_factor(pset.get("Scale", "")) or 0.01
    _write_dxf(output_path, block_defs, block_order, block_inserts,
               flat_edges, wall_polys_by_key,
               annotations=annotations, cam_inv_np=_cam_inv_np,
               template_path=template_path, scale_factor=scale_factor_val,
               drawing_name=getattr(drawing, "Name", None),
               drawing_identification=getattr(drawing, "Identification", None),
               drawing_scale=human_scale)
    elapsed = time.perf_counter() - t0
    size_kb = os.path.getsize(output_path) // 1024
    print(f"  DXF gen    : {elapsed:.2f}s")
    print(f"  >> {output_path}  ({size_kb} KB)")
    return output_path


# ---------------------------------------------------------------------------
# render_preview (optional)
# ---------------------------------------------------------------------------

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
        print(f"  (preview skipped -- {e})")
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
