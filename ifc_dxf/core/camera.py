"""Camera and projection math — no IFC schema, no DXF, no bpy."""

import math
import multiprocessing

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


def camera_pos_dir_ref(drawing):
    """Return (pos, view_dir, ref_dir) tuples for ifcopenshell.geom's
    SvgSerializer.addDrawing(pos, dir, ref, name, include_projection).

    ref_dir must be the placement's local +X axis (not +Y): empirically
    verified against this module's own camera_matrix_inv_col_major
    projection -- passing local Y here rotates the HLR output 90 degrees
    relative to the rest of the pipeline. With local X, HLR path
    coordinates come out identical (to float precision) to the coordinates
    produced by the approximate pipeline's camera-space projection.
    """
    m = _placement_matrix(drawing)
    pos      = tuple(float(x) for x in m[:3, 3])
    view_dir = tuple(float(x) for x in -m[:3, 2])
    ref_dir  = tuple(float(x) for x in m[:3, 0])
    return pos, view_dir, ref_dir


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

    Fast path only: uses element origin as proxy for full bounding box —
    correct for point-like elements (furniture, doors) but misses elements
    whose origin is outside while their body extends into view (long walls).
    Use filter_elements_in_frustum for full culling with the geometry-AABB
    second pass.
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


def _aabb_overlap_pass(ifc, elements, frustum):
    """Return the subset of `elements` whose world-space geometry AABB overlaps
    the frustum bbox.

    One batched ifcopenshell.geom.iterator run (multicore, world coords) over
    all candidates — far cheaper than per-element create_shape. Elements the
    iterator yields no shape for are excluded (their origin already failed the
    fast test and there is no geometry evidence to rescue them).
    """
    by_guid = {e.GlobalId: e for e in elements if hasattr(e, "GlobalId")}
    if not by_guid:
        return set()
    x_min, x_max, y_min, y_max, z_min, z_max = frustum

    kept = set()
    try:
        s = ifcopenshell.geom.settings()
        s.set('use-world-coords', True)
        it = ifcopenshell.geom.iterator(
            s, ifc, multiprocessing.cpu_count(), include=list(by_guid.values())
        )
        if it.initialize():
            while True:
                shape = it.get()
                elem = by_guid.get(shape.guid)
                if elem is not None:
                    v = np.array(shape.geometry.verts).reshape(-1, 3)
                    if len(v) and not (
                        v[:, 0].max() < x_min or v[:, 0].min() > x_max or
                        v[:, 1].max() < y_min or v[:, 1].min() > y_max or
                        v[:, 2].max() < z_min or v[:, 2].min() > z_max
                    ):
                        kept.add(elem)
                if not it.next():
                    break
    except Exception:
        pass
    return kept


def filter_elements_in_frustum(ifc, elements, frustum):
    """Frustum culling in two passes; returns (kept_elements, n_rescued).

    Pass 1: cheap ObjectPlacement-origin test (element_in_frustum) — admits
    most in-view elements without touching geometry.
    Pass 2: elements whose origin fell outside get a real world-AABB overlap
    test from tessellated body geometry (batched iterator). This keeps long
    walls/slabs/beams whose placement origin lies outside the view but whose
    body extends into it — the failure mode of the origin-only test.
    """
    if frustum is None:
        return set(elements), 0
    inside = set()
    failed = []
    for e in elements:
        if element_in_frustum(e, frustum):
            inside.add(e)
        else:
            failed.append(e)
    rescued = _aabb_overlap_pass(ifc, failed, frustum) if failed else set()
    return inside | rescued, len(rescued)


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
