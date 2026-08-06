# Bonsai Salad — daylight_ventilation tool

import bpy
from bonsai import tool

from .core import ratios
from .data import Summary


def selected_spaces(context):
    """Only the selected rooms: what a button that edits one room at a time works
    on, never the whole file by accident."""
    elements = [tool.Ifc.get_entity(obj) for obj in context.selected_objects]
    return [element for element in elements if element and element.is_a("IfcSpace")]


class SetDaylightRequirement(bpy.types.Operator, tool.Ifc.Operator):
    """Sets the two ratios the selected IfcSpaces have to reach, as the
    "Requisiti aeroilluminanti" property set: 0.125 is the usual eighth, 0 marks
    a room the regulation does not ask anything of"""

    bl_idname = "bim.salad_set_daylight_requirement"
    bl_label = "Set Requirement"
    bl_options = {"REGISTER", "UNDO"}

    daylight: bpy.props.FloatProperty(name="Illuminazione", default=ratios.DEFAULT_REQUIREMENT)
    air: bpy.props.FloatProperty(name="Aerazione", default=ratios.DEFAULT_REQUIREMENT)

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        spaces = selected_spaces(context)
        if not spaces:
            self.report({"ERROR"}, "Select the IfcSpaces to set first.")
            return {"CANCELLED"}
        for space in spaces:
            ratios.write_requirements(tool.Ifc.get(), space, self.daylight, self.air)
        Summary.refresh()
        self.report({"INFO"}, f"{len(spaces)} rooms set.")


class QuantifyDaylight(bpy.types.Operator, tool.Ifc.Operator):
    """Measures the clear opening of every window and door, records the room each
    one serves as an IfcRelSpaceBoundary where it has none yet, and writes each
    room's areas, ratios and verdict into its "Requisiti aeroilluminanti"
    property set.

    Nothing already in the file is overwritten: not a boundary, not an area you
    corrected by hand, not a requirement you edited"""

    bl_idname = "bim.salad_quantify_daylight"
    bl_label = "Calcola"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        summary = ratios.quantify(tool.Ifc.get())
        Summary.store(summary)
        self.report(
            {"INFO"},
            f"{len(summary.rows)} rooms checked, {summary.boundaries_written} boundaries written."
            + (f" {len(summary.orphans)} orphan fillings." if summary.orphans else "")
            + (f" {len(summary.unmeasured)} unmeasured." if summary.unmeasured else "")
            + (f" {len(summary.disagreeing)} disagreeing." if summary.disagreeing else ""),
        )


classes = (SetDaylightRequirement, QuantifyDaylight)
