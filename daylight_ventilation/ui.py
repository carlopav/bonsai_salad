# Bonsai Salad — daylight_ventilation tool

import bpy
from bonsai import tool

from .core import boundaries, ratios
from .data import Summary
from .operator import selected_spaces


class DaylightVentilationProperties(bpy.types.PropertyGroup):
    daylight: bpy.props.FloatProperty(
        name="Illuminazione",
        description=(
            "The ratio of lighting area to net floor area the selected rooms have to reach. "
            "0 marks a room the regulation asks nothing of"
        ),
        default=ratios.DEFAULT_REQUIREMENT,
        soft_min=0.0,
        soft_max=1.0,
    )
    air: bpy.props.FloatProperty(
        name="Aerazione",
        description=(
            "The ratio of ventilation area to net floor area the selected rooms have to reach. "
            "0 marks a room the regulation asks nothing of"
        ),
        default=ratios.DEFAULT_REQUIREMENT,
        soft_min=0.0,
        soft_max=1.0,
    )


class DaylightVentilationPanel(bpy.types.Panel):
    bl_label = "Rapporti aeroilluminanti"
    bl_idname = "BONSAI_SALAD_PT_daylight"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_schedules"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return
        if not tool.Ifc.get().by_type("IfcSpace"):
            layout.label(text="No IfcSpace in the file.", icon="ERROR")
            return

        props = context.scene.daylight_ventilation
        column = layout.column(align=True)
        column.prop(props, "daylight")
        column.prop(props, "air")
        # The requirement is written to the selected rooms, not to the whole
        # file: only the button waits for a selection, the values can be dialled
        # in beforehand.
        apply = column.row(align=True)
        apply.enabled = bool(selected_spaces(context))
        operator = apply.operator("bim.salad_set_daylight_requirement", icon="CHECKMARK")
        operator.daylight, operator.air = props.daylight, props.air

        layout.operator("bim.salad_quantify_daylight", icon="DRIVER")
        self.draw_summary(layout)
        self.draw_active_space(context, layout)

    def draw_summary(self, layout):
        summary = Summary.load()
        if not summary.get("spaces"):
            layout.label(text="Non ancora calcolato.", icon="INFO")
            return
        unverified = summary["unverified"]
        box = layout.box()
        box.label(
            text=f"{summary['spaces']} locali, {len(unverified)} non verificati",
            icon="CHECKMARK" if not unverified else "ERROR",
        )
        if summary["orphans"]:
            box.label(text=f"{len(summary['orphans'])} serramenti orfani", icon="GHOST_DISABLED")
        if summary["unmeasured"]:
            box.label(text=f"{len(summary['unmeasured'])} serramenti non misurati", icon="QUESTION")

    def draw_active_space(self, context, layout):
        element = tool.Ifc.get_entity(context.active_object) if context.active_object else None
        if element is None or not element.is_a("IfcSpace"):
            return
        (row,) = ratios.measure_spaces(tool.Ifc.get(), [element])
        box = layout.box()
        box.label(text=f"{row.identification} {row.name}".strip(), icon="MESH_PLANE")
        box.label(text=f"Superficie netta: {row.net:.2f}")
        box.label(text=f"Aerante {row.air:.2f} — rapporto {row.air_ratio:.3f} / {row.air_requirement:.3f}")
        box.label(
            text=f"Illuminante {row.daylight:.2f} — rapporto {row.daylight_ratio:.3f} "
            f"/ {row.daylight_requirement:.3f}"
        )
        # unmeasured > 0 withholds the verdict on purpose (write_space_results):
        # row.verified alone cannot tell "failed" from "not checked".
        if row.unmeasured:
            box.label(text="Da verificare", icon="QUESTION")
        else:
            box.label(
                text="Verificato" if row.verified else "Non verificato", icon="CHECKMARK" if row.verified else "ERROR"
            )
        for filling in boundaries.serves(element):
            daylight, air = ratios.contribution(filling)
            suffix = " (corretto)" if ratios.is_overridden(filling) else ""
            box.label(text=f"{filling.Name or filling.is_a()}: {daylight:.2f} / {air:.2f}{suffix}")


classes = (DaylightVentilationProperties, DaylightVentilationPanel)
