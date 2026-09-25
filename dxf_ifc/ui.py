# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import bpy


class DxfIfcPanel(bpy.types.Panel):
    # Generic on purpose: DXF is the first format supported here, not the only one planned.
    bl_label = "Import External Geometry as Representation"
    bl_idname = "BONSAI_SALAD_PT_dxf_ifc"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_geometry"
    bl_order = 0
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.operator("bim.import_dxf_as_representation", icon="IMPORT")

        # The mapping CSV is picked here, not in the import dialog: that dialog is
        # itself a file browser, and Blender allows only one open at a time.
        box = layout.box()
        box.label(text="Elements by layer", icon="OUTLINER")
        props = getattr(context.scene, "dxf_ifc", None)
        if props is not None:
            box.prop(props, "mapping_filepath", text="")
        box.operator("bim.write_dxf_layer_mapping", icon="FILE_TEXT")


classes = [DxfIfcPanel]
