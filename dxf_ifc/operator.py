# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Thin Blender wrapper: reads bpy context, calls dxf_ifc.core functions.

import bpy

from .core import import_dxf_as_representation, get_or_create_subcontext


# ---------------------------------------------------------------------------
# Blender / Bonsai context helpers
# ---------------------------------------------------------------------------

def _get_ifc():
    try:
        from bonsai import tool
        return tool.Ifc.get()
    except Exception:
        return None


def _get_selected_element():
    """Return the IFC element for the active selected object, or None."""
    try:
        from bonsai import tool
        obj = bpy.context.active_object
        if obj is None:
            return None
        return tool.Ifc.get_entity(obj)
    except Exception:
        return None


def _get_element_subcontext(ifc, element):
    """Return the subcontext from the element's first existing representation, or None."""
    try:
        if element.Representation:
            for r in element.Representation.Representations:
                return r.ContextOfItems
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class ImportDxfAsRepresentationOperator(bpy.types.Operator):
    """Import a DXF file as IFC Annotation representation on the active element."""

    bl_idname = "bim.import_dxf_as_representation"
    bl_label = "Import DXF as Representation"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.dxf;*.DXF", options={"HIDDEN"})
    _element_label: bpy.props.StringProperty(options={"HIDDEN"})
    _subcontext_label: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _get_ifc() is not None and _get_selected_element() is not None

    def draw(self, context):
        layout = self.layout
        if self._element_label:
            layout.label(text=self._element_label, icon="OBJECT_DATA")
        if self._subcontext_label:
            icon = "ERROR" if self._subcontext_label.startswith("!") else "SCENE_DATA"
            layout.label(text=self._subcontext_label.lstrip("!"), icon=icon)
        layout.prop(context.scene.dxf_ifc, "write_pset")

    def invoke(self, context, event):
        ifc = _get_ifc()
        element = _get_selected_element()
        self._element_label = getattr(element, "Name", None) or (element.is_a() if element else "")
        subcontext = _get_element_subcontext(ifc, element) if (ifc and element) else None
        if subcontext is not None:
            ctx_id = getattr(subcontext, "ContextIdentifier", "") or ""
            target = getattr(subcontext, "TargetView", "") or ""
            self._subcontext_label = f"{ctx_id} / {target}" if target else ctx_id
        else:
            self._subcontext_label = "!No representation found."
        self.filepath = bpy.path.abspath("//")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ifc = _get_ifc()
        if ifc is None:
            self.report({"ERROR"}, "No IFC file loaded.")
            return {"CANCELLED"}

        element = _get_selected_element()
        if element is None:
            self.report({"ERROR"}, "No active IFC element selected.")
            return {"CANCELLED"}

        props = context.scene.dxf_ifc
        write_pset = props.write_pset

        subcontext = _get_element_subcontext(ifc, element)

        try:
            import_dxf_as_representation(
                ifc,
                element,
                self.filepath,
                subcontext=subcontext,
                write_pset=write_pset,
            )
        except Exception as exc:
            self.report({"ERROR"}, f"DXF import failed: {exc}")
            return {"CANCELLED"}

        # Save the IFC file to persist the new representation
        try:
            ifc_path = bpy.path.abspath(bpy.context.scene.BIMProperties.ifc_file)
            ifc.write(ifc_path)
        except Exception:
            pass

        self.report({"INFO"}, f"Imported {self.filepath} → {getattr(element, 'Name', element)}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class DxfIfcProperties(bpy.types.PropertyGroup):
    write_pset: bpy.props.BoolProperty(
        name="Write Pset_DXFSource",
        description="Attach DXF style metadata as a property set on the element",
        default=False,
    )


classes = [DxfIfcProperties, ImportDxfAsRepresentationOperator]
