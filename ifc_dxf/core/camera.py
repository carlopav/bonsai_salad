"""Camera and projection math — no IFC schema, no DXF, no bpy."""

import math
import multiprocessing

import numpy as np
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.geom

from .units import project_unit_scale


# ifcopenshell renamed this between 0.8 and 0.9, and the add-on is used from
# both: Blender 5.2 links the 0.8 lane, Blender 5.1 the 0.9 one. Same meaning
# either way -- an IfcObjectPlacement composed with its parents into a 4x4
# matrix in project units (0.9 grew a should_return_si flag that defaults off).
_placement_to_matrix = getattr(
    ifcopenshell.util.placement, "get_placement", None
) or ifcopenshell.util.placement.get_local_placement


def _placement_matrix(drawing):
    """Return the 4x4 numpy placement matrix for the drawing annotation."""
    return _placement_to_matrix(drawing.ObjectPlacement)


# A reflected ceiling plan looks up, so its drawing is the plan reflected in Y.
# The SVG serializer is told separately (svg-mirror-y / setMirrorY), and Bonsai
# keeps the two halves of a drawing in one frame by putting the same reflection
# in the camera itself -- tool.Drawing.get_camera_shape_matrix negates the
# camera's Y for a REFLECTED_PLAN_VIEW, so geometry, annotations and symbols all
# inherit it. With the mirror on the serializer alone, an RCP came out with its
# linework reflected and its text left where the unmirrored projection put it:
# all 48 light tags on the wrong side of the plan.
_MIRRORED_TARGET_VIEWS = frozenset({"REFLECTED_PLAN_VIEW"})

# Reflect the projected Y. Composed on the left of the camera inverse, this is
# the same drawing as negating the camera's own Y axis before inverting.
_MIRROR_Y_4 = np.diag([1.0, -1.0, 1.0, 1.0])
_MIRROR_Y_3 = np.diag([1.0, -1.0, 1.0])


def drawing_target_view(drawing):
    """Return the drawing's EPset_Drawing TargetView, defaulting to PLAN_VIEW."""
    try:
        return ifcopenshell.util.element.get_pset(
            drawing, "EPset_Drawing", "TargetView") or "PLAN_VIEW"
    except Exception:
        return "PLAN_VIEW"


def projection_is_mirrored(cam_inv_np):
    """True when the camera projection reflects as well as rotates.

    Read off the matrix rather than passed around, so every consumer of a
    camera inverse can tell without the target view having to reach it.
    """
    return bool(np.linalg.det(np.asarray(cam_inv_np, dtype=float)[:3, :3]) < 0.0)


def camera_matrix_inv_col_major(drawing):
    """Column-major flat list of the inverse camera world matrix.

    Required by the projection pipeline.  Column-major = Fortran order in numpy,
    matching the expected layout (glam DMat4::from_cols_array).

    For a mirrored target view the reflection is baked in here -- see
    _MIRRORED_TARGET_VIEWS.
    """
    m = _placement_matrix(drawing)
    m_inv = np.linalg.inv(m)
    if drawing_target_view(drawing) in _MIRRORED_TARGET_VIEWS:
        m_inv = _MIRROR_Y_4 @ m_inv
    return list(m_inv.flatten(order="F"))


def camera_plan_rotation(cam_inv_np):
    """Return (cam_R, cam_rot_deg): the camera's in-plane turn, reflection removed.

    plan_symbols builds block-local geometry with cam_R and turns arc start/end
    angles by cam_rot_deg, which only describes a rotation -- a reflection has no
    such angle. So the reflection a mirrored target view adds is taken back out
    here, and reapplied per instance by dxf_template._compute_insert, which
    reports it as the INSERT's yscale -1. cam_R's third row (the view axis) and
    its Z column are untouched either way, so the view-axis and plan-parallel
    tests read the same.
    """
    cam_R = np.asarray(cam_inv_np, dtype=float)[:3, :3]
    if projection_is_mirrored(cam_R):
        cam_R = _MIRROR_Y_3 @ cam_R
    x_proj = cam_R @ np.array([1.0, 0.0, 0.0])
    return cam_R, float(np.degrees(np.arctan2(float(x_proj[1]),
                                              float(x_proj[0]))))


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


def get_camera_frustum_bbox(drawing, unit_scale=1.0):
    """Return (x_min, x_max, y_min, y_max, z_min, z_max) in world coordinates
    from the drawing camera's body geometry (IfcCsgSolid / IfcExtrudedAreaSolid).

    ifcopenshell.geom does not apply ObjectPlacement for IfcAnnotation, so we
    read local coords and transform manually with the placement matrix. The
    engine's metres are first converted to the placement's project unit
    (`unit_scale` metres each).
    Returns None if the body geometry is unavailable.
    """
    try:
        s = ifcopenshell.geom.settings()
        s.set('use-world-coords', False)
        shape = ifcopenshell.geom.create_shape(s, drawing)
        v_local = np.array(shape.geometry.verts).reshape(-1, 3) / unit_scale
        m = _placement_to_matrix(drawing.ObjectPlacement)
        ones = np.ones((len(v_local), 1))
        v_world = (m @ np.hstack([v_local, ones]).T).T[:, :3]
        return (v_world[:, 0].min(), v_world[:, 0].max(),
                v_world[:, 1].min(), v_world[:, 1].max(),
                v_world[:, 2].min(), v_world[:, 2].max())
    except Exception:
        return None


def camera_body_local_extents(drawing, unit_scale=1.0):
    """Return (x_min, x_max, y_min, y_max) of the camera body geometry in the
    drawing placement's local frame, in the project unit (`unit_scale` metres
    each), or None if unavailable.

    Used to map the native SVG serializer's paper-frame output back to
    camera space deterministically (no empirical calibration): with the
    ElevationRefGuid configuration the SVG origin is the top-left corner of
    this box, x growing right and y growing down, at 1000*scale units per
    metre. So, with f = 1000*scale*unit_scale svg units per project unit,
    x_cam = x_svg/f + x_min and y_cam = y_max - y_svg/f.
    """
    try:
        s = ifcopenshell.geom.settings()
        s.set('use-world-coords', False)
        shape = ifcopenshell.geom.create_shape(s, drawing)
        v = np.array(shape.geometry.verts).reshape(-1, 3) / unit_scale
        if not len(v):
            return None
        return (float(v[:, 0].min()), float(v[:, 0].max()),
                float(v[:, 1].min()), float(v[:, 1].max()))
    except Exception:
        return None


def _element_origin(element):
    """Return (x, y, z) world origin of element's ObjectPlacement, or None."""
    try:
        placement = getattr(element, "ObjectPlacement", None)
        if placement is None:
            return None
        m = _placement_to_matrix(placement)
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
    # The engine yields metres; the frustum is in the project unit.
    unit_scale = project_unit_scale(ifc)

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
                    v = np.array(shape.geometry.verts).reshape(-1, 3) / unit_scale
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
        m = _placement_to_matrix(placement)
        return list(m.flatten(order="F"))
    except Exception:
        return list(np.eye(4).flatten(order="F"))
