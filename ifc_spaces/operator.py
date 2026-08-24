# Bonsai Salad — ifc_spaces tool

import os
from pathlib import Path

import bpy
import ifccsv
import ifcopenshell.api.attribute
from bonsai import tool
from bonsai.core import drawing as core_drawing

from .core import numbering, schedule, sheet_style

# Bonsai names a directory for sheets, layouts, titleblocks and drawings
# (bim/ui.py:295), but none for schedules: its Add Schedule takes a file from
# wherever it lies. This one is ours, shaped like those.
SCHEDULES_DIR = "schedules"
SCHEDULE_NAME = "Abaco dei locali"


def selected_spaces(context):
    """Only the selected rooms: whatever else is selected is not this panel's
    business."""
    elements = [tool.Ifc.get_entity(obj) for obj in context.selected_objects]
    return [element for element in elements if element and element.is_a("IfcSpace")]


def _select(elements):
    """Puts the given elements in the viewport selection, active on the first.

    select_products skips what the view layer does not hold — selecting an
    object in an excluded collection raises — so only what it did select can be
    made active."""
    bpy.ops.object.select_all(action="DESELECT")
    tool.Spatial.select_products(elements)
    objects = [obj for obj in (tool.Ifc.get_object(element) for element in elements) if obj and obj.select_get()]
    if objects:
        tool.Blender.set_active_object(objects[0])


class NumberSpaces(bpy.types.Operator, tool.Ifc.Operator):
    """Renames the selected IfcSpaces as prefix01, prefix02, and so on. One
    counter climbs the storeys by elevation, and inside a storey the numbering
    starts at the largest room and goes on to the nearest one after it, the way
    you would walk the plan.

    Only the Name is written. A room without a storey, or whose geometry cannot
    be read, stops the whole run: nothing is renamed and those rooms are left
    selected for you to look at"""

    bl_idname = "bim.salad_number_spaces"
    bl_label = "Rinumera"
    bl_options = {"REGISTER", "UNDO"}

    prefix: bpy.props.StringProperty(name="Prefisso")
    rename_named: bpy.props.BoolProperty(name="Rinumera anche i locali già nominati", default=True)

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def _execute(self, context):
        spaces = selected_spaces(context)
        if not spaces:
            self.report({"ERROR"}, "Select the IfcSpaces to number first.")
            return {"CANCELLED"}
        ifc_file = tool.Ifc.get()
        plan = numbering.plan(ifc_file, spaces, self.prefix, self.rename_named)
        if plan.unplaceable:
            _select(plan.unplaceable)
            self.report(
                {"ERROR"},
                f"{len(plan.unplaceable)} rooms have no storey or no readable geometry: "
                "nothing was renamed, and they are now selected.",
            )
            return {"CANCELLED"}
        for space, name in plan.assignments:
            ifcopenshell.api.attribute.edit_attributes(ifc_file, product=space, attributes={"Name": name})
            tool.Root.set_object_name(tool.Ifc.get_object(space), space)
        self.report(
            {"INFO"},
            f"{len(plan.assignments)} rooms renumbered over {plan.storeys} storeys."
            + (f" {len(plan.kept)} already named were left alone." if plan.kept else ""),
        )
        # The numbering is what the user asked for: a name it repeats elsewhere
        # is worth saying, not worth refusing.
        if plan.clashes:
            self.report({"WARNING"}, f"{len(plan.clashes)} rooms this run left alone already carry one of these names.")


class ExportSpacesSchedule(bpy.types.Operator, tool.Ifc.Operator):
    """Writes the room schedule of every storey as an ODS table, one file per
    storey: code, name, net floor area, volume, the daylight and ventilation
    area the room owes and the two it has, and the verdict.

    Nothing is measured here, and nothing is computed. Every column is read off
    the model through IfcCsv — the areas and the volume from
    Qto_SpaceBaseQuantities, the rest from the pset the Rapporti aeroilluminanti
    check writes — so the table says what the file says and no more: a quantity
    nobody took off prints a dash instead of a zero. Take off the rooms and run
    the check first. Balconies, parking bays and GFA overlays are not rooms and
    stay out.

    Each table goes to schedules/Abaco dei locali - <piano>.ods next to the IFC,
    overwriting the one already there, and is registered among Bonsai's
    schedules the first time so it can be built and placed on a sheet.

    The sheet is styled as it is written — column widths totalling the 190 mm the
    table is given on a sheet, a framed heading over ruled rows, the measures
    aligned right, and a print range around the content. Bonsai's renderer reads
    all of that off the file, so the drawing follows it"""

    bl_idname = "bim.salad_export_spaces_schedule"
    bl_label = "Esporta abaco"
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
        ifc_file = tool.Ifc.get()
        grouping = schedule.by_storey(ifc_file)
        if not grouping.storeys:
            self.report({"ERROR"}, "No room belongs to a storey: there is no schedule to write.")
            return {"CANCELLED"}
        columns = schedule.columns(ifc_file)
        labels = schedule.labels([storey for storey, _ in grouping.storeys])

        added = rooms = 0
        for label, (_, spaces) in zip(labels, grouping.storeys):
            path = schedule_path(label)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                self._write(ifc_file, spaces, columns, path)
            except OSError as error:
                self.report({"ERROR"}, f"Could not write {path}: {error}")
                return {"CANCELLED"}
            added += self._add_schedule(path)
            rooms += len(spaces)

        self.report(
            {"INFO"},
            f"{rooms} rooms written over {len(labels)} storeys."
            + (f" {added} added to the schedules." if added else ""),
        )
        # One file per storey has nowhere to write a room outside one: saying so
        # is the only way the user learns it is missing from every table.
        if grouping.orphans:
            self.report({"WARNING"}, f"{len(grouping.orphans)} rooms belong to no storey and are in no table.")

    def _write(self, ifc_file, spaces, columns, path):
        """One storey's table, through the exporter Bonsai already carries.

        The queries are handed over as a fresh list each time: IfcCsv reads the
        columns of its sort and formatting rules by their position in it.

        The look is written into the sheet afterwards rather than into a
        stylesheet beside it, because that is where Bonsai's schedule renderer
        reads widths, alignment and rules from."""
        ifccsv.IfcCsv().export(
            ifc_file,
            spaces,
            [query for query, _ in columns],
            headers=[header for _, header in columns],
            output=path,
            format="ods",
            # The file is written whole every time. IfcCsv can rewrite the
            # cells of the one already there instead, keeping the formatting
            # given it by hand, but it addresses a cell by counting cell
            # elements: a sheet saved from LibreOffice, which collapses two
            # identical neighbours into one cell carrying
            # number-columns-repeated, then takes every value after the first
            # collapse one column to the left. The table is not ours to format
            # anyway — it is read from here and drawn elsewhere.
            #
            # The GlobalId would head the table with a column nobody reads on
            # a sheet, and it is IfcCsv's sort key unless it is left out.
            include_global_id=False,
            null=schedule.NULL,
            bool_true=schedule.YES,
            bool_false=schedule.NO,
            sort=schedule.SORT,
            formatting=schedule.FORMATTING,
        )
        sheet_style.apply(path, schedule.LAYOUT)

    def _add_schedule(self, path):
        """Registers the table among Bonsai's schedules, unless one already
        points at it — the file has just been overwritten in place, so a second
        document would only be a duplicate. True when one was added."""
        if any(os.path.samefile(existing, path) for existing in _schedule_paths() if os.path.exists(existing)):
            return False
        uri = tool.Ifc.get_uri(Path(path), use_relative_path=True)
        core_drawing.add_document(tool.Ifc, tool.Drawing, "SCHEDULE", uri=str(uri))
        return True


def schedules_dir():
    """Where the tables live: the project's schedules directory, beside the
    IFC."""
    return os.path.join(os.path.dirname(tool.Ifc.get_path()), SCHEDULES_DIR)


def schedule_path(label):
    """One file per storey, named after it, rewritten in place so the schedule
    registered in the IFC keeps pointing at the current numbers."""
    name = tool.Drawing.sanitise_filename(label).strip() or "piano"
    return os.path.join(schedules_dir(), f"{SCHEDULE_NAME} - {name}.ods")


def _schedule_paths():
    """Where each of Bonsai's schedules points, absolute."""
    documents = [d for d in tool.Ifc.get().by_type("IfcDocumentInformation") if d.Scope == "SCHEDULE"]
    return [uri for uri in (tool.Drawing.get_document_uri(d) for d in documents) if uri]


classes = (NumberSpaces, ExportSpacesSchedule)
