import bpy


class BONSAI_SALAD_PT_geometry(bpy.types.Panel):
    bl_label = "Geometry"
    bl_idname = "BONSAI_SALAD_PT_geometry"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_order = 0

    def draw(self, context):
        pass


class BONSAI_SALAD_PT_drawings(bpy.types.Panel):
    bl_label = "Drawings and Documents"
    bl_idname = "BONSAI_SALAD_PT_drawings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_order = 1

    def draw(self, context):
        pass


class BONSAI_SALAD_PT_schedules(bpy.types.Panel):
    bl_label = "Schedules"
    bl_idname = "BONSAI_SALAD_PT_schedules"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_order = 2

    def draw(self, context):
        pass


class BONSAI_SALAD_PT_energy_mep(bpy.types.Panel):
    bl_label = "Energy and MEP"
    bl_idname = "BONSAI_SALAD_PT_energy_mep"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        pass


classes = (
    BONSAI_SALAD_PT_geometry,
    BONSAI_SALAD_PT_drawings,
    BONSAI_SALAD_PT_schedules,
    BONSAI_SALAD_PT_energy_mep,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
