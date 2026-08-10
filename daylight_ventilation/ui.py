# Bonsai Salad — daylight_ventilation tool

import os

import bpy
from bonsai import tool

from .core import boundaries, ratios
from .data import Summary
from .operator import schedule_path, selected_fillings, selected_spaces


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
        # The override is written to the selected windows and doors: correcting
        # one is looking at one, never at the whole file.
        prepare = layout.row(align=True)
        prepare.enabled = bool(selected_fillings(context))
        prepare.operator("bim.salad_prepare_daylight_overrides", icon="GREASEPENCIL")

        self.draw_summary(layout)
        self.draw_active_space(context, layout)

        layout.operator("bim.salad_export_daylight_schedule", icon="EXPORT")
        # There is no file browser to read the destination off any more.
        if tool.Ifc.get_path():
            project = os.path.dirname(tool.Ifc.get_path())
            layout.label(text=os.path.relpath(schedule_path(), project), icon="FILE_BLANK")

    def draw_summary(self, layout):
        summary = Summary.load()
        if not summary.get("spaces"):
            if summary.get("dropped"):
                layout.label(text="Riepilogo scaduto: ricalcola.", icon="FILE_REFRESH")
            else:
                layout.label(text="Non ancora calcolato.", icon="INFO")
            return
        unverified, withheld, orphans, unmeasurable, disagreeing = (
            summary["unverified"],
            summary["withheld"],
            summary["orphans"],
            summary["unmeasurable"],
            summary["disagreeing"],
        )
        box = layout.box()
        row = box.row(align=True)
        row.label(
            text=f"{summary['spaces']} locali, {len(unverified)} non verificati",
            icon="CHECKMARK" if not unverified else "ERROR",
        )
        if unverified:
            row.operator("bim.salad_select_unverified_spaces", text="", icon="RESTRICT_SELECT_OFF")
        # A withheld verdict is the opposite of a failed one: nothing was
        # checked, so it cannot be counted among the unverified.
        if withheld:
            box.label(text=f"{len(withheld)} locali senza verdetto", icon="QUESTION")
        if orphans:
            box.label(text=f"{len(orphans)} serramenti orfani", icon="GHOST_DISABLED")
        if unmeasurable:
            box.label(text=f"{len(unmeasurable)} serramenti non misurati", icon="QUESTION")
        if disagreeing:
            row = box.row(align=True)
            row.label(text=f"{len(disagreeing)} associazioni in disaccordo", icon="LIBRARY_DATA_BROKEN")
            row.operator("bim.salad_select_disagreeing_openings", text="", icon="RESTRICT_SELECT_OFF")
            row.operator("bim.salad_refresh_space_boundaries", text="", icon="FILE_REFRESH")

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
        # An unmeasured filling withholds the verdict on purpose
        # (write_space_results): row.verified alone cannot tell "failed" from
        # "not checked".
        if row.unmeasured_fillings:
            box.label(text="Da verificare", icon="QUESTION")
        else:
            box.label(
                text="Verificato" if row.verified else "Non verificato", icon="CHECKMARK" if row.verified else "ERROR"
            )
        for filling in boundaries.serves(element):
            daylight, air = ratios.contribution(filling)
            name = filling.Name or filling.is_a()
            # The measured clear opening leads the line only where what counts
            # differs: otherwise it would repeat the two numbers after it.
            if ratios.differs_from_measured(filling):
                measured = ratios.clear_opening(filling)
                box.label(text=f"{name}: {measured:.2f} — {daylight:.2f} / {air:.2f} (≠ misurato)")
            else:
                box.label(text=f"{name}: {daylight:.2f} / {air:.2f}")


classes = (DaylightVentilationProperties, DaylightVentilationPanel)
