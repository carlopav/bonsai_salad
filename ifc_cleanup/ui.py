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


class IfcCleanupPanel(bpy.types.Panel):
    bl_label = "IFC Cleanup"
    bl_idname = "BONSAI_SALAD_PT_ifc_cleanup"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        props = context.scene.ifc_cleanup
        box = layout.box()
        box.label(text="MEP Cleaner")
        row = box.row(align=True)
        row.operator("bim.pipe_cleaner", icon="META_CAPSULE")
        row.prop(props, "delete_original_geometry", text="")

        box = layout.box()
        box.label(text="Architecture")
        row = box.row(align=True)
        row.operator("bim.align_base_to_cursor", icon="TRIA_DOWN_BAR")
        row.prop(props, "keep_inserts_fixed", text="", icon="LINKED" if props.keep_inserts_fixed else "UNLINKED")


classes = [IfcCleanupProperties, IfcCleanupPanel]
