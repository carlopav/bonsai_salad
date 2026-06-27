# Bonsai Salad — ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import bpy
from .operator import _get_ifc, _get_ifc_dxf, _get_active_drawing


class IfcDxfPanel(bpy.types.Panel):
    bl_label = "Export Drawing to DXF"
    bl_idname = "BONSAI_SALAD_PT_ifc_dxf"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if _get_ifc() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        if _get_ifc_dxf() is None:
            col = layout.column()
            col.label(text="ifc_dxf module not built.", icon="ERROR")
            col.label(text="Run: cd ifc_dxf_rs && maturin develop --release")
            return

        drawing = _get_active_drawing(context)
        if drawing is None:
            layout.label(text="No active drawing.", icon="INFO")
        else:
            layout.label(text=getattr(drawing, "Name", "Drawing"), icon="FILE_IMAGE")

        layout.operator("bim.export_drawing_to_dxf", icon="EXPORT")


classes = [IfcDxfPanel]
