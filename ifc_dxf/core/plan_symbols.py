"""Shared BLOCK/INSERT construction from native IFC 2D plan representations.

Used by both pipelines for elements with a usable Plan/Body/FootPrint
representation (see ifc_query.find_plan_repr): doors, windows, furniture,
sanitary fixtures, and any other Bucket-A-eligible class. Not used for
elements that need a true 3D section (walls, columns -- see approximate's
Shapely wall extraction, or accurate's HLR pipeline).
"""

import numpy as np

from .ifc_query import find_plan_repr, is_mapped_repr, get_type_block_name, get_material_name
from .curves import _extract_local_curves
from .dxf_template import _project_local_to_lines, _compute_insert
from .camera import world_matrix_col_major


def place_plan_symbol(element, layer, target_view, crease_angle_deg,
                       cam_R, cam_inv_np, cam_rot_deg,
                       block_defs, block_order, block_inserts, seen_blocks):
    """Place one element via its native 2D plan representation.

    Shares one BLOCK across instances of the same IfcTypeObject, matching the
    approximate pipeline's Bucket A rule:
      a) from_type=True: repr found in the type's RepresentationMaps directly.
      b) is_mapped_repr: element has its own Representation but all items are
         IfcMappedItem delegating to the type.

    Mutates block_defs/block_order/block_inserts/seen_blocks in place so
    repeated calls for elements sharing a type only build the block geometry
    once. Returns True if the element was placed (BLOCK+INSERT written),
    False if it has no usable plan representation or yields no geometry.
    """
    plan_repr, from_type = find_plan_repr(element, target_view)
    if plan_repr is None:
        return False

    ifc_class = element.is_a()
    material  = get_material_name(element)
    gid       = element.GlobalId
    wm        = world_matrix_col_major(element)

    block_name = None
    block_gid  = gid
    if from_type or is_mapped_repr(plan_repr):
        ifc_type, shared_name = get_type_block_name(element)
        if shared_name is not None:
            block_name = shared_name
            block_gid  = ifc_type.GlobalId

    if block_name is None:
        block_name = f"{ifc_class}_{gid[:8]}"
        block_gid  = gid

    if block_name not in seen_blocks:
        verts, edges, arcs, circles, ellipses = _extract_local_curves(
            element, plan_repr, crease_angle_deg
        )
        if not (verts or edges or arcs or circles or ellipses):
            return False
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
        block_defs[block_name] = {
            "ifc_class": ifc_class, "material": material,
            "lines": lines, "arcs": arcs_blk,
            "circles": circles_blk, "ellipses": ellipses_blk,
            "globalid": block_gid,
        }
        block_order.append(block_name)
        seen_blocks[block_name] = True

    if block_name in seen_blocks:
        pos, rot = _compute_insert(wm, cam_inv_np)
        block_inserts.setdefault(block_name, []).append((pos, rot, layer))
        return True
    return False
