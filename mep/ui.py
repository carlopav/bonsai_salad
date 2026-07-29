# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import bpy
from bonsai import tool

from .operator import selection_systems_summary


class MepPanel(bpy.types.Panel):
    bl_label = "MEP (WIP)"
    bl_idname = "BONSAI_SALAD_PT_mep"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_energy_mep"
    bl_order = 0
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        props = context.scene.mep
        layout.row(align=True).prop(props, "tipologia", expand=True)

        if props.tipologia != "GRAVITA":
            layout.label(text="Tipologia non ancora disponibile.", icon="INFO")
            return

        layout.prop(props, "catalog_system")
        layout.prop(props, "sistema")
        layout.prop(props, "uso")
        layout.operator("bim.mep_check_data", icon="FILE_REFRESH")

        # Existing system memberships of the selection (spec §8, step 1).
        if context.selected_objects:
            elements, summary, without = selection_systems_summary(context)
            if elements:
                box = layout.box()
                box.label(text=f"Selezione: {elements} elementi IFC",
                          icon="RESTRICT_SELECT_OFF")
                for name in sorted(summary):
                    box.label(text=f"{name}: {summary[name]}",
                              icon="OUTLINER_COLLECTION")
                if without:
                    box.label(text=f"Senza sistema: {without}", icon="INFO")
                distinct = [n for n in summary if "(circuito)" not in n]
                if len(distinct) > 1:
                    box.label(text="Attenzione: più sistemi nella selezione",
                              icon="ERROR")

        # Flow (spec §8); set ready=False to gray out unfinished steps.
        col = layout.column(align=True)
        col.label(text="Linea di scarico:")
        col.prop(props, "linea")
        row = col.row(align=True)
        row.prop(props, "apparecchio", text="")
        row.operator("bim.mep_mark_terminal", text="Segna", icon="PINNED")
        row = col.row(align=True)
        row.prop(props, "tipo_recapito", text="")
        row.operator("bim.mep_mark_outfall", text="Segna", icon="TRIA_DOWN_BAR")
        col.operator("bim.mep_sync_ports", icon="UV_SYNC_SELECT")
        for idname, icon, ready in (
            ("bim.mep_generate_tracciato", "EDGESEL", True),
            ("bim.mep_apply_slopes", "IPO_LINEAR", True),
            ("bim.mep_validate_tracciato", "CHECKMARK", True),
            ("bim.mep_generate_pipes", "META_CAPSULE", True),
        ):
            row = col.row(align=True)
            row.enabled = ready
            row.operator(idname, icon=icon)


classes = [MepPanel]
