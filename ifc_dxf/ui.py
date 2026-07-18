# Bonsai Salad — ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import os

import bpy
from bonsai import tool

from .operator import _get_active_drawing


class IfcDxfPanel(bpy.types.Panel):
    bl_label = "Export Drawing to DXF"
    bl_idname = "BONSAI_SALAD_PT_ifc_dxf"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        drawing = _get_active_drawing()
        if drawing is None:
            layout.label(text="No active drawing.", icon="INFO")
        else:
            layout.label(text=getattr(drawing, "Name", "Drawing"), icon="FILE_IMAGE")

        layout.operator("bim.export_drawing_to_dxf", icon="EXPORT")


class IfcDxfOptionsPanel(bpy.types.Panel):
    """Native collapsible sub-panel (DEFAULT_CLOSED): Blender skips its draw()
    entirely while collapsed, unlike a hand-rolled box+BoolProperty toggle
    which re-runs the whole parent draw() on every redraw regardless."""

    bl_label = "Options"
    bl_idname = "BONSAI_SALAD_PT_ifc_dxf_options"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_ifc_dxf"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def draw(self, context):
        layout = self.layout
        props = context.scene.ifc_dxf

        layout.row(align=True).prop(props, "export_method", expand=True)

        from . import get_template_path as _get_tpl
        tpl = props.template_path or (_get_tpl() or "")
        tpl_name = os.path.basename(tpl) if tpl else "—"
        row = layout.row(align=True)
        row.label(text=tpl_name, icon="FILE")
        row.operator("bim.select_dxf_template", text="", icon="FILEBROWSER")

        layout.prop(props, "mesh_crease_angle")
        layout.prop(props, "export_material_layers")


classes = [IfcDxfPanel, IfcDxfOptionsPanel]
