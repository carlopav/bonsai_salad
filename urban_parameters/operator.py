# Bonsai Salad — urban_parameters tool

import os
from pathlib import Path

import bpy
from bonsai import tool
from bonsai.core import drawing as core_drawing

from .core import ods, quantities

# Bonsai names a directory for sheets, layouts, titleblocks and drawings
# (bim/ui.py:295), but none for schedules: its Add Schedule takes a file from
# wherever it lies. This one is ours, shaped like those.
SCHEDULES_DIR = "schedules"


def target_zones(context):
    """The zones a table is built from: every spatial zone of the ObjectType
    chosen in the panel. Homogeneous by construction — a total taken over
    zones of different kinds says nothing."""
    return quantities.spatial_zones(tool.Ifc.get(), context.scene.urban_parameters.object_type)


def selected_zones(context):
    """Only the selected zones: what a button that edits one zone at a time
    works on, never the whole file by accident."""
    zones = [tool.Ifc.get_entity(obj) for obj in context.selected_objects]
    return [zone for zone in zones if zone and zone.is_a(quantities.ZONE_CLASS)]


def _measured(operator, context, zones=None):
    """The measured rows, or None with the reason already reported."""
    zones = target_zones(context) if zones is None else zones
    if not zones:
        operator.report(
            {"ERROR"}, f"No {quantities.ZONE_CLASS} of type {context.scene.urban_parameters.object_type}."
        )
        return None
    try:
        rows = quantities.measure_zones(tool.Ifc.get(), zones)
    except quantities.UnmeasurableGeometry as error:
        operator.report({"ERROR"}, f"{error} Close the geometry and try again.")
        return None
    if not rows:
        operator.report({"ERROR"}, f"None of the {len(zones)} zones has a body to measure.")
        return None
    return rows


def _skipped(zones, rows):
    skipped = len(zones) - len(rows)
    return f" {skipped} without a body were skipped." if skipped else ""


class SetUrbanCoefficient(bpy.types.Operator, tool.Ifc.Operator):
    """Sets how the selected IfcSpatialZones enter the count, as the
    Coefficiente of their "Parametri urbanistici" quantity set: 1 counts the
    zone in full, -1 detracts it from the others, a fraction counts it in
    part. Only this button and a hand edit ever change it — a take-off leaves
    a coefficient that is already there alone"""

    bl_idname = "bim.salad_set_urban_coefficient"
    bl_label = "Set Coefficient"
    bl_options = {"REGISTER", "UNDO"}

    coefficient: bpy.props.FloatProperty(name="Coefficient", default=quantities.DEFAULT_COEFFICIENT)

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        zones = selected_zones(context)
        if not zones:
            self.report({"ERROR"}, f"Select the {quantities.ZONE_CLASS} to weigh first.")
            return {"CANCELLED"}
        ifc_file = tool.Ifc.get()
        for zone in zones:
            quantities.write_coefficient(ifc_file, zone, self.coefficient)
        self.report({"INFO"}, f"{len(zones)} zones set to {self.coefficient:g}.")


class QuantifyUrbanParameters(bpy.types.Operator, tool.Ifc.Operator):
    """Measures the selected IfcSpatialZones and writes Superficie lorda,
    Altezza media and Volume urbanistico into a "Parametri urbanistici"
    quantity set on each one, plus a Coefficiente of 1 the first time. The
    gross area is the zone's footprint projected along world Z, the mean
    height its volume over that area.
    ALT+CLICK to take off every zone of the ObjectType chosen above instead"""

    bl_idname = "bim.salad_quantify_urban_parameters"
    bl_label = "Quantify Zones"
    bl_options = {"REGISTER", "UNDO"}

    all_of_type: bpy.props.BoolProperty(
        name="Every Zone of the Type",
        description="Take off every zone of the chosen ObjectType, not only the selected ones",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def invoke(self, context, event):
        # Always set, never only on ALT: an operator property keeps the value
        # of the last run, and a stray ALT would go on quantifying the file.
        self.all_of_type = event.alt
        return self.execute(context)

    def _execute(self, context):
        zones = target_zones(context) if self.all_of_type else selected_zones(context)
        if not zones and not self.all_of_type:
            self.report({"ERROR"}, f"Select the {quantities.ZONE_CLASS} to take off, or ALT+CLICK for the whole type.")
            return {"CANCELLED"}
        rows = _measured(self, context, zones)
        if rows is None:
            return {"CANCELLED"}
        ifc_file = tool.Ifc.get()
        for row in rows:
            quantities.write_qto(ifc_file, row.zone, row.area, row.height, row.volume)
        self.report({"INFO"}, f"Quantified {len(rows)} zones.{_skipped(zones, rows)}")


class ExportUrbanParameters(bpy.types.Operator, tool.Ifc.Operator):
    """Writes the urban parameters of the IfcSpatialZones of the chosen
    ObjectType as an ODS table: the zones that add up with their subtotal, the
    detracted ones with theirs, and a net total. Subtotals and total are
    formulas over the rows above them.

    The table is the project's schedule for that ObjectType: it goes to
    schedules/<ObjectType>.ods next to the IFC, overwriting the one already
    there, and is registered among Bonsai's schedules the first time so it can
    be built and placed on a sheet"""

    bl_idname = "bim.salad_export_urban_parameters"
    bl_label = "Export Table"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if tool.Ifc.get() is None:
            return False
        if not tool.Ifc.get_path():
            cls.poll_message_set("Save the IFC file first: the schedule is written next to it.")
            return False
        return True

    def _execute(self, context):
        zones = target_zones(context)
        rows = _measured(self, context, zones)
        if rows is None:
            return {"CANCELLED"}
        ifc_file = tool.Ifc.get()
        path = schedule_path(context.scene.urban_parameters.object_type)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ods.write(path, quantities.headers(ifc_file), quantities.sections(rows), quantities.NET)
        except OSError as error:
            self.report({"ERROR"}, f"Could not write {path}: {error}")
            return {"CANCELLED"}
        added = self._add_schedule(path)
        self.report(
            {"INFO"},
            f"{len(rows)} zones written to {path}."
            f"{' Added to the schedules.' if added else ' Its schedule was already there.'}"
            f"{_skipped(zones, rows)}",
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


def schedule_path(object_type):
    """Where the table of one ObjectType lives: named after the type, in the
    project's schedules directory. One file per type, rewritten in place, so
    the schedule registered in the IFC keeps pointing at the current numbers."""
    name = tool.Drawing.sanitise_filename(object_type).strip() or "schedule"
    return os.path.join(os.path.dirname(tool.Ifc.get_path()), SCHEDULES_DIR, f"{name}.ods")


def _schedule_paths():
    """Where each of Bonsai's schedules points, absolute."""
    documents = [d for d in tool.Ifc.get().by_type("IfcDocumentInformation") if d.Scope == "SCHEDULE"]
    return [uri for uri in (tool.Drawing.get_document_uri(d) for d in documents) if uri]


classes = (SetUrbanCoefficient, QuantifyUrbanParameters, ExportUrbanParameters)
