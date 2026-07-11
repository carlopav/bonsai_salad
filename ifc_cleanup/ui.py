# Bonsai Salad — ifc_cleanup tool

import bpy


def _get_ifc():
    try:
        from bonsai import tool
        return tool.Ifc.get()
    except Exception:
        return None


class IfcCleanupProperties(bpy.types.PropertyGroup):
    delete_original_geometry: bpy.props.BoolProperty(
        name="Delete Original Geometry",
        description="Remove the geometry from the source object once it has "
                    "been split off into the new elements",
        default=True,
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

        if _get_ifc() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        props = context.scene.ifc_cleanup
        box = layout.box()
        box.label(text="MEP Cleaner")
        row = box.row(align=True)
        row.operator("bim.pipe_cleaner", icon="META_CAPSULE")
        row.prop(props, "delete_original_geometry", text="")


classes = [IfcCleanupProperties, IfcCleanupPanel]
