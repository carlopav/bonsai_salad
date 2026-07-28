# Bonsai Salad — drawing_diff tool

import bpy

from .core.svg_read import DEFAULT_EXCLUDE
from .core.svg_write import Colors, rgb
from .data import DrawingDiffData, SourceDocuments


def _channels(color):
    return [channel / 255 for channel in rgb(color)]


def get_source_documents(self, context):
    if not SourceDocuments.is_loaded:
        SourceDocuments.load()
    return SourceDocuments.data["documents"]


class DrawingDiffComparison(bpy.types.PropertyGroup):
    ifc_definition_id: bpy.props.IntProperty()
    description: bpy.props.StringProperty()
    has_sources: bpy.props.BoolProperty()


class DrawingDiffProperties(bpy.types.PropertyGroup):
    comparisons: bpy.props.CollectionProperty(type=DrawingDiffComparison)
    active_comparison_index: bpy.props.IntProperty(name="Active Comparison Index")
    # The list is read from the IFC on demand: until then the panel only says
    # how many comparisons there are.
    is_loaded: bpy.props.BoolProperty(name="Is Loaded", default=False)
    # The file the rows were read from. They carry its entity ids, and the same
    # id means something else in another file, so a list outlives its own IFC
    # only as far as this check.
    loaded_path: bpy.props.StringProperty(name="Loaded Path")
    is_editing: bpy.props.BoolProperty(name="Is Editing", default=False)
    # The comparison being edited, 0 while a new one is being created.
    editing_document: bpy.props.IntProperty(name="Editing Document")
    source_mode: bpy.props.EnumProperty(
        name="Sources",
        description="Where the two drawings to compare are chosen from",
        items=[
            ("FILE", "Files", "Two SVG files picked on disk"),
            (
                "DOCUMENT",
                "IFC Documents",
                "Two documents of the open IFC: the drawings Bonsai writes and the SVGs imported as references. "
                "Whichever way they are chosen, the comparison stores the two source paths, so it regenerates the "
                "same in both modes",
            ),
        ],
        default="FILE",
    )
    existing_svg: bpy.props.StringProperty(
        name="Existing State",
        description="SVG of the existing state drawing, generated from the other IFC project",
    )
    project_svg: bpy.props.StringProperty(
        name="Project",
        description="SVG of the project drawing. Left empty, the drawing selected in Bonsai's Drawings list is used",
    )
    existing_document: bpy.props.EnumProperty(
        name="Existing State",
        description="Document of the open IFC standing for the existing state drawing",
        items=get_source_documents,
    )
    project_document: bpy.props.EnumProperty(
        name="Project",
        description="Document of the open IFC standing for the project drawing",
        items=get_source_documents,
    )
    include_linework: bpy.props.BoolProperty(
        name="Projection Linework",
        description=(
            "Also compare the projection linework of the two drawings, on top of the cut fills. "
            "Annotations are never compared: they belong to the drawing, not to the model"
        ),
        default=False,
    )
    exclude_filter: bpy.props.StringProperty(
        name="Exclude",
        description=(
            "Comma separated tokens: a cut or projection group whose class carries one of them stays out of the "
            "comparison, and is counted in the report. An IFC class leaves a group alone when the group also "
            "carries another class, so a wall merged with a piece of furniture is not lost. Besides the classes "
            "the drawings carry material-<name>, layer-material-<name> and layer-material-category-<name>: those "
            "take a group out as asked, so use them knowing that the two states may draw the same wall "
            "differently, one monolithic and one split into layers. Emptied, everything the drawings show is "
            "compared"
        ),
        default=", ".join(DEFAULT_EXCLUDE),
    )
    export_dxf: bpy.props.BoolProperty(
        name="DXF",
        description="Also write the comparison as a DXF in model coordinates, next to the SVG",
        default=False,
    )
    color_unchanged: bpy.props.FloatVectorProperty(
        name="Unchanged", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0, default=_channels(Colors.unchanged)
    )
    color_demolished: bpy.props.FloatVectorProperty(
        name="Demolished", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0, default=_channels(Colors.demolished)
    )
    color_added: bpy.props.FloatVectorProperty(
        name="New", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0, default=_channels(Colors.added)
    )


class BIM_UL_salad_drawing_diff(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if not item:
            layout.label(text="", translate=False)
            return
        row = layout.row(align=True)
        row.label(text=item.name, icon="MOD_BOOLEAN")
        if not item.has_sources:
            row.label(text="", icon="ERROR")


class DrawingDiffPanel(bpy.types.Panel):
    bl_label = "Drawing Diff"
    bl_idname = "BONSAI_SALAD_PT_drawing_diff"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Bonsai Salad"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout

        if not DrawingDiffData.is_loaded or DrawingDiffData.is_stale():
            DrawingDiffData.load()
        if not DrawingDiffData.data["has_ifc"]:
            layout.label(text="No IFC file loaded.", icon="ERROR")
            return

        props = context.scene.drawing_diff

        if not props.is_loaded or props.loaded_path != DrawingDiffData.data["path"]:
            row = layout.row(align=True)
            row.label(text=f"{DrawingDiffData.data['total_comparisons']} Comparisons Found", icon="MOD_BOOLEAN")
            row.operator("bim.salad_drawing_diff_load", text="", icon="IMPORT")
            return

        row = layout.row()
        row.template_list(
            "BIM_UL_salad_drawing_diff", "", props, "comparisons", props, "active_comparison_index", rows=3
        )
        column = row.column(align=True)
        column.operator("bim.salad_drawing_diff_add", text="", icon="ADD")
        if 0 <= props.active_comparison_index < len(props.comparisons):
            active = props.comparisons[props.active_comparison_index]
            column.operator("bim.salad_drawing_diff_enable_editing", text="", icon="GREASEPENCIL").document = (
                active.ifc_definition_id
            )
            column.operator("bim.salad_drawing_diff_regenerate", text="", icon="FILE_REFRESH").document = (
                active.ifc_definition_id
            )
            column.operator("bim.salad_drawing_diff_open", text="", icon="URL").document = active.ifc_definition_id
            column.operator("bim.salad_drawing_diff_remove", text="", icon="X").document = active.ifc_definition_id
        column.separator()
        column.operator("bim.salad_drawing_diff_unload", text="", icon="CANCEL")

        if not props.comparisons:
            layout.label(text="No comparisons yet: add one.", icon="INFO")

        if not props.is_editing:
            return

        layout.row().prop(props, "source_mode", expand=True)

        if props.source_mode == "DOCUMENT":
            layout.prop(props, "existing_document")
            layout.prop(props, "project_document")
            layout.operator("bim.salad_drawing_diff_add_reference", icon="IMPORT")
        else:
            row = layout.row(align=True)
            row.prop(props, "existing_svg")
            row.operator("bim.salad_drawing_diff_select_svg", text="", icon="FILE_FOLDER").target = "existing_svg"

            row = layout.row(align=True)
            row.prop(props, "project_svg")
            row.operator("bim.salad_drawing_diff_select_svg", text="", icon="FILE_FOLDER").target = "project_svg"

        layout.prop(props, "include_linework")
        layout.prop(props, "exclude_filter")

        row = layout.row(align=True)
        row.prop(props, "color_unchanged", text="")
        row.prop(props, "color_demolished", text="")
        row.prop(props, "color_added", text="")

        layout.prop(props, "export_dxf", toggle=True)

        row = layout.row(align=True)
        if props.editing_document:
            row.operator("bim.salad_drawing_diff_save", icon="CHECKMARK")
        else:
            row.operator("bim.salad_drawing_diff", icon="CHECKMARK")
        row.operator("bim.salad_drawing_diff_disable_editing", text="", icon="CANCEL")


classes = (DrawingDiffComparison, DrawingDiffProperties, BIM_UL_salad_drawing_diff, DrawingDiffPanel)
