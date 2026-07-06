"""Shared DXF writing: _write_dxf, render_preview.

Consumes plain data structures (block defs, wall polygons, annotations, ...)
produced by either the approximate or accurate pipeline. No pipeline-specific
extraction logic lives here.
"""

import os

try:
    import shapely
    import shapely.ops
    _SHAPELY_AVAILABLE = True
except ImportError:
    _SHAPELY_AVAILABLE = False

from .materials import classify_material_color
from .dxf_template import (
    _populate_scale_list,
    _resolve_text_font,
    _fill_cartiglio,
)
from .annotations import (
    _write_dimension_annotations,
    _write_text_annotations,
)


# ---------------------------------------------------------------------------
# _write_dxf
# ---------------------------------------------------------------------------

def _dedup_ring(pts, tol=0.0005):
    """Drop consecutive duplicate vertices (and a wrap-around closing duplicate).

    Shapely buffer/union with mitre joins can leave coincident vertices in a
    ring; a HATCH boundary polyline with those is rejected by BricsCAD's audit
    ("Polyline Hatch boundary has duplicated vertices"). This collapses points
    within `tol` (default 0.5 mm, matching SNAP_TOL) to a clean ring.
    """
    out = []
    for x, y in pts:
        if out and abs(x - out[-1][0]) <= tol and abs(y - out[-1][1]) <= tol:
            continue
        out.append((x, y))
    # Ring endpoints are stored open here (callers pass coords[:-1]); still guard
    # against the first/last coinciding after the pass above.
    if len(out) >= 2 and abs(out[0][0] - out[-1][0]) <= tol \
            and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def _write_dxf(output_path, block_defs, block_order, block_inserts,
               flat_edges, wall_polys_by_key,
               annotations=None, cam_inv_np=None,
               template_path=None, scale_factor=0.01,
               drawing_name=None, drawing_identification=None,
               drawing_scale=None, footprint_polys=None,
               wall_layer_polys=None, wall_subdivision_lines=None,
               direct_entities=None):
    """Write all collected drawing data to a DXF file using ezdxf.

    When template_path is provided the document is cloned from the template
    (layers, dimstyles, layouts already configured); otherwise a minimal
    document is created with basic layer defaults.

    block_defs:    name -> {ifc_class, material, lines, arcs, circles, ellipses}
    block_order:   list of block names in insertion order
    block_inserts: name -> [(pos_2d, rot_deg, layer), ...]
    direct_entities: [{layer, polylines, arcs, circles, ellipses}, ...] --
                   world-space geometry for elements that don't match a reusable
                   type symbol, drawn straight onto their IfcClass layer.
    flat_edges:    [(p0, p1, layer), ...]
    wall_polys_by_key: {(ifc_class, material, layer, z_top) -> [shapely Polygon, ...]}
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

    # Direct geometry (elements not matching a reusable type symbol): entities
    # drawn straight onto their IfcClass layer, inheriting its colour/linetype.
    for ent in (direct_entities or []):
        layer = ent["layer"]
        for pts in ent.get("polylines", []):
            if len(pts) < 2:
                continue
            closed = (len(pts) >= 4
                      and abs(pts[0][0] - pts[-1][0]) < SNAP_TOL
                      and abs(pts[0][1] - pts[-1][1]) < SNAP_TOL)
            msp.add_lwpolyline(pts[:-1] if closed else pts,
                               dxfattribs={"layer": layer, "closed": closed})
        for cx, cy, r, a_s, a_e in ent.get("arcs", []):
            msp.add_arc((cx, cy), r, a_s, a_e, dxfattribs={"layer": layer})
        for cx, cy, r in ent.get("circles", []):
            msp.add_circle((cx, cy), r, dxfattribs={"layer": layer})
        for cx, cy, mx, my, ratio, t1, t2 in ent.get("ellipses", []):
            msp.add_ellipse(center=(cx, cy, 0), major_axis=(mx, my, 0),
                            ratio=ratio, start_param=t1, end_param=t2,
                            dxfattribs={"layer": layer})

    # flat wall edges (wall_mode='flat')
    for p0, p1, layer in flat_edges:
        msp.add_line(p0, p1, dxfattribs={"layer": layer})

    # wall polygons (wall_mode='shapely')
    # Pre-process all wall groups into (outline_layer, hatch_layer, geoms_list).
    # Then write in two passes: hatches first (bottom of draw order), outlines on top.
    wall_geom_groups = []
    for (ifc_class, material, layer, z_top), polys in wall_polys_by_key.items():
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
        decompose_key = (ifc_class, layer, z_top)
        wall_geom_groups.append((outline_layer, hatch_layer, is_section, geoms, decompose_key))

    # Keys whose standard hatch was replaced by per-material-layer hatches (Pass 1b).
    # Elements without a material layer set (e.g. IfcColumn, or walls with no
    # IfcMaterialLayerSetUsage) are never decomposed and must keep their standard hatch.
    decomposed_keys = set()
    if wall_layer_polys:
        decomposed_keys = {
            (ifc_class, layer, z_top)
            for (ifc_class, _mat_name, layer, z_top) in wall_layer_polys.keys()
        }

    def _write_hatch(msp, hatch_layer, geoms):
        for poly in geoms:
            if poly.geom_type != 'Polygon':
                continue
            exterior = _dedup_ring([(float(x), float(y)) for x, y in poly.exterior.coords[:-1]], SNAP_TOL)
            holes    = [_dedup_ring([(float(x), float(y)) for x, y in ring.coords[:-1]], SNAP_TOL)
                        for ring in poly.interiors]
            if len(exterior) < 3:
                continue
            hatch = msp.add_hatch(dxfattribs={"layer": hatch_layer, "color": 256})
            hatch.set_solid_fill(color=256)
            hatch.paths.add_polyline_path(exterior, is_closed=True, flags=1)
            for hole in holes:
                if len(hole) < 3:
                    continue
                hatch.paths.add_polyline_path(hole, is_closed=True, flags=16)

    # Pass 1 -- hatches (drawn first -> below everything else)
    # Only groups actually decomposed into per-material-layer hatches (Pass 1b)
    # skip their standard hatch here; everything else (e.g. IfcColumn, or walls
    # without a material layer set) keeps its normal hatch.
    for outline_layer, hatch_layer, is_section, geoms, decompose_key in wall_geom_groups:
        if not is_section or decompose_key in decomposed_keys:
            continue
        _write_hatch(msp, hatch_layer, geoms)

    # Pass 1b -- per-material-layer hatches (IfcWall_Hatches_Calcestruzzo, …)
    # These layers don't exist in the template (material names are arbitrary),
    # so they're created on the fly and coloured by material keyword.
    if wall_layer_polys:
        for (ifc_class, mat_name, layer, _z_top), polys in wall_layer_polys.items():
            if not layer.endswith("_Section") or not polys:
                continue
            try:
                expanded = [p.buffer(SNAP_TOL, join_style=2) for p in polys]
                merged   = shapely.ops.unary_union(expanded).buffer(-SNAP_TOL, join_style=2)
            except Exception:
                merged = polys[0] if len(polys) == 1 else None
            if merged is None:
                continue
            suffix     = f"_{mat_name}" if mat_name else ""
            hatch_layer = f"{ifc_class}_Hatches{suffix}"
            try:
                dxf_layer = doc.layers.get(hatch_layer)
            except Exception:
                dxf_layer = doc.layers.add(hatch_layer)
            dxf_layer.color = classify_material_color(mat_name)
            geoms = list(merged.geoms) if merged.geom_type == 'MultiPolygon' else [merged]
            _write_hatch(msp, hatch_layer, geoms)

    # Pass 1c -- material-layer subdivision lines (fixed layer, styled by template)
    if wall_subdivision_lines:
        for line in wall_subdivision_lines:
            pts = [(float(x), float(y)) for x, y in line.coords]
            if len(pts) >= 2:
                msp.add_lwpolyline(pts, dxfattribs={"layer": "IfcWall_LayersSubdivision"})

    # Pass 2 -- outlines (drawn last -> on top of hatches)
    for outline_layer, hatch_layer, is_section, geoms, _decompose_key in wall_geom_groups:
        for poly in geoms:
            if poly.geom_type != 'Polygon':
                continue
            exterior = _dedup_ring([(float(x), float(y)) for x, y in poly.exterior.coords[:-1]], SNAP_TOL)
            holes    = [_dedup_ring([(float(x), float(y)) for x, y in ring.coords[:-1]], SNAP_TOL)
                        for ring in poly.interiors]
            if len(exterior) < 3:
                continue
            msp.add_lwpolyline(exterior,
                               dxfattribs={"closed": True, "layer": outline_layer})
            for hole in holes:
                if len(hole) < 3:
                    continue
                msp.add_lwpolyline(hole,
                                   dxfattribs={"closed": True, "layer": outline_layer})

    # Footprint polygons: closed LWPOLYLINE entities grouped per element
    if footprint_polys:
        for gid, layer, exterior, holes in footprint_polys:
            entities = [msp.add_lwpolyline(exterior, dxfattribs={"closed": True, "layer": layer})]
            for hole in holes:
                entities.append(msp.add_lwpolyline(hole, dxfattribs={"closed": True, "layer": layer}))
            doc.groups.new(f"fp_{gid[:8]}").extend(entities)

    # zoom extents
    xs, ys = [], []
    for _, _, _, geoms, _ in wall_geom_groups:
        for poly in geoms:
            if poly.geom_type == 'Polygon':
                xs.extend(x for x, y in poly.exterior.coords)
                ys.extend(y for x, y in poly.exterior.coords)
    for inserts in block_inserts.values():
        for pos, _rot, _layer in inserts:
            xs.append(float(pos[0])); ys.append(float(pos[1]))
    for _gid, _layer, exterior, _holes in (footprint_polys or []):
        xs.extend(p[0] for p in exterior)
        ys.extend(p[1] for p in exterior)
    for ent in (direct_entities or []):
        for pts in ent.get("polylines", []):
            xs.extend(p[0] for p in pts); ys.extend(p[1] for p in pts)
        for cx, cy, r, _a_s, _a_e in ent.get("arcs", []):
            xs.extend((cx - r, cx + r)); ys.extend((cy - r, cy + r))
        for cx, cy, r in ent.get("circles", []):
            xs.extend((cx - r, cx + r)); ys.extend((cy - r, cy + r))
        for cx, cy, _mx, _my, _ratio, _t1, _t2 in ent.get("ellipses", []):
            xs.append(cx); ys.append(cy)
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
        _write_dimension_annotations(msp, doc, annotations, cam_inv_np, scale_factor,
                                     current_scale_handle)
        _write_text_annotations(msp, doc, annotations, cam_inv_np, scale_factor,
                                current_scale_handle)

    # Clearing the template modelspace (delete_all_entities) leaves the sample
    # entities' OBJECTS-section satellites -- AcDbContextDataManager /
    # ACDB_ANNOTATIONSCALES dicts, per-scale context data, the SUN object --
    # orphaned with dangling owner handles; later steps can orphan more. ezdxf's
    # Auditor purges these cleanly (BricsCAD's audit otherwise flags/repairs
    # them). Our own generated content is well-formed and survives the audit.
    doc.audit()

    doc.saveas(output_path)


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
