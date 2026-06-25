# Bonsai - OpenBIM 5D Blender Add-on based on Bonsai
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
#
# This file is part of Bonsai5D+.  GNU GPL v3 or later.

import bpy
from .operator import _get_ifc


class SheetsToPdfPanel(bpy.types.Panel):
    bl_label = "Sheets to PDF"
    bl_idname = "SCENE_PT_sheets_to_pdf"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai5D+"
    bl_parent_id = "SCENE_PT_bonsai5d_sandbox"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        if _get_ifc() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return
        layout.operator("bim.export_sheets_to_pdf", icon="FILE_BLANK")


classes = [SheetsToPdfPanel]
