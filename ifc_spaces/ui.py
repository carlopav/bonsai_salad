# Bonsai Salad — ifc_spaces tool

import os

import bpy
from bonsai import tool

from .core import schedule
from .operator import schedules_dir, selected_spaces


class IfcSpacesProperties(bpy.types.PropertyGroup):
    prefix: bpy.props.StringProperty(
        name="Prefisso",
        description="What every name generated begins with, before the number. May be left empty",
    )
    rename_named: bpy.props.BoolProperty(
        name="Rinumera anche i locali già nominati",
        description=(
            "Off, a room that already carries a name keeps it and consumes no number, while still guiding the "
            "order of the ones numbered around it"
        ),
        default=True,
    )


class IfcSpacesPanel(bpy.types.Panel):
    bl_label = "IfcSpaces"
    bl_idname = "BONSAI_SALAD_PT_ifc_spaces"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_schedules"
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        props = context.scene.ifc_spaces
        row = layout.row(align=True)
        row.label(text="Rename spaces: ")
        row.prop(props, "prefix", text="")
        # On one line there is no room for the label: the toggle carries its icon
        # and says the rest in the tooltip.
        row.prop(props, "rename_named", text="", icon="FILE_REFRESH")
        # The numbering runs on the selected rooms: only the button waits for a
        # selection, the prefix can be typed in beforehand.
        button = row.row(align=True)
        button.enabled = bool(selected_spaces(context))
        operator = button.operator("bim.salad_number_spaces", text="", icon="SORTALPHA")
        operator.prefix, operator.rename_named = props.prefix, props.rename_named


class IfcSpacesSchedulePanel(bpy.types.Panel):
    bl_label = "Abaco dei locali"
    bl_idname = "BONSAI_SALAD_PT_ifc_spaces_schedule"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_parent_id = "BONSAI_SALAD_PT_ifc_spaces"
    bl_order = 0
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if tool.Ifc.get() is None:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        grouping = schedule.by_storey(tool.Ifc.get())
        rooms = sum(len(spaces) for _, spaces in grouping.storeys)
        plural = "" if len(grouping.storeys) == 1 else "i"
        layout.label(text=f"{rooms} locali su {len(grouping.storeys)} pian{plural}", icon="MESH_PLANE")
        # A room outside a storey lands in no file at all: it is worth seeing
        # before the export rather than in the report afterwards.
        if grouping.orphans:
            layout.label(text=f"{len(grouping.orphans)} locali senza piano, esclusi", icon="ERROR")

        layout.operator("bim.salad_export_spaces_schedule", icon="EXPORT")
        # There is no file browser to read the destination off any more, and
        # there is a file per storey rather than one to name.
        if tool.Ifc.get_path():
            project = os.path.dirname(tool.Ifc.get_path())
            layout.label(text=os.path.relpath(schedules_dir(), project), icon="FILE_FOLDER")


classes = (IfcSpacesProperties, IfcSpacesPanel, IfcSpacesSchedulePanel)
