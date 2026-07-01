# Bonsai Salad -- ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Thin Blender wrapper: reads bpy context, calls ifc_dxf.core functions.

import os

import bpy
import numpy as np

from .core import export_drawing, find_drawings


# ---------------------------------------------------------------------------
# Blender / Bonsai context helpers
# ---------------------------------------------------------------------------

def _get_ifc():
    """Return the currently loaded ifcopenshell.file from Bonsai, or None."""
    try:
        from bonsai import tool
        return tool.Ifc.get()
    except Exception:
        return None


def _get_active_drawing():
    """Return the active IfcAnnotation drawing from Bonsai, or None."""
    try:
        from bonsai import tool
        item = tool.Drawing.get_active_drawing_item()
        if item is None:
            return None
        return tool.Ifc.get().by_id(item.ifc_definition_id)
    except Exception:
        return None


def _get_camera_obj(drawing):
    """Return the Blender camera object for the given drawing annotation."""
    try:
        from bonsai import tool
        return tool.Ifc.get_object(drawing)
    except Exception:
        return None


def _get_target_view(drawing):
    """Return the TargetView string for the given drawing annotation."""
    try:
        from bonsai import tool
        return tool.Drawing.get_drawing_target_view(drawing)
    except Exception:
        return "PLAN_VIEW"


def _open_file(path):
    """Open a file with the OS default application, cross-platform."""
    import sys
    import subprocess

    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Blender operator classes
# ---------------------------------------------------------------------------

class ExportDrawingToDxfOperator(bpy.types.Operator):
    """Export the active Bonsai drawing to DXF (ezdxf, pure Python)."""

    bl_idname = "bim.export_drawing_to_dxf"
    bl_label  = "Export Drawing to DXF"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    dxf_version: bpy.props.EnumProperty(
        name="DXF Version",
        items=[
            ("R2010", "R2010 (AC1024)", ""),
            ("R2013", "R2013 (AC1027)", ""),
            ("R2018", "R2018 (AC1032)", ""),
        ],
        default="R2010",
    )
    open_after_export: bpy.props.BoolProperty(
        name="Open After Export",
        description="Open the exported file with the system's default application",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _get_ifc() is not None and _get_active_drawing() is not None

    def _default_filepath(self, drawing):
        try:
            ifc_path = bpy.context.scene.BIMProperties.ifc_file or ""
        except Exception:
            ifc_path = ""
        raw_name = (getattr(drawing, "Name", None) or "drawing") if drawing else "drawing"
        safe_name = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in raw_name)
        if ifc_path:
            ifc_abs = bpy.path.abspath(ifc_path)
            drawings_dir = os.path.join(os.path.dirname(ifc_abs), "drawings")
            base_dir = drawings_dir if os.path.isdir(drawings_dir) else os.path.dirname(ifc_abs)
        else:
            base_dir = ""
        return os.path.join(base_dir, safe_name + ".dxf")

    def invoke(self, context, event):
        self.filepath = self._default_filepath(_get_active_drawing())
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "dxf_version")
        layout.prop(self, "open_after_export")

    def execute(self, context):
        ifc = _get_ifc()
        if ifc is None:
            self.report({"ERROR"}, "No IFC file loaded.")
            return {"CANCELLED"}

        drawing = _get_active_drawing()
        if drawing is None:
            self.report({"ERROR"}, "No active Bonsai drawing found.")
            return {"CANCELLED"}

        raw_path = self.filepath or self._default_filepath(drawing)
        output_path = bpy.path.abspath(raw_path)
        if not output_path.lower().endswith((".dxf", ".dwg")):
            output_path += ".dxf"

        # Read EPset_Drawing pset
        try:
            import ifcopenshell.util.element
            pset = ifcopenshell.util.element.get_psets(drawing).get("EPset_Drawing", {})
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read drawing pset: {exc}")
            return {"CANCELLED"}

        # Determine wall mode
        try:
            import shapely  # noqa: F401
            wall_mode = "shapely"
        except ImportError:
            wall_mode = "flat"

        # Determine template path
        tpl = getattr(getattr(context.scene, "ifc_dxf", None), "template_path", "") or ""
        if tpl:
            template_path = bpy.path.abspath(tpl)
        else:
            from . import get_template_path
            template_path = get_template_path() or None

        try:
            export_drawing(
                ifc, drawing, pset, output_path,
                wall_mode=wall_mode,
                template_path=template_path,
            )
        except Exception as exc:
            self.report({"ERROR"}, f"DXF export failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported to {output_path}")

        if self.open_after_export:
            _open_file(output_path)

        return {"FINISHED"}


class SelectDxfTemplateOperator(bpy.types.Operator):
    """Browse for a DXF template file, starting in the default template folder."""

    bl_idname  = "bim.select_dxf_template"
    bl_label   = "Select DXF Template"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.dxf;*.DXF", options={"HIDDEN"})

    def invoke(self, context, event):
        # Start the browser at the current template location (or the built-in default).
        current = bpy.path.abspath(context.scene.ifc_dxf.template_path or "")
        if not current or not os.path.isfile(current):
            from . import get_template_path
            current = get_template_path() or ""
        self.filepath = current
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.scene.ifc_dxf.template_path = self.filepath
        return {"FINISHED"}


class IfcDxfProperties(bpy.types.PropertyGroup):
    template_path: bpy.props.StringProperty(
        name="DXF Template",
        description="Path to the ifc_dxf_template_metric.dxf used as base for exports",
        subtype="FILE_PATH",
        default="",
    )


classes = [IfcDxfProperties, SelectDxfTemplateOperator, ExportDrawingToDxfOperator]
