# Bonsai Salad — ifc_cleanup tool

import bpy
from bonsai import tool


class IfcCleanupProperties(bpy.types.PropertyGroup):
    delete_original_geometry: bpy.props.BoolProperty(
        name="Delete Original Geometry",
        description="Remove the geometry from the source object once it has " "been split off into the new elements",
        default=True,
    )
    keep_inserts_fixed: bpy.props.BoolProperty(
        name="Keep Doors/Windows Fixed",
        description="Counter-move hosted openings and door/window fillings so "
        "they stay put in world space, and regenerate the host's "
        "geometry to match",
        default=False,
    )


class IfcCleanupGeometryPanel(bpy.types.Panel):
    bl_label = "IFC Cleanup"
    bl_idname = "BONSAI_SALAD_PT_ifc_cleanup"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_geometry"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        props = context.scene.ifc_cleanup
        row = layout.row(align=True)
        row.operator("bim.align_base_to_cursor", icon="TRIA_DOWN_BAR")
        row.prop(props, "keep_inserts_fixed", text="", icon="LINKED" if props.keep_inserts_fixed else "UNLINKED")
        layout.operator("bim.split_layers", icon="MOD_BUILD")


class MepCleanerPanel(bpy.types.Panel):
    bl_label = "MEP Cleaner"
    bl_idname = "BONSAI_SALAD_PT_mep_cleaner"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_energy_mep"
    bl_order = 1
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        props = context.scene.ifc_cleanup
        row = layout.row(align=True)
        row.operator("bim.pipe_cleaner", icon="META_CAPSULE")
        row.prop(props, "delete_original_geometry", text="")


classes = [IfcCleanupProperties, IfcCleanupGeometryPanel, MepCleanerPanel]
