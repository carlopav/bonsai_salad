"""
add_clipping_plane — Bonsai / Blender.

Cuts the selected IFC objects with a new clipping plane through the 3D cursor,
lying on the face of the active object the cursor sits on. The plane's normal
never points down, and a half space normal points at the material being
removed: what stands above the face goes.

Same reading as the button next to it: active object = reference (it gives the
face, and is not cut), selected = the elements that take the cut. The cursor's
own rotation is ignored — the face is found on the mesh, so it does not matter
how the cursor got there.

Usage: put the 3D cursor on the face (Shift+RMB projects it on the surface),
select the elements to cut, keep the face's object active, run.
"""

import bpy
import numpy as np
import ifcopenshell.util.unit
import bonsai.tool as tool

from . import clipping
from .clipping_scene import clip_target, sync_placement

TOLERANCE = 0.01  # m: past this the cursor is not on the face any more


def face_normal_at(obj, point):
    """(world normal, distance) of the face of obj closest to a world point, or
    None when obj carries no mesh face to read."""
    if obj.type != "MESH" or obj.data is None or not len(obj.data.polygons):
        return None
    hit, location, normal, _ = obj.closest_point_on_mesh(obj.matrix_world.inverted() @ point)
    if not hit:
        return None
    world_normal = (obj.matrix_world.to_3x3().inverted().transposed() @ normal).normalized()
    return tuple(world_normal), (obj.matrix_world @ location - point).length


def main(context):
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        return "No active IFC project in Bonsai."

    source_obj = context.active_object
    if source_obj is None:
        return "Make the object whose face the plane should follow the active one."
    cursor = context.scene.cursor.location
    face = face_normal_at(source_obj, cursor)
    if face is None:
        return f"{source_obj.name} has no mesh face to align the plane with."
    normal, distance = face
    if distance > TOLERANCE:
        return (
            f"The 3D cursor is {distance * 1000:.0f} mm off the nearest face of {source_obj.name}: "
            "put it on the face the plane should follow."
        )

    targets = [obj for obj in context.selected_objects if obj != source_obj]
    if not targets:
        return "Select the elements to cut: the active object only provides the face."

    sync_placement(source_obj)
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
    half_space = clipping.half_space_from_plane(ifc_file, tuple(cursor), clipping.upward(normal), unit_scale)
    try:
        clipped = skipped = 0
        for obj in targets:
            if clip_target(ifc_file, obj, [half_space], np.eye(4), unit_scale) is None:
                print(f"{obj.name}: skipped, no solid IFC geometry of its own to clip.")
                skipped += 1
            else:
                clipped += 1
    finally:
        clipping.discard_half_space(ifc_file, half_space)

    return f"Cut {clipped} element(s) with the plane at the cursor, skipped {skipped}."


class AddClippingPlaneAtCursor(bpy.types.Operator, tool.Ifc.Operator):
    """Cuts the selected IFC objects with a clipping plane through the 3D
    cursor, lying on the face of the active object the cursor sits on: what
    stands above that face is removed. The active object gives the face and is
    not cut itself. Objects with no IFC entity, no Body representation,
    geometry shared with their type, or nothing a boolean can be applied to are
    skipped and reported, not errored"""

    bl_idname = "bim.add_clipping_plane_at_cursor"
    bl_label = "New Clipping Plane at Cursor"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if len(context.selected_objects) < 2:
            cls.poll_message_set("Select the elements to cut and make the face's object active.")
            return False
        return True

    def _execute(self, context):
        self.report({"INFO"}, main(context))
