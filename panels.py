import bpy
from .sheets_to_pdf.operator import _get_ifc


class BONSAI_SALAD_PT_main(bpy.types.Panel):
    bl_label = "Bonsai Salad"
    bl_idname = "BONSAI_SALAD_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"

    def draw(self, _context):
        layout = self.layout
        layout.label(text="Tools coming soon... or not?")

        col = layout.column(align=True)
        col.label(text="Sheets to PDF")
        if _get_ifc() is None:
            col.label(text="No IFC file loaded.", icon="ERROR")
        else:
            col.operator("bim.export_sheets_to_pdf", icon="FILE_BLANK")


classes = (BONSAI_SALAD_PT_main,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
