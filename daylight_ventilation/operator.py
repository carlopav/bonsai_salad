# Bonsai Salad — daylight_ventilation tool

import os
from pathlib import Path

import bpy
from bonsai import tool
from bonsai.core import drawing as core_drawing

from .core import boundaries, diagnostics, ods, openings, ratios
from .data import Summary


def selected_spaces(context):
    """Only the selected rooms: what a button that edits one room at a time works
    on, never the whole file by accident."""
    elements = [tool.Ifc.get_entity(obj) for obj in context.selected_objects]
    return [element for element in elements if element and element.is_a("IfcSpace")]


def selected_fillings(context):
    """Only the selected windows and doors, the same way."""
    elements = [tool.Ifc.get_entity(obj) for obj in context.selected_objects]
    return [element for element in elements if element and (element.is_a("IfcWindow") or element.is_a("IfcDoor"))]


def colour_diagnostics(context):
    """Paints each room and the openings that count for it in one colour, the
    rest in red, and puts the viewport where those colours show. Returns
    (groups, excluded). Elements with no Blender object are not loaded and are
    skipped."""
    paired, excluded = diagnostics.groups(tool.Ifc.get())
    for index, (space, fillings) in enumerate(paired):
        colour = (*diagnostics.colour_for(index), 1.0)
        # An opening EXTERNAL to two rooms is in both tuples; whichever room
        # paints it last owns its colour on screen.
        for element in (space, *fillings):
            if obj := tool.Ifc.get_object(element):
                obj.color = colour
    for element in excluded:
        if obj := tool.Ifc.get_object(element):
            obj.color = (*diagnostics.EXCLUDED, 1.0)
    props = context.scene.daylight_ventilation
    if space_data := tool.Blender.get_view3d_space():
        # Recolouring while the switch is already on must not overwrite the
        # mode saved on first activation with the "OBJECT" this call sets.
        if not props.diagnostics:
            props.previous_color_type = space_data.shading.color_type
        space_data.shading.color_type = "OBJECT"
    return len(paired), len(excluded)


def clear_diagnostics(context):
    """Bonsai's own reset, by the user's decision: it whitens every visible
    object, not only ours, and clears the Search module's colourscheme. It does
    not touch the viewport's colour mode, so putting that back is ours.

    Bonsai hides every IfcSpace on import, and a filling with no spatial
    container lands in a collection hidden by default: reset_object_colours
    skips both, so every room and filling this feature could have coloured is
    whitened again here, whatever its current visibility."""
    bpy.ops.bim.reset_object_colours()
    ifc_file = tool.Ifc.get()
    for element in (*ifc_file.by_type("IfcSpace"), *openings.fillings(ifc_file)):
        if obj := tool.Ifc.get_object(element):
            obj.color = (1.0, 1.0, 1.0, 1.0)
    props = context.scene.daylight_ventilation
    if space_data := tool.Blender.get_view3d_space():
        space_data.shading.color_type = props.previous_color_type or "MATERIAL"
    props.previous_color_type = ""


class SetDaylightRequirement(bpy.types.Operator, tool.Ifc.Operator):
    """Sets the two ratios the selected IfcSpaces have to reach, as the
    "Requisiti aeroilluminanti" property set: 0.125 is the usual eighth, 0 marks
    a room the regulation does not ask anything of.

    Each room's stored verdict goes with it — it was reached against the
    requirement you are replacing — until the next Calcola"""

    bl_idname = "bim.salad_set_daylight_requirement"
    bl_label = "Set Requirement"
    bl_options = {"REGISTER", "UNDO"}

    daylight: bpy.props.FloatProperty(name="Illuminazione", default=ratios.DEFAULT_REQUIREMENT)
    air: bpy.props.FloatProperty(name="Aerazione", default=ratios.DEFAULT_REQUIREMENT)

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        spaces = selected_spaces(context)
        if not spaces:
            self.report({"ERROR"}, "Select the IfcSpaces to set first.")
            return {"CANCELLED"}
        for space in spaces:
            ratios.write_requirements(tool.Ifc.get(), space, self.daylight, self.air)
        Summary.refresh()
        self.report({"INFO"}, f"{len(spaces)} rooms set.")


class PrepareDaylightOverrides(bpy.types.Operator, tool.Ifc.Operator):
    """Copies the measured clear opening of the selected windows and doors into
    their "Superficie illuminante" and "Superficie aerante", so both are there to
    be edited instead of typed from nothing.

    From then on those two areas are yours: what the calculation measures no
    longer counts for those fillings. An override already written is left alone,
    and a filling with no clear opening yet has nothing to copy"""

    bl_idname = "bim.salad_prepare_daylight_overrides"
    bl_label = "Prepara override"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        fillings = selected_fillings(context)
        if not fillings:
            self.report({"ERROR"}, "Select the windows or doors to prepare first.")
            return {"CANCELLED"}
        # The summary is not invalidated: a prepared override equals the
        # measurement it was copied from, so nothing it reports has changed.
        outcomes = [ratios.prepare_overrides(tool.Ifc.get(), filling) for filling in fillings]
        already_set, unmeasured = outcomes.count(ratios.ALREADY_SET), outcomes.count(ratios.UNMEASURED)
        self.report(
            {"INFO"},
            f"{outcomes.count(ratios.PREPARED)} fillings prepared."
            + (f" {already_set} already corrected." if already_set else "")
            + (f" {unmeasured} with no clear opening to copy." if unmeasured else ""),
        )


class QuantifyDaylight(bpy.types.Operator, tool.Ifc.Operator):
    """Measures the clear opening of every window and door, records the room each
    one serves as an IfcRelSpaceBoundary where it has none yet, and writes each
    room's areas, ratios and verdict into its "Requisiti aeroilluminanti"
    property set.

    Nothing already in the file is overwritten: not a boundary, not an area you
    corrected by hand, not a requirement you edited"""

    bl_idname = "bim.salad_quantify_daylight"
    bl_label = "Calcola"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        summary = ratios.quantify(tool.Ifc.get())
        Summary.store(summary)
        self.report(
            {"INFO"},
            f"{len(summary.rows)} rooms checked, {summary.boundaries_written} boundaries written."
            + (f" {len(summary.orphans)} orphan fillings." if summary.orphans else "")
            + (f" {len(summary.unmeasurable)} unmeasured." if summary.unmeasurable else "")
            + (f" {len(summary.disagreeing)} disagreeing." if summary.disagreeing else ""),
        )
        # Colours that describe a previous run are the worst way for a
        # diagnostic to be wrong.
        if context.scene.daylight_ventilation.diagnostics:
            colour_diagnostics(context)


# Bonsai names a directory for sheets, layouts, titleblocks and drawings
# (bim/ui.py:295), but none for schedules: this one is ours, shaped like those.
SCHEDULES_DIR = "schedules"
SCHEDULE_NAME = "Rapporti aeroilluminanti"


def _select(elements):
    """Puts the given elements in the viewport selection, active on the first.

    select_products skips what the view layer does not hold — selecting an
    object in an excluded collection raises — so only what it did select can be
    counted or made active."""
    bpy.ops.object.select_all(action="DESELECT")
    tool.Spatial.select_products(elements)
    objects = [obj for obj in (tool.Ifc.get_object(element) for element in elements) if obj and obj.select_get()]
    if objects:
        tool.Blender.set_active_object(objects[0])
    return len(objects)


class SelectDisagreeingOpenings(bpy.types.Operator):
    """Selects the windows and doors whose space boundary names a room the
    geometry does not put them anywhere near — so you can look at them before
    letting anything be rewritten"""

    bl_idname = "bim.salad_select_disagreeing_openings"
    bl_label = "Seleziona"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(Summary.load().get("disagreeing"))

    def execute(self, context):
        count = _select(Summary.load()["disagreeing"])
        self.report({"INFO"}, f"{count} fillings selected.")
        return {"FINISHED"}


class SelectUnverifiedSpaces(bpy.types.Operator):
    """Selects the rooms that do not reach their required ratio"""

    bl_idname = "bim.salad_select_unverified_spaces"
    bl_label = "Seleziona"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(Summary.load().get("unverified"))

    def execute(self, context):
        count = _select(Summary.load()["unverified"])
        self.report({"INFO"}, f"{count} rooms selected.")
        return {"FINISHED"}


class RefreshSpaceBoundaries(bpy.types.Operator, tool.Ifc.Operator):
    """Removes the space boundaries that name the wrong room and writes the ones
    the geometry gives instead. Acts on the selected windows and doors when there
    is a selection, on every disagreeing one otherwise.

    The only thing in this tool that deletes a boundary. A boundary you corrected
    by hand is never reported as disagreeing, so it never reaches this button,
    and one written by another authoring tool — with a contact surface, or 2nd
    level — is left alone even when it does"""

    bl_idname = "bim.salad_refresh_space_boundaries"
    bl_label = "Aggiorna"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        ifc_file = tool.Ifc.get()
        disagreeing = boundaries.disagreeing(openings.proposals(ifc_file))
        selected = {tool.Ifc.get_entity(obj) for obj in context.selected_objects}
        selected.discard(None)
        if selected:
            disagreeing = [proposal for proposal in disagreeing if proposal.filling in selected]
        if not disagreeing:
            self.report({"INFO"}, "No disagreeing boundary to update.")
            return {"CANCELLED"}
        updated, kept = boundaries.refresh(ifc_file, disagreeing)
        Summary.refresh()
        self.report(
            {"INFO"},
            f"{updated} associations updated."
            + (f" {kept} left alone: their boundaries were written elsewhere." if kept else ""),
        )


class ExportDaylightSchedule(bpy.types.Operator, tool.Ifc.Operator):
    """Writes the check as an ODS table, one row per room grouped by storey, with
    the ratios and the verdict as formulas over the areas beside them.

    The table goes to schedules/Rapporti aeroilluminanti.ods next to the IFC,
    overwriting the one already there, and is registered among Bonsai's schedules
    the first time so it can be built and placed on a sheet"""

    bl_idname = "bim.salad_export_daylight_schedule"
    bl_label = "Esporta"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if tool.Ifc.get() is None:
            return False
        if not tool.Ifc.get_path():
            cls.poll_message_set("Save the IFC file first: the schedule is written next to it.")
            return False
        # Before the first Calcola no room has a clear opening yet and every one
        # of them would be written down as failing. Asked of the file rather
        # than of the panel's cache, which any edit invalidates.
        if not ratios.is_quantified(tool.Ifc.get()):
            cls.poll_message_set("Run Calcola first: the schedule reports what the last calculation found.")
            return False
        return True

    def _execute(self, context):
        ifc_file = tool.Ifc.get()
        rows = ratios.measure_spaces(ifc_file, ifc_file.by_type("IfcSpace"))
        if not rows:
            self.report({"ERROR"}, "No IfcSpace to write.")
            return {"CANCELLED"}
        path = schedule_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ods.write(path, ratios.headers(ifc_file), ratios.sections(ifc_file, rows))
        except OSError as error:
            self.report({"ERROR"}, f"Could not write {path}: {error}")
            return {"CANCELLED"}
        added = self._add_schedule(path)
        self.report(
            {"INFO"},
            f"{len(rows)} rooms written to {path}."
            f"{' Added to the schedules.' if added else ' Its schedule was already there.'}",
        )

    def _add_schedule(self, path):
        """Registers the table among Bonsai's schedules, unless one already
        points at it — the file has just been overwritten in place, so a second
        document would only be a duplicate. True when one was added."""
        if any(os.path.samefile(existing, path) for existing in _schedule_paths() if os.path.exists(existing)):
            return False
        uri = tool.Ifc.get_uri(Path(path), use_relative_path=True)
        core_drawing.add_document(tool.Ifc, tool.Drawing, "SCHEDULE", uri=str(uri))
        return True


def schedule_path():
    """Where the table lives: one file for the project, rewritten in place, so
    the schedule registered in the IFC keeps pointing at the current numbers."""
    name = tool.Drawing.sanitise_filename(SCHEDULE_NAME).strip() or "schedule"
    return os.path.join(os.path.dirname(tool.Ifc.get_path()), SCHEDULES_DIR, f"{name}.ods")


def _schedule_paths():
    """Where each of Bonsai's schedules points, absolute."""
    documents = [d for d in tool.Ifc.get().by_type("IfcDocumentInformation") if d.Scope == "SCHEDULE"]
    return [uri for uri in (tool.Drawing.get_document_uri(d) for d in documents) if uri]


class ToggleDaylightDiagnostics(bpy.types.Operator):
    """Colours every room and the windows and doors that count for it alike, and
    everything the check knows but does not count in red: an opening that is
    internal, orphan, or whose area could not be established, and a room left
    without a valid one.

    Writes nothing to the IFC. Switching off resets the colours through Bonsai's
    own Reset Colours, which whitens every visible object"""

    bl_idname = "bim.salad_toggle_daylight_diagnostics"
    bl_label = "Diagnostica associazioni"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ifc_file = tool.Ifc.get()
        if ifc_file is None:
            return False
        if not ratios.is_quantified(ifc_file):
            cls.poll_message_set("Run Calcola first: no opening has a measured area yet.")
            return False
        return True

    def execute(self, context):
        props = context.scene.daylight_ventilation
        if props.diagnostics:
            clear_diagnostics(context)
            props.diagnostics = False
            self.report({"INFO"}, "Diagnostica spenta.")
        else:
            paired, excluded = colour_diagnostics(context)
            props.diagnostics = True
            self.report({"INFO"}, f"{paired} gruppi, {excluded} elementi in rosso.")
        return {"FINISHED"}


classes = (
    SetDaylightRequirement,
    PrepareDaylightOverrides,
    QuantifyDaylight,
    SelectDisagreeingOpenings,
    SelectUnverifiedSpaces,
    RefreshSpaceBoundaries,
    ExportDaylightSchedule,
    ToggleDaylightDiagnostics,
)
