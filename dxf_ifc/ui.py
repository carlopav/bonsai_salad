# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import bpy
from .operator import _get_ifc, _get_selected_element, _get_element_subcontext


class DxfIfcPanel(bpy.types.Panel):
    bl_label = "Import DXF as Representation"
    bl_idname = "BONSAI_SALAD_PT_dxf_ifc"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        props = context.scene.dxf_ifc
        layout.prop(props, "write_pset")
        layout.operator("bim.import_dxf_as_representation", icon="IMPORT")


classes = [DxfIfcPanel]
