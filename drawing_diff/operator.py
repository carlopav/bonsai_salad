# Bonsai Salad — drawing_diff tool

import os

import bpy
from bonsai import tool
from bonsai.core import drawing as core_drawing
from bpy_extras.io_utils import ImportHelper
from lxml import etree

from . import data
from .core import diff, dxf_write, ifc_link, svg_read, svg_write
from .data import SourceDocuments


def active_drawing_document():
    """The document of the drawing selected in Bonsai's Drawings list, if any."""
    item = tool.Drawing.get_active_drawing_item()
    if item is None:
        return None
    drawing = tool.Ifc.get().by_id(item.ifc_definition_id)
    return tool.Drawing.get_drawing_document(drawing)


def active_drawing_svg():
    """The SVG of the drawing selected in Bonsai's Drawings list, if any."""
    document = active_drawing_document()
    if document is None:
        return None
    return tool.Drawing.get_document_uri(document)


def drawings_directory():
    """Where Bonsai writes the drawing SVGs, so the file browser opens there."""
    directory = tool.Ifc.resolve_uri(os.path.dirname(tool.Drawing.get_default_drawing_path("")))
    return directory if os.path.isdir(directory) else ""


def comparison_output(project_svg):
    return os.path.join(
        os.path.dirname(project_svg), f"{os.path.splitext(os.path.basename(project_svg))[0]} RAFFRONTO.svg"
    )


def comparison_by_id(operator, document_id):
    """The list survives a change of IFC file, its ids do not: without this a
    click on a stale row is a traceback."""
    try:
        return tool.Ifc.get().by_id(document_id)
    except (RuntimeError, AttributeError):
        operator.report({"ERROR"}, "This comparison is not in the open IFC: close the list and load it again.")
        return None


def source_paths(operator, document):
    """The two drawings a comparison was made of, absolute."""
    paths = []
    for key in (ifc_link.EXISTING, ifc_link.PROJECT):
        reference = ifc_link.source_reference(document, key)
        if reference is None or not reference.Location:
            operator.report(
                {"ERROR"},
                f"{document.Name} does not record its source drawings: it predates this feature, "
                "generate it once from the fields below the list.",
            )
            return None
        paths.append(tool.Ifc.resolve_uri(reference.Location))
    return paths


def document_svg(operator, document_id):
    """Where a document chosen in the lists lives, absolute. Its own location,
    never Bonsai's get_document_uri: on a comparison that answers with any of
    the three references it carries, so with one of its sources."""
    if not document_id:
        return ""
    try:
        document = tool.Ifc.get().by_id(int(document_id))
    except (RuntimeError, AttributeError):
        operator.report({"ERROR"}, "That document is not in the open IFC: close the list and load it again.")
        return ""
    location = ifc_link.own_location(document)
    if not location:
        operator.report({"ERROR"}, f"{document.Name or 'That document'} has no file to read.")
        return ""
    return tool.Ifc.resolve_uri(location)


def panel_sources(operator, props, fallback=False):
    """The two drawings the fields point at, whichever way they were chosen.
    Both modes end here as two paths, and it is the two paths that get stored
    on the comparison (ifc_link.set_sources): the switch is a way of choosing,
    not a second way of remembering."""
    if props.source_mode == "DOCUMENT":
        return [document_svg(operator, props.existing_document), document_svg(operator, props.project_document)]
    existing = bpy.path.abspath(props.existing_svg) if props.existing_svg else ""
    project = bpy.path.abspath(props.project_svg) if props.project_svg else ""
    if not project and fallback:
        project = active_drawing_svg() or ""
    return [existing, project]


def select_document(props, field, document):
    """Assigning an identifier the enum does not list raises: the cache is the
    list, so nothing is selected that was not loaded from the IFC."""
    if document is None or str(document.id()) not in SourceDocuments.ids():
        return False
    setattr(props, field, str(document.id()))
    return True


def select_source_documents(props, sources):
    """Fills the two lists from the paths a comparison remembers. A source that
    is no document of this IFC can only be shown as a path, and regenerating
    from whatever the lists happen to show would rebuild the wrong drawing: the
    fields fall back to files."""
    ifc_file, ifc_path = tool.Ifc.get(), tool.Ifc.get_path()
    selected = [
        select_document(props, field, ifc_link.find_svg_document(ifc_file, path, ifc_path))
        for field, path in zip(("existing_document", "project_document"), sources)
    ]
    if not all(selected):
        props.source_mode = "FILE"


def output_location(operator, document):
    """The comparison's own file, which is not one of the two sources it also references."""
    location = ifc_link.own_location(document)
    if not location:
        operator.report({"ERROR"}, f"{document.Name} has no file of its own to write to.")
        return None
    return tool.Ifc.resolve_uri(location)


def panel_settings(props):
    return svg_write.Settings(
        linework=props.include_linework,
        exclude=props.exclude_filter,
        colors=svg_write.Colors(
            unchanged=_hex(props.color_unchanged),
            demolished=_hex(props.color_demolished),
            added=_hex(props.color_added),
        ),
    )


def stored_settings(operator, document, fallback):
    """The settings a comparison was made with, read back from its own SVG. If
    the drawing is gone the panel answers for it, and that is worth saying: a
    regeneration would silently change what the sheet shows."""
    location = ifc_link.own_location(document)
    resolved = tool.Ifc.resolve_uri(location) if location else ""
    if not resolved or not os.path.isfile(resolved):
        operator.report(
            {"WARNING"},
            f"{document.Name} is not on disk: regenerating it with the settings currently in the panel.",
        )
        return fallback
    return svg_write.read_settings(resolved, fallback)


def generate(operator, context, existing_svg, project_svg, settings, output=None):
    """Shared by the first run and by every regeneration: same reading, same
    booleans, same writing, so a regenerated comparison cannot drift from a
    freshly generated one."""
    props = context.scene.drawing_diff
    for path in (existing_svg, project_svg):
        if not path or not os.path.isfile(path):
            operator.report({"ERROR"}, f"{path or 'The drawing'} is missing.")
            return None
        if not path.lower().endswith(".svg"):
            operator.report(
                {"ERROR"},
                f"{os.path.basename(path)} is not an SVG. The comparison reads the drawing SVGs that Bonsai "
                "writes in the drawings folder, not the IFC models themselves.",
            )
            return None

    exclude = svg_read.parse_exclude(settings.exclude)
    try:
        _, existing = svg_read.read_sections(existing_svg, exclude, settings.linework)
        canvas, project = svg_read.read_sections(project_svg, exclude, settings.linework)
        result = diff.compare(existing, project, linework=settings.linework)
    except (diff.DiffError, ValueError, etree.XMLSyntaxError) as e:
        operator.report({"ERROR"}, str(e))
        return None

    output = output or comparison_output(project_svg)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    svg_write.write(output, result, project[0], canvas, settings)
    dxf = os.path.splitext(output)[0] + ".dxf"
    # On a regeneration the DXF is refreshed only if it was asked for once.
    if props.export_dxf or os.path.isfile(dxf):
        dxf_write.write_dxf(dxf, result, settings.colors)

    register_document(output, existing_svg, project_svg, result)
    operator.report({"INFO"}, report_message(result, settings.linework))
    return output


def register_document(output, existing_svg, project_svg, result):
    ifc_file = tool.Ifc.get()
    document = ifc_link.ensure_reference(
        ifc_file,
        output,
        tool.Ifc.get_path(),
        lambda: core_drawing.add_document(
            tool.Ifc, tool.Drawing, "REFERENCE", uri=tool.Ifc.get_uri(output, use_relative_path=True)
        ),
    )
    ifc_link.nest_under(ifc_file, document, tool.Drawing.ensure_drawings_parent_document())
    ifc_link.set_sources(
        ifc_file,
        document,
        tool.Ifc.get_uri(existing_svg, use_relative_path=True),
        tool.Ifc.get_uri(project_svg, use_relative_path=True),
    )
    ifc_link.describe_reference(
        ifc_file,
        document,
        ifc_link.DiffInfo(
            existing_svg=existing_svg,
            project_svg=project_svg,
            tolerance_cm=result.tolerance_cm,
            min_area_m2=result.min_area_m2,
            area_demolished_m2=result.area_demolished_m2,
            area_new_m2=result.area_new_m2,
        ),
    )
    return document


def _load_settings(props, settings):
    props.include_linework = settings.linework
    props.exclude_filter = settings.exclude
    props.color_unchanged = _channels(settings.colors.unchanged)
    props.color_demolished = _channels(settings.colors.demolished)
    props.color_added = _channels(settings.colors.added)


class DrawingDiffAdd(bpy.types.Operator):
    """Shows the fields for a new comparison, below the list."""

    bl_idname = "bim.salad_drawing_diff_add"
    bl_label = "Add Comparison"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.drawing_diff
        SourceDocuments.load()
        props.existing_svg = ""
        props.project_svg = active_drawing_svg() or ""
        select_document(props, "project_document", active_drawing_document())
        # A new comparison starts from the defaults, not from the settings of
        # whichever one was edited last.
        _load_settings(props, svg_write.Settings(exclude=", ".join(svg_read.DEFAULT_EXCLUDE)))
        props.editing_document = 0
        props.is_editing = True
        return {"FINISHED"}


class DrawingDiffEnableEditing(bpy.types.Operator):
    """Edits the selected comparison: its own source drawings fill the fields
    below the list."""

    bl_idname = "bim.salad_drawing_diff_enable_editing"
    bl_label = "Edit Comparison"
    bl_options = {"REGISTER", "UNDO"}

    document: bpy.props.IntProperty()

    def execute(self, context):
        props = context.scene.drawing_diff
        SourceDocuments.load()
        document = comparison_by_id(self, self.document)
        if document is None:
            return
        sources = source_paths(self, document)
        if sources is None:
            return {"CANCELLED"}
        props.existing_svg, props.project_svg = sources
        select_source_documents(props, sources)
        _load_settings(props, stored_settings(self, document, panel_settings(props)))
        props.editing_document = self.document
        props.is_editing = True
        return {"FINISHED"}


class DrawingDiffDisableEditing(bpy.types.Operator):
    """Leaves the fields without touching the comparison."""

    bl_idname = "bim.salad_drawing_diff_disable_editing"
    bl_label = "Cancel"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.drawing_diff
        props.is_editing = False
        props.editing_document = 0
        return {"FINISHED"}


class DrawingDiffSelectSvg(bpy.types.Operator, ImportHelper):
    """Picks a drawing SVG, starting from the folder Bonsai writes them into."""

    bl_idname = "bim.salad_drawing_diff_select_svg"
    bl_label = "Choose SVG"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.svg", options={"HIDDEN"})
    filename_ext = ".svg"
    # The panel property to fill in.
    target: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        directory = drawings_directory()
        if directory:
            self.filepath = os.path.join(directory, "")
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        setattr(context.scene.drawing_diff, self.target, self.filepath)
        return {"FINISHED"}


class DrawingDiffAddReference(bpy.types.Operator, ImportHelper):
    """Brings the existing state SVG into this IFC as a REFERENCE document, so
    it can be picked in the lists and laid out on a sheet like any other
    drawing. The import itself is Bonsai's bim.add_reference, which also
    carries the IFC transaction: wrapping it in a second one would nest them."""

    bl_idname = "bim.salad_drawing_diff_add_reference"
    bl_label = "Import SVG as Reference"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: bpy.props.StringProperty(default="*.svg", options={"HIDDEN"})
    filename_ext = ".svg"
    use_relative_path: bpy.props.BoolProperty(name="Use Relative Path", default=True)

    def execute(self, context):
        bpy.ops.bim.add_reference(
            directory=os.path.dirname(self.filepath),
            files=[{"name": os.path.basename(self.filepath)}],
            use_relative_path=self.use_relative_path,
        )
        import_comparisons(context)
        return {"FINISHED"}


class DrawingDiff(bpy.types.Operator, tool.Ifc.Operator):
    """Compares the cut geometry of two drawings, the existing state and the
    project, and writes the demolitions/additions comparison drawing next to
    the project SVG. The two drawings must be cut on the same plane; nothing
    is matched by GUID, the three states come from the union of each drawing's
    cut polygons (demolished = existing - project, new = project - existing,
    unchanged = the intersection)."""

    bl_idname = "bim.salad_drawing_diff"
    bl_label = "Generate Comparison"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        props = context.scene.drawing_diff
        existing_svg, project_svg = panel_sources(self, props, fallback=True)
        if not project_svg:
            self.report({"ERROR"}, "No project SVG: choose one, or select a drawing in Bonsai's Drawings list.")
            return
        output = generate(self, context, existing_svg, project_svg, panel_settings(props))
        if output:
            props.is_editing = False
            import_comparisons(context)
            tool.Drawing.open_svg(output)


class DrawingDiffSave(bpy.types.Operator, tool.Ifc.Operator):
    """Rebuilds the comparison being edited over its own file, from the two
    drawings now in the fields."""

    bl_idname = "bim.salad_drawing_diff_save"
    bl_label = "Apply and Regenerate"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        props = context.scene.drawing_diff
        document = comparison_by_id(self, props.editing_document)
        if document is None:
            return
        output = output_location(self, document)
        if output is None:
            return
        existing_svg, project_svg = panel_sources(self, props)
        if generate(self, context, existing_svg, project_svg, panel_settings(props), output):
            props.is_editing = False
            props.editing_document = 0
            import_comparisons(context)


class DrawingDiffRegenerate(bpy.types.Operator, tool.Ifc.Operator):
    """Rebuilds a comparison from the two drawings it was made of, as they are
    now: regenerate the project drawing in Bonsai, then this."""

    bl_idname = "bim.salad_drawing_diff_regenerate"
    bl_label = "Regenerate"
    bl_options = {"REGISTER", "UNDO"}

    document: bpy.props.IntProperty()

    def _execute(self, context):
        document = comparison_by_id(self, self.document)
        if document is None:
            return
        sources = source_paths(self, document)
        if sources is None:
            return
        output = output_location(self, document)
        if output is None:
            return
        settings = stored_settings(self, document, panel_settings(context.scene.drawing_diff))
        if generate(self, context, sources[0], sources[1], settings, output):
            import_comparisons(context)


class DrawingDiffOpen(bpy.types.Operator):
    """Opens the comparison in the system viewer."""

    bl_idname = "bim.salad_drawing_diff_open"
    bl_label = "Open Comparison"
    bl_options = {"REGISTER"}

    document: bpy.props.IntProperty()

    def execute(self, context):
        document = comparison_by_id(self, self.document)
        if document is None:
            return {"CANCELLED"}
        output = output_location(self, document)
        if output is None:
            return {"CANCELLED"}
        if not os.path.isfile(output):
            self.report({"ERROR"}, f"{os.path.basename(output)} is not on disk: regenerate it.")
            return {"CANCELLED"}
        tool.Drawing.open_svg(output)
        return {"FINISHED"}


class DrawingDiffRemove(bpy.types.Operator, tool.Ifc.Operator):
    """Removes the comparison from the IFC. The SVG and the DXF stay on disk:
    a comparison is a deliverable, and deleting it is not this button's job."""

    bl_idname = "bim.salad_drawing_diff_remove"
    bl_label = "Remove Comparison"
    bl_options = {"REGISTER", "UNDO"}

    document: bpy.props.IntProperty()

    def _execute(self, context):
        document = comparison_by_id(self, self.document)
        if document is None:
            return
        name = document.Name or "Unnamed"
        # Bonsai's own removal: it takes the document's references with it, and
        # nothing else, because the comparison is a sibling of the drawings and
        # has no children of its own to cascade into.
        core_drawing.remove_document(tool.Ifc, tool.Drawing, "REFERENCE", document)
        props = context.scene.drawing_diff
        if props.editing_document == self.document:
            props.is_editing = False
            props.editing_document = 0
        import_comparisons(context)
        self.report({"INFO"}, f"{name} removed from the IFC. Its files are still on disk.")


class DrawingDiffLoad(bpy.types.Operator):
    """Reads the comparisons stored in the IFC into the list."""

    bl_idname = "bim.salad_drawing_diff_load"
    bl_label = "Load Comparisons"
    bl_options = {"REGISTER"}

    def execute(self, context):
        import_comparisons(context)
        props = context.scene.drawing_diff
        props.loaded_path = tool.Ifc.get_path()
        props.is_loaded = True
        return {"FINISHED"}


class DrawingDiffUnload(bpy.types.Operator):
    """Closes the list, leaving the count. Nothing in the IFC is touched."""

    bl_idname = "bim.salad_drawing_diff_unload"
    bl_label = "Close List"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.drawing_diff
        props.comparisons.clear()
        props.is_loaded = False
        props.is_editing = False
        props.editing_document = 0
        data.refresh()
        return {"FINISHED"}


def import_comparisons(context):
    """The panel never queries the IFC while drawing: the list is filled here,
    the way Bonsai fills its own (tool/drawing.py:1186), and the items of the
    two document lists along with it."""
    data.refresh()
    SourceDocuments.load()
    props = context.scene.drawing_diff
    props.comparisons.clear()
    if tool.Ifc.get() is None:
        return
    for document in ifc_link.comparisons(tool.Ifc.get()):
        item = props.comparisons.add()
        item.ifc_definition_id = document.id()
        item.name = document.Name or "Unnamed"
        item.description = document.Description or ""
        item.has_sources = all(
            ifc_link.source_reference(document, key) is not None for key in (ifc_link.EXISTING, ifc_link.PROJECT)
        )
    props.active_comparison_index = min(props.active_comparison_index, max(len(props.comparisons) - 1, 0))


def report_message(result, linework=False):
    message = (
        f"Demolished {result.area_demolished_m2:.2f} m2, new {result.area_new_m2:.2f} m2, "
        f"unchanged {result.area_unchanged_m2:.2f} m2. "
    )
    if linework:
        message += (
            f"Linework demolished {result.length_demolished_m:.2f} m, new {result.length_new_m:.2f} m, "
            f"unchanged {result.length_unchanged_m:.2f} m. "
        )
    message += (
        f"Discarded subpaths {result.existing_discarded}/{result.project_discarded}, "
        f"groups without geometry {result.existing_empty_groups}/{result.project_empty_groups}."
    )
    excluded = result.existing_excluded_classes + result.project_excluded_classes
    if excluded:
        message += f" Excluded groups: {sum(excluded.values())} ({', '.join(sorted(excluded))})."
    return message


def _channels(color):
    return [channel / 255 for channel in svg_write.rgb(color)]


def _hex(color):
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in color)


classes = (
    DrawingDiffAdd,
    DrawingDiffEnableEditing,
    DrawingDiffDisableEditing,
    DrawingDiffSelectSvg,
    DrawingDiffAddReference,
    DrawingDiff,
    DrawingDiffSave,
    DrawingDiffRegenerate,
    DrawingDiffOpen,
    DrawingDiffRemove,
    DrawingDiffLoad,
    DrawingDiffUnload,
)
