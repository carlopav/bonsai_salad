"""
copy_clipping_planes — Bonsai / Blender.

Copies the half-space clipping planes cutting the active object onto every
other selected IFC object, keeping each plane exactly where it is in world
space: one roof pitch, one section plane, cut through a whole selection.

The cut is applied to the Body representation items, not to any class-specific
parameter, so it works on any solid IFC geometry — walls, slabs, columns,
IfcSpace, imported breps — whatever the semantics.

Usage: select the targets, make the already clipped element active, run. The
clips already on a target are kept, the new ones are added. Runs inside a
tool.Ifc.Operator: CTRL+Z undoes the whole run, and single clips can be
removed afterwards from Bonsai's Booleans panel.
"""

import bpy
import ifcopenshell.util.unit
import bonsai.tool as tool

from . import clipping
from .clipping_scene import body_of, clip_target, sync_placement, world_matrix


def main(context):
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        return "No active IFC project in Bonsai."

    source_obj = context.active_object
    source_element = tool.Ifc.get_entity(source_obj) if source_obj else None
    if source_element is None:
        return "Make the clipped IFC element the active object: it is the one the planes are read from."
    source_representation = body_of(source_element)
    if source_representation is None:
        return "The active object has no Body representation."
    half_spaces = clipping.half_space_clips(tool.Model.get_booleans(representation=source_representation))
    if not half_spaces:
        return "The active object carries no clipping planes."

    sync_placement(source_obj)
    source_matrix = world_matrix(source_obj)
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)

    clipped = skipped = 0
    for obj in context.selected_objects:
        if obj == source_obj:
            continue
        if clip_target(ifc_file, obj, half_spaces, source_matrix, unit_scale) is None:
            print(f"{obj.name}: skipped, no solid IFC geometry of its own to clip.")
            skipped += 1
        else:
            clipped += 1

    return f"Copied {len(half_spaces)} clipping plane(s) onto {clipped} element(s), skipped {skipped}."


class CopyClippingPlanes(bpy.types.Operator, tool.Ifc.Operator):
    """Copies the clipping planes of the active object onto the other selected
    IFC objects, each plane keeping its position in world space. Works on any
    solid geometry regardless of semantics. Objects with no IFC entity, no Body
    representation, geometry shared with their type, or nothing a boolean can
    be applied to are skipped and reported, not errored"""

    bl_idname = "bim.copy_clipping_planes"
    bl_label = "Copy Clipping Planes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if len(context.selected_objects) < 2:
            cls.poll_message_set("Select the targets and make the clipped element active.")
            return False
        return True

    def _execute(self, context):
        self.report({"INFO"}, main(context))
