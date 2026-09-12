"""Shared BLOCK/INSERT (and direct-geometry) construction from native IFC 2D
plan representations.

Used by both pipelines for elements with a usable Plan/Body/FootPrint
representation (see ifc_query.find_plan_repr): doors, windows, furniture,
sanitary fixtures, and any other Bucket-A-eligible class. Not used for
elements that need a true 3D section (walls, columns -- see approximate's
Shapely wall extraction, or accurate's HLR pipeline).

Placement rule:
  * If the element's geometry is inherited from its type (from_type) or is
    entirely mapped to the type (is_mapped_repr), one shared BLOCK is built per
    IfcTypeObject and every instance becomes an INSERT of it.
  * Otherwise the element does not match a reusable type symbol, so its geometry
    is drawn directly into the drawing on the IfcClass layer (LWPOLYLINE where
    segments chain up, else LINE/ARC/CIRCLE/ELLIPSE). No per-instance block.
"""

from collections import defaultdict

import numpy as np

from .ifc_query import find_plan_repr, is_mapped_repr, get_type_block_name, get_material_name
from .curves import _extract_local_curves
from .dxf_template import _project_local_to_lines, _compute_insert
from .camera import world_matrix_col_major


def place_plan_symbol(element, layer, target_view, crease_angle_deg,
                       cam_R, cam_inv_np, cam_rot_deg,
                       block_defs, block_order, block_inserts, seen_blocks,
                       direct_entities, role="view", unit_scale=1.0):
    """Place one element via its native 2D plan representation.

    Elements whose geometry comes from the type share one BLOCK (built once)
    placed per instance as an INSERT. Elements with their own bespoke geometry
    are appended to direct_entities and drawn straight onto the IfcClass layer.

    Mutates block_defs/block_order/block_inserts/seen_blocks and direct_entities
    in place. Returns True if the element was placed, False if it has no usable
    plan representation or yields no geometry.
    """
    plan_repr, from_type = find_plan_repr(element, target_view)
    if plan_repr is None:
        return False

    ifc_class = element.is_a()
    material  = get_material_name(element)
    gid       = element.GlobalId
    wm        = world_matrix_col_major(element)

    # A shared BLOCK is built only when the geometry is inherited from (or fully
    # mapped to) the element's type -- i.e. the instance matches a reusable type
    # symbol. Bespoke per-instance geometry is drawn directly instead.
    block_name = None
    block_gid  = gid
    if from_type or is_mapped_repr(plan_repr):
        ifc_type, shared_name = get_type_block_name(element)
        if shared_name is not None:
            block_name = shared_name
            block_gid  = ifc_type.GlobalId

    if block_name is not None:
        # Shared type BLOCK: extract geometry once, then INSERT per instance.
        if block_name not in seen_blocks:
            geom = _extract_block_geom(element, plan_repr, crease_angle_deg,
                                       cam_R, cam_rot_deg, unit_scale)
            if geom is None:
                return False
            geom.update({"ifc_class": ifc_class, "material": material,
                         "globalid": block_gid})
            block_defs[block_name] = geom
            block_order.append(block_name)
            seen_blocks[block_name] = True
        pos, rot = _compute_insert(wm, cam_inv_np)
        # The tuple carries the *instance* GlobalId (-> INSERT XDATA); the
        # block definition's description holds the shared type's GlobalId.
        block_inserts.setdefault(block_name, []).append((pos, rot, layer, gid, role))
        return True

    # Direct draw on the IfcClass layer: bake the INSERT transform into the
    # geometry so output matches what a block+insert would have produced.
    geom = _extract_block_geom(element, plan_repr, crease_angle_deg,
                               cam_R, cam_rot_deg, unit_scale)
    if geom is None:
        return False
    pos, rot = _compute_insert(wm, cam_inv_np)
    _append_direct(direct_entities, layer, pos, rot, geom,
                   gid=gid, ifc_class=ifc_class, role=role)
    return True


def _extract_block_geom(element, plan_repr, crease_angle_deg, cam_R, cam_rot_deg,
                        unit_scale=1.0):
    """Extract block-local 2D geometry (lines/arcs/circles/ellipses) or None.

    Coordinates are element-local geometry rotated into the camera plane by
    cam_R; the element's world placement is applied later by an INSERT (block
    path) or by _append_direct (direct path).
    """
    # cam_R maps world to camera, so its third row is the view axis in world
    # coords -- what tells a profile facing the viewer from one seen edge-on.
    verts, edges, arcs, circles, ellipses = _extract_local_curves(
        element, plan_repr, crease_angle_deg, unit_scale,
        view_axis=np.asarray(cam_R)[2, :]
    )
    if not (verts or edges or arcs or circles or ellipses):
        return None
    lines = _project_local_to_lines(verts or [], edges or [], cam_R)
    arcs_blk = [
        (float((cam_R @ np.array([cx, cy, 0.]))[0]),
         float((cam_R @ np.array([cx, cy, 0.]))[1]),
         r, (a_s + cam_rot_deg) % 360, (a_e + cam_rot_deg) % 360)
        for cx, cy, r, a_s, a_e in arcs
    ]
    circles_blk = [
        (float((cam_R @ np.array([cx, cy, 0.]))[0]),
         float((cam_R @ np.array([cx, cy, 0.]))[1]), r)
        for cx, cy, r in circles
    ]
    ellipses_blk = []
    for cx, cy, maj_x, maj_y, ratio, t1, t2 in ellipses:
        c   = cam_R @ np.array([cx, cy, 0.0])
        maj = cam_R @ np.array([maj_x, maj_y, 0.0])
        ellipses_blk.append((float(c[0]), float(c[1]),
                              float(maj[0]), float(maj[1]), ratio, t1, t2))
    return {"lines": lines, "arcs": arcs_blk,
            "circles": circles_blk, "ellipses": ellipses_blk}


def _append_direct(direct_entities, layer, pos, rot_deg, geom,
                   gid=None, ifc_class=None, role="view"):
    """Bake an INSERT transform (pos, rot_deg) into geom and record it directly.

    Reproduces exactly what a DXF INSERT would do to the block-local geometry,
    so an element draws identically whether it went through a shared block or
    not. Line segments are chained into polylines where they share endpoints.
    """
    theta  = np.radians(rot_deg)
    ct, st = np.cos(theta), np.sin(theta)

    def xf(x, y):  # rotate about origin then translate to the insert point
        return (ct * x - st * y + pos[0], st * x + ct * y + pos[1])

    lines     = [(xf(*p0), xf(*p1)) for p0, p1 in geom["lines"]]
    polylines = _chain_segments(lines)

    arcs    = [(*xf(cx, cy), r, (a_s + rot_deg) % 360, (a_e + rot_deg) % 360)
               for cx, cy, r, a_s, a_e in geom["arcs"]]
    circles = [(*xf(cx, cy), r) for cx, cy, r in geom["circles"]]
    ellipses = []
    for cx, cy, mx, my, ratio, t1, t2 in geom["ellipses"]:
        ecx, ecy = xf(cx, cy)
        # major axis is a direction vector -> rotate only, no translation
        rmx = ct * mx - st * my
        rmy = st * mx + ct * my
        ellipses.append((ecx, ecy, rmx, rmy, ratio, t1, t2))

    direct_entities.append({
        "layer": layer,
        "role": role,
        "gid": gid,
        "ifc_class": ifc_class,
        "polylines": polylines,
        "arcs": arcs,
        "circles": circles,
        "ellipses": ellipses,
    })


def _q(pt, tol):
    """Quantize a 2D point to an integer grid so shared endpoints match."""
    return (round(pt[0] / tol), round(pt[1] / tol))


def _chain_segments(segments, tol=1e-6):
    """Chain 2D line segments into polylines by matching shared endpoints.

    Returns a list of polylines, each a list of >= 2 points. Isolated segments
    become 2-point polylines. At a junction where >2 segments meet the walk
    simply stops, so ambiguous fans are split into several polylines rather than
    guessed. A closed loop ends with its start point repeated.
    """
    if not segments:
        return []

    adj = defaultdict(list)  # point-key -> segment indices touching it
    for i, (a, b) in enumerate(segments):
        adj[_q(a, tol)].append(i)
        adj[_q(b, tol)].append(i)
    used = [False] * len(segments)

    def other_end(seg, key):
        a, b = seg
        return b if _q(a, tol) == key else a

    def next_unused(key):
        for j in adj[key]:
            if not used[j]:
                return j
        return None

    polylines = []
    for i in range(len(segments)):
        if used[i]:
            continue
        used[i] = True
        a, b  = segments[i]
        chain = [a, b]

        cur = _q(b, tol)
        while (j := next_unused(cur)) is not None:
            used[j] = True
            nxt = other_end(segments[j], cur)
            chain.append(nxt)
            cur = _q(nxt, tol)
            if cur == _q(chain[0], tol):
                break

        cur = _q(a, tol)
        while (j := next_unused(cur)) is not None:
            used[j] = True
            prev = other_end(segments[j], cur)
            chain.insert(0, prev)
            cur = _q(prev, tol)
            if cur == _q(chain[-1], tol):
                break

        polylines.append(chain)
    return polylines
