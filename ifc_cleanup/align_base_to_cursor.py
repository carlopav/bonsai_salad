"""
align_base_to_cursor — Bonsai / Blender.

Translates each selected IFC element rigidly along Z so that the lowest
point of its mesh (world space) lands on the current 3D cursor's Z. X, Y,
rotation and all element dimensions are untouched.

Optionally (Keep Doors/Windows Fixed), any openings and door/window
fillings hosted by a moved element are counter-translated so they stay
put in world space, and the host's Body representation is regenerated so
its cut re-aligns with them.

Usage: select one or more IFC objects, position the 3D cursor at the
target Z, run. Runs inside a tool.Ifc.Operator: CTRL+Z undoes all moved
elements at once.
"""

import bpy
import numpy as np
import bonsai.core.geometry
import bonsai.tool as tool

TOLERANCE = 1e-5  # m


def mesh_min_z_world(obj):
    """Lowest Z of the object's actual mesh vertices in world space, or
    None if it has no mesh geometry."""
    if obj.type != "MESH" or obj.data is None or len(obj.data.vertices) == 0:
        return None
    n = len(obj.data.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(obj.matrix_world, dtype=np.float64)
    world = co @ mw[:3, :3].T + mw[:3, 3]
    return float(world[:, 2].min())


def move_along_z(obj, delta):
    """Shifts obj along world Z by delta and writes the new placement back
    to IFC. dual-write: edit_object_placement reads the just-updated
    obj.matrix_world and writes the matching IFC placement (same path as a
    manual viewport move)."""
    matrix = obj.matrix_world.copy()
    matrix.translation.z += delta
    obj.matrix_world = matrix
    bonsai.core.geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)


def move_base_to_cursor(obj, cursor_z):
    """Shifts obj along world Z so its mesh base lands on cursor_z and
    writes the new placement back to IFC. Returns the applied delta (0.0
    if already at cursor_z), or None if obj has no mesh geometry."""
    base_z = mesh_min_z_world(obj)
    if base_z is None:
        return None
    delta = cursor_z - base_z
    if abs(delta) <= TOLERANCE:
        return 0.0
    move_along_z(obj, delta)
    return delta


def counter_move_inserts(element, delta):
    """Counter-translates element's openings (IfcRelVoidsElement) and their
    door/window fillings (IfcRelFillsElement) by -delta, so they stay put
    in world space while element (already moved by delta) leaves them
    behind. Returns the number of Blender objects counter-moved."""
    kept = 0
    for rel in getattr(element, "HasOpenings", []) or []:
        opening = rel.RelatedOpeningElement
        entities = [opening]
        entities += [fill.RelatedBuildingElement for fill in getattr(opening, "HasFillings", []) or []]
        for entity in entities:
            obj = tool.Ifc.get_object(entity)
            if obj is not None:
                move_along_z(obj, -delta)
                kept += 1
    return kept


def recut_host(host_obj):
    """Regenerates host_obj's Body representation from its current IFC
    state, re-cutting it against its openings' current (already-written)
    placements. Direct `switch_representation` call — mirrors the pattern
    used by Bonsai's own AddOpening/RemoveOpening operators, since
    `tool.Geometry.recut_host` does not exist in the installed Bonsai
    (0.8.6a260515)."""
    representation = tool.Geometry.get_active_representation(host_obj)
    if representation is None:
        return False
    bonsai.core.geometry.switch_representation(tool.Ifc, tool.Geometry, obj=host_obj, representation=representation)
    return True


class AlignBaseToCursor(bpy.types.Operator, tool.Ifc.Operator):
    """Moves each selected IFC element along Z only, so the lowest point
    of its mesh (world space) lands exactly on the 3D cursor's Z. Blender
    objects with no IFC entity, or an IFC entity with no mesh geometry to
    compute a base from, are skipped and reported, not errored. Elements
    already at the target Z are left untouched. If Keep Doors/Windows
    Fixed is on, hosted openings/fillings are counter-moved and the
    host's Body representation is regenerated to match."""

    bl_idname = "bim.align_base_to_cursor"
    bl_label = "Align Base to Cursor Z"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        cursor_z = context.scene.cursor.location.z
        props = getattr(context.scene, "ifc_cleanup", None)
        keep_fixed = props.keep_inserts_fixed if props else False

        moved = unchanged = skipped_no_ifc = skipped_no_geometry = 0
        inserts_kept = hosts_regenerated = 0

        for obj in context.selected_objects:
            element = tool.Ifc.get_entity(obj)
            if element is None:
                skipped_no_ifc += 1
                continue
            delta = move_base_to_cursor(obj, cursor_z)
            if delta is None:
                skipped_no_geometry += 1
                continue
            if not delta:
                unchanged += 1
                continue
            moved += 1
            if not keep_fixed:
                continue
            kept = counter_move_inserts(element, delta)
            if not kept:
                continue
            inserts_kept += kept
            if recut_host(obj):
                hosts_regenerated += 1

        message = (
            f"Moved {moved}, already at cursor Z: {unchanged}, "
            f"skipped (not IFC): {skipped_no_ifc}, skipped (no geometry): {skipped_no_geometry}."
        )
        if keep_fixed:
            message += f" Inserts kept fixed: {inserts_kept}, hosts regenerated: {hosts_regenerated}."
        self.report({"INFO"}, message)
