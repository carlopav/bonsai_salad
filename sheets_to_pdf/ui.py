# Bonsai Salad — sheets_to_pdf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import bpy
from bonsai import tool


class SheetsToPdfPanel(bpy.types.Panel):
    bl_label = "Sheets to PDF"
    bl_idname = "BONSAI_SALAD_PT_sheets_to_pdf"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_drawings"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return
        layout.operator("bim.export_sheets_to_pdf", icon="FILE_BLANK")


classes = [SheetsToPdfPanel]
