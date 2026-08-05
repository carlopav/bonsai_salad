# Bonsai Salad — urban_parameters tool

import os

import bpy
from bonsai import tool

from .core import quantities
from .data import ZoneTypes
from .operator import schedule_path, selected_zones, target_zones


def get_object_types(self, context):
    return ZoneTypes.load()


class UrbanParametersProperties(bpy.types.PropertyGroup):
    object_type: bpy.props.EnumProperty(
        name="Tipo",
        description=(
            "The ObjectType of the spatial zones the table is built from. One table covers one type: totalling "
            "zones of different kinds together says nothing"
        ),
        items=get_object_types,
    )
    coefficient: bpy.props.FloatProperty(
        name="Coefficiente",
        description=(
            "How the selected zones enter the count: 1 counts a zone in full, -1 detracts it from the others, "
            "a fraction counts it in part. Written to the zone's quantity set"
        ),
        default=quantities.DEFAULT_COEFFICIENT,
        soft_min=-1.0,
        soft_max=1.0,
    )


class UrbanParametersPanel(bpy.types.Panel):
    bl_label = "IfcSpatialZones"
    bl_idname = "BONSAI_SALAD_PT_urban_parameters"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_schedules"
    bl_order = 0
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        if not ZoneTypes.load():
            layout.label(text=f"No {quantities.ZONE_CLASS} in the file.", icon="ERROR")
            return

        props = context.scene.urban_parameters
        layout.prop(props, "object_type")

        zones = target_zones(context)
        detracted = sum(1 for zone in zones if quantities.coefficient(zone) < 0)
        plural = "" if len(zones) == 1 else "s"
        layout.label(
            text=f"{len(zones)} spatial zone{plural}" + (f", {detracted} detracted" if detracted else ""),
            icon="MOD_BUILD",
        )

        row = layout.row(align=True)
        row.prop(props, "coefficient")
        # The coefficient is written to the selected zones, not to the whole
        # type: only the button waits for a selection, the value can be dialled
        # in beforehand.
        apply = row.row(align=True)
        apply.enabled = bool(selected_zones(context))
        apply.operator("bim.salad_set_urban_coefficient", text="", icon="CHECKMARK").coefficient = props.coefficient

        layout.operator("bim.salad_quantify_urban_parameters", icon="DRIVER")
        layout.operator("bim.salad_export_urban_parameters", icon="EXPORT")
        # There is no file browser to read the destination off any more.
        if tool.Ifc.get_path():
            project = os.path.dirname(tool.Ifc.get_path())
            layout.label(text=os.path.relpath(schedule_path(props.object_type), project), icon="FILE_BLANK")


classes = (UrbanParametersProperties, UrbanParametersPanel)
