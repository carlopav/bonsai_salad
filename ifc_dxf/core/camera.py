"""Camera and projection math — no IFC schema, no DXF, no bpy."""

import math

import numpy as np
import ifcopenshell.util.placement
import ifcopenshell.geom


def _placement_matrix(drawing):
    """Return the 4x4 numpy placement matrix for the drawing annotation."""
    return ifcopenshell.util.placement.get_local_placement(drawing.ObjectPlacement)


def camera_matrix_inv_col_major(drawing):
    """Column-major flat list of the inverse camera world matrix.

    Required by the projection pipeline.  Column-major = Fortran order in numpy,
    matching the expected layout (glam DMat4::from_cols_array).
    """
    m = _placement_matrix(drawing)
    m_inv = np.linalg.inv(m)
    return list(m_inv.flatten(order="F"))


def camera_dir_pos(drawing):
    """Return (camera_dir, camera_pos) from IFC placement.

    The drawing plane normal = local +Z of the placement.
    The camera looks along local -Z (downward for PLAN_VIEW).
    """
    m = _placement_matrix(drawing)
    cam_dir = list(-m[:3, 2].astype(float))   # local -Z in world
    cam_pos = list( m[:3, 3].astype(float))   # origin in world
    return cam_dir, cam_pos


def get_camera_frustum_bbox(drawing):
    """Return (x_min, x_max, y_min, y_max, z_min, z_max) in world coordinates
    from the drawing camera's body geometry (IfcCsgSolid / IfcExtrudedAreaSolid).

    ifcopenshell.geom does not apply ObjectPlacement for IfcAnnotation, so we
    read local coords and transform manually with the placement matrix.
    Returns None if the body geometry is unavailable.
    """
    try:
        s = ifcopenshell.geom.settings()
        s.set('use-world-coords', False)
        shape = ifcopenshell.geom.create_shape(s, drawing)
        v_local = np.array(shape.geometry.verts).reshape(-1, 3)
        m = ifcopenshell.util.placement.get_local_placement(drawing.ObjectPlacement)
        ones = np.ones((len(v_local), 1))
        v_world = (m @ np.hstack([v_local, ones]).T).T[:, :3]
        return (v_world[:, 0].min(), v_world[:, 0].max(),
                v_world[:, 1].min(), v_world[:, 1].max(),
                v_world[:, 2].min(), v_world[:, 2].max())
    except Exception:
        return None


def _element_origin(element):
    """Return (x, y, z) world origin of element's ObjectPlacement, or None."""
    try:
        placement = getattr(element, "ObjectPlacement", None)
        if placement is None:
            return None
        m = ifcopenshell.util.placement.get_local_placement(placement)
        return float(m[0, 3]), float(m[1, 3]), float(m[2, 3])
    except Exception:
        return None


def element_in_frustum(element, frustum):
    """Return True if element's ObjectPlacement origin is inside the frustum bbox.

    Uses element origin as proxy for full bounding box — fast and correct for
    point-like elements (furniture, doors). For large elements (walls, slabs)
    the ObjectPlacement is at the element's base, which is the discriminating
    coordinate for storey assignment.
    """
    if frustum is None:
        return True
    x_min, x_max, y_min, y_max, z_min, z_max = frustum
    pt = _element_origin(element)
    if pt is None:
        return True  # no placement -> include (annotations, etc.)
    x, y, z = pt
    return (x_min <= x <= x_max and
            y_min <= y <= y_max and
            z_min <= z <= z_max)


def world_matrix_col_major(element):
    """Return column-major flat list of the element world placement matrix."""
    try:
        placement = getattr(element, "ObjectPlacement", None)
        if placement is None:
            return list(np.eye(4).flatten(order="F"))
        m = ifcopenshell.util.placement.get_local_placement(placement)
        return list(m.flatten(order="F"))
    except Exception:
        return list(np.eye(4).flatten(order="F"))
