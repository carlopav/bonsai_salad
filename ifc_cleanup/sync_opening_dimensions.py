"""
sync_opening_dimensions — Bonsai / Blender.

Rewrites the OverallWidth/OverallHeight of the selected IfcDoor and IfcWindow
occurrences from the size their type was parametrised with. Bonsai sets the two
attributes when an occurrence is created, but nothing re-derives them when the
type changes afterwards, so schedules keep quoting the size the door had when
it was placed.

The width and height corrections are signed and in metres, and apply to the
whole selection: set them to -0.10 and -0.05 to record a door's net passage
rather than its lining, or leave them at zero for a plain resync. Only the two
attributes are written — the geometry is left as modelled, which is the point
of a correction.

Usage: select the doors/windows, set the corrections, run. Runs inside a
tool.Ifc.Operator: CTRL+Z undoes the whole run.
"""

import collections

import bpy
import ifcopenshell.util.unit
import bonsai.tool as tool

from . import opening_dimensions

# Why an element was left alone, as the panel reports it.
SKIP_REASONS = {
    "no_type_data": "no parametric data on its type",
    "invalid": "the correction leaves nothing of the opening",
    "not_an_opening": "not a door or a window",
}


def main(context):
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        return "No active IFC project in Bonsai."

    props = context.scene.ifc_cleanup
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
    outcomes = collections.Counter()

    for obj in context.selected_objects:
        element = tool.Ifc.get_entity(obj)
        if not opening_dimensions.is_opening(element):
            outcome = "not_an_opening"
        else:
            outcome = opening_dimensions.sync_dimensions(
                element, props.opening_width_delta, props.opening_height_delta, unit_scale
            )
        outcomes[outcome] += 1
        if outcome in SKIP_REASONS:
            print(f"{obj.name}: skipped, {SKIP_REASONS[outcome]}.")

    message = f"Synced {outcomes['synced']}, already in sync: {outcomes['unchanged']}"
    skipped = ", ".join(
        f"{reason}: {outcomes[outcome]}" for outcome, reason in SKIP_REASONS.items() if outcomes[outcome]
    )
    return f"{message}, skipped ({skipped})." if skipped else f"{message}."


class SyncOpeningDimensions(bpy.types.Operator, tool.Ifc.Operator):
    """Rewrites the selected doors' and windows' OverallWidth/OverallHeight
    from the size their type was parametrised with, plus the corrections below.
    Occurrences with no type, or whose type carries no parametric data, are
    skipped and reported, not errored. The geometry is not regenerated"""

    bl_idname = "bim.sync_opening_dimensions"
    bl_label = "Sync Door/Window Sizes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if not context.selected_objects:
            cls.poll_message_set("Select the doors and windows to resync.")
            return False
        return True

    def _execute(self, context):
        self.report({"INFO"}, main(context))
