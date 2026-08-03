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

from .layers import apply_role, ensure_layer, insert_layer, layer_name
from .dxf_template import (
    _populate_scale_list,
    _resolve_text_font,
    _fill_cartiglio,
)
from .annotations import (
    _write_dimension_annotations,
    _write_text_annotations,
)
from .xdata import ensure_xdata_appid, set_ifc_xdata


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
               direct_entities=None, section_patch_union=None):
    """Write all collected drawing data to a DXF file using ezdxf.

    When template_path is provided the document is cloned from the template
    (layers, dimstyles, layouts already configured); otherwise a minimal
    document is created with basic layer defaults.

    Layers name the IFC class; the drawing role (section/view/overhead) is
    written as an explicit style on each entity -- see core/layers.py.

    block_defs:    name -> {ifc_class, material, lines, arcs, circles, ellipses}
    block_order:   list of block names in insertion order
    block_inserts: name -> [(pos_2d, rot_deg, layer, gid, role), ...]
    direct_entities: [{layer, role, gid, ifc_class, polylines, arcs, circles,
                   ellipses}, ...] -- world-space geometry for elements that
                   don't match a reusable type symbol, drawn straight onto
                   their IfcClass layer.
    flat_edges:    [(p0, p1, layer, role), ...]
    wall_polys_by_key: {(ifc_class, material, role, z_top) ->
                   [(shapely Polygon, gid), ...]} -- `material` is None unless
                   the caller asked to fuse per material, in which case touching
                   walls of different materials stay separate groups.
    footprint_polys: [(gid, ifc_class, layer, exterior_pts, [hole_pts]), ...]
                   -- always drawn with the "view" role

    Every model-space entity that originates from IFC element(s) carries their
    identity as XDATA under appid IFC_DXF (see core/xdata.py) -- the DXF
    counterpart of Bonsai's SVG ifc:guid/class attributes. Fused wall
    outlines/hatches list every contributing GlobalId.
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

    # IFC identity travels on every entity as XDATA; the appid must be
    # registered or the audit pass strips it.
    ensure_xdata_appid(doc)

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

        # A block reads its appearance from its layer only: the INSERT stays
        # BYLAYER (no explicit colour/linetype/lineweight) and the BYBLOCK
        # content follows it. A symbol whose role is not the plain view says so
        # by its layer instead -- see layers.insert_layer. The INSERT carries
        # the *instance* GlobalId as XDATA (the block description above holds
        # the shared type's GlobalId).
        for pos, rot, layer, gid, role in block_inserts.get(block_name, []):
            layer = insert_layer(layer, role)
            ensure_layer(doc, layer, "overhead" if role == "overhead" else "class")
            ins = msp.add_blockref(block_name, pos,
                                   dxfattribs={"rotation": rot, "layer": layer})
            set_ifc_xdata(ins, bd.get("ifc_class"), gid)

    # Direct geometry (elements not matching a reusable type symbol): entities
    # drawn straight onto their IfcClass layer, styled by their role.
    for ent in (direct_entities or []):
        layer = ent["layer"]
        role  = ent.get("role", "view")
        gid   = ent.get("gid")
        cls   = ent.get("ifc_class")
        ensure_layer(doc, layer)
        for pts in ent.get("polylines", []):
            if len(pts) < 2:
                continue
            closed = (len(pts) >= 4
                      and abs(pts[0][0] - pts[-1][0]) < SNAP_TOL
                      and abs(pts[0][1] - pts[-1][1]) < SNAP_TOL)
            e = msp.add_lwpolyline(pts[:-1] if closed else pts,
                                   dxfattribs={"layer": layer, "closed": closed})
            apply_role(e, role)
            set_ifc_xdata(e, cls, gid)
        for cx, cy, r, a_s, a_e in ent.get("arcs", []):
            e = msp.add_arc((cx, cy), r, a_s, a_e, dxfattribs={"layer": layer})
            apply_role(e, role)
            set_ifc_xdata(e, cls, gid)
        for cx, cy, r in ent.get("circles", []):
            e = msp.add_circle((cx, cy), r, dxfattribs={"layer": layer})
            apply_role(e, role)
            set_ifc_xdata(e, cls, gid)
        for cx, cy, mx, my, ratio, t1, t2 in ent.get("ellipses", []):
            e = msp.add_ellipse(center=(cx, cy, 0), major_axis=(mx, my, 0),
                                ratio=ratio, start_param=t1, end_param=t2,
                                dxfattribs={"layer": layer})
            apply_role(e, role)
            set_ifc_xdata(e, cls, gid)

    # flat wall edges (wall_mode='flat')
    for p0, p1, layer, role in flat_edges:
        ensure_layer(doc, layer)
        e = msp.add_line(p0, p1, dxfattribs={"layer": layer})
        apply_role(e, role)

    # wall polygons (wall_mode='shapely')
    # Pre-process all wall groups into (outline_layer, hatch_layer, geoms_list).
    # Then write in two passes: hatches first (bottom of draw order), outlines on top.
    # Each geom is paired with the GlobalIds of the source polygons the union
    # fused into it (a merged run of walls legitimately lists several), so the
    # written outline/hatch entities can carry their IFC identity as XDATA.
    wall_geom_groups = []
    for (ifc_class, _material, role, z_top), poly_gids in wall_polys_by_key.items():
        if not poly_gids:
            continue
        polys = [p for p, _g in poly_gids]
        try:
            expanded = [p.buffer(SNAP_TOL, join_style=2) for p in polys]
            merged   = shapely.ops.unary_union(expanded).buffer(-SNAP_TOL, join_style=2)
        except Exception:
            merged = polys[0] if len(polys) == 1 else None
        if merged is None:
            continue
        outline_layer = layer_name(ifc_class)
        hatch_layer   = layer_name(ifc_class, "hatch")
        merged_polys = list(merged.geoms) if merged.geom_type == 'MultiPolygon' else [merged]
        geoms = []
        for g in merged_polys:
            gids, seen_gids = [], set()
            for p, gid in poly_gids:
                if not gid or gid in seen_gids:
                    continue
                try:
                    hit = p.intersects(g)
                except Exception:
                    hit = False
                if hit:
                    gids.append(gid)
                    seen_gids.add(gid)
            geoms.append((g, gids))
        decompose_key = (ifc_class, role, z_top)
        wall_geom_groups.append((outline_layer, hatch_layer, role, geoms, decompose_key))

    # Pinhole-void patching: HLR occasionally loses thin miter wedges at wall
    # joints, leaving small interior rings in the fused section polygons that
    # no element's output covers (unhatched slivers at corners). A hole is
    # filled only when ALL of: it is small (< 1 m2), the authored-geometry
    # union says the area is solid, and no other drawn group covers it.
    # Genuine voids (shafts, cavities between walls) fail one of the tests.
    if _SHAPELY_AVAILABLE and section_patch_union is not None and wall_geom_groups:
        # Only hatched (section) geometry counts as "covered": unhatched _View
        # outlines (e.g. a spatial zone boundary spanning the whole plan) must
        # not veto the patch.
        try:
            all_drawn = shapely.ops.unary_union(
                [g for _, _, grole, geoms, _ in wall_geom_groups
                 for g, _gids in geoms if grole == "section"])
        except Exception:
            all_drawn = None
        if all_drawn is not None:
            n_patched = 0
            for gi, (ol, hl, grole, geoms, dk) in enumerate(wall_geom_groups):
                if grole != "section":
                    continue
                new_geoms = []
                group_changed = False
                for poly, gids in geoms:
                    if poly.geom_type == 'Polygon' and poly.interiors:
                        keep = []
                        poly_changed = False
                        for ring in poly.interiors:
                            try:
                                hole = shapely.Polygon(ring)
                                if (hole.area < 1.0
                                        and hole.difference(section_patch_union).area < 1e-3
                                        and hole.difference(all_drawn).area > hole.area * 0.5):
                                    n_patched += 1
                                    poly_changed = True
                                    continue  # drop the ring -> fill the void
                            except Exception:
                                pass
                            keep.append(ring)
                        if poly_changed:
                            poly = shapely.Polygon(poly.exterior, keep)
                            group_changed = True
                    new_geoms.append((poly, gids))
                if group_changed:
                    wall_geom_groups[gi] = (ol, hl, grole, new_geoms, dk)
            if n_patched:
                print(f"  Hatch patch: filled {n_patched} pinhole void(s) "
                      f"confirmed solid by authored geometry")

    # Keys whose standard hatch was replaced by per-material-layer hatches (Pass 1b).
    # Elements without a material layer set (e.g. IfcColumn, or walls with no
    # IfcMaterialLayerSetUsage) are never decomposed and must keep their standard hatch.
    decomposed_keys = set()
    if wall_layer_polys:
        decomposed_keys = {
            (ifc_class, role, z_top)
            for (ifc_class, _mat_name, role, z_top) in wall_layer_polys.keys()
        }

    def _write_hatch(msp, hatch_layer, ifc_class, geoms):
        """geoms: [(Polygon, gids-or-None), ...]; gids become IFC_DXF XDATA."""
        for poly, gids in geoms:
            if poly.geom_type != 'Polygon':
                continue
            exterior = _dedup_ring([(float(x), float(y)) for x, y in poly.exterior.coords[:-1]], SNAP_TOL)
            holes    = [_dedup_ring([(float(x), float(y)) for x, y in ring.coords[:-1]], SNAP_TOL)
                        for ring in poly.interiors]
            if len(exterior) < 3:
                continue
            ensure_layer(doc, hatch_layer, "hatch")
            hatch = msp.add_hatch(dxfattribs={"layer": hatch_layer, "color": 256})
            hatch.set_solid_fill(color=256)
            hatch.paths.add_polyline_path(exterior, is_closed=True, flags=1)
            for hole in holes:
                if len(hole) < 3:
                    continue
                hatch.paths.add_polyline_path(hole, is_closed=True, flags=16)
            set_ifc_xdata(hatch, ifc_class, gids)

    # Pass 1 -- hatches (drawn first -> below everything else)
    # Only groups actually decomposed into per-material-layer hatches (Pass 1b)
    # skip their standard hatch here; everything else (e.g. IfcColumn, or walls
    # without a material layer set) keeps its normal hatch.
    for outline_layer, hatch_layer, role, geoms, decompose_key in wall_geom_groups:
        if role != "section" or decompose_key in decomposed_keys:
            continue
        _write_hatch(msp, hatch_layer, decompose_key[0], geoms)

    # Pass 1b -- per-material-layer hatches (IfcWall_Hatches_Calcestruzzo, …)
    # Material names are arbitrary, so these layers are created on the fly and
    # coloured by material keyword (unless the template already declares one).
    if wall_layer_polys:
        for (ifc_class, mat_name, role, _z_top), polys in wall_layer_polys.items():
            if role != "section" or not polys:
                continue
            try:
                expanded = [p.buffer(SNAP_TOL, join_style=2) for p in polys]
                merged   = shapely.ops.unary_union(expanded).buffer(-SNAP_TOL, join_style=2)
            except Exception:
                merged = polys[0] if len(polys) == 1 else None
            if merged is None:
                continue
            hatch_layer = layer_name(ifc_class, "hatch", material=mat_name)
            ensure_layer(doc, hatch_layer, "hatch", material=mat_name)
            # Material-layer strips carry no per-element gid (a strip may span
            # several fused walls) -- no XDATA on these.
            geoms = [(g, None) for g in
                     (merged.geoms if merged.geom_type == 'MultiPolygon' else [merged])]
            _write_hatch(msp, hatch_layer, ifc_class, geoms)

    # Pass 1c -- material-layer subdivision lines (fixed layer, styled by template)
    if wall_subdivision_lines:
        for line in wall_subdivision_lines:
            pts = [(float(x), float(y)) for x, y in line.coords]
            if len(pts) >= 2:
                msp.add_lwpolyline(pts, dxfattribs={"layer": "IfcWall_LayersSubdivision"})

    # Pass 2 -- outlines (drawn last -> on top of hatches)
    for outline_layer, hatch_layer, role, geoms, decompose_key in wall_geom_groups:
        ensure_layer(doc, outline_layer)
        for poly, gids in geoms:
            if poly.geom_type != 'Polygon':
                continue
            exterior = _dedup_ring([(float(x), float(y)) for x, y in poly.exterior.coords[:-1]], SNAP_TOL)
            holes    = [_dedup_ring([(float(x), float(y)) for x, y in ring.coords[:-1]], SNAP_TOL)
                        for ring in poly.interiors]
            if len(exterior) < 3:
                continue
            e = msp.add_lwpolyline(exterior,
                                   dxfattribs={"closed": True, "layer": outline_layer})
            apply_role(e, role)
            set_ifc_xdata(e, decompose_key[0], gids)
            for hole in holes:
                if len(hole) < 3:
                    continue
                e = msp.add_lwpolyline(hole,
                                       dxfattribs={"closed": True, "layer": outline_layer})
                apply_role(e, role)
                set_ifc_xdata(e, decompose_key[0], gids)

    # Footprint polygons: closed LWPOLYLINE entities grouped per element.
    # An element may contribute several entries (disjoint HLR loops), all
    # sharing its gid -- collect them into one GROUP per element.
    if footprint_polys:
        entities_by_gid = {}
        for gid, ifc_class, layer, exterior, holes in footprint_polys:
            entities = entities_by_gid.setdefault(gid, [])
            ensure_layer(doc, layer)
            e = msp.add_lwpolyline(exterior, dxfattribs={"closed": True, "layer": layer})
            apply_role(e, "view")
            set_ifc_xdata(e, ifc_class, gid)
            entities.append(e)
            for hole in holes:
                e = msp.add_lwpolyline(hole, dxfattribs={"closed": True, "layer": layer})
                apply_role(e, "view")
                set_ifc_xdata(e, ifc_class, gid)
                entities.append(e)
        for gid, entities in entities_by_gid.items():
            doc.groups.new(f"fp_{gid[:8]}").extend(entities)

    # zoom extents
    xs, ys = [], []
    for _, _, _, geoms, _ in wall_geom_groups:
        for poly, _gids in geoms:
            if poly.geom_type == 'Polygon':
                xs.extend(x for x, y in poly.exterior.coords)
                ys.extend(y for x, y in poly.exterior.coords)
    for inserts in block_inserts.values():
        for pos, _rot, _layer, _gid, _role in inserts:
            xs.append(float(pos[0])); ys.append(float(pos[1]))
    for _gid, _cls, _layer, exterior, _holes in (footprint_polys or []):
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
