# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Thin Blender wrapper: reads bpy context, calls dxf_ifc.core functions.

import bpy

from bonsai import tool

from .core import import_dxf_as_representation, get_or_create_subcontext


# ---------------------------------------------------------------------------
# Blender / Bonsai context helpers
# ---------------------------------------------------------------------------

def _get_selected_element():
    return tool.Ifc.get_entity(bpy.context.active_object)


def _get_element_subcontext(ifc, element):
    """Return the best subcontext to overwrite: Plan/Annotation preferred, else first found."""
    try:
        reprs = element.Representation.Representations if element.Representation else []
        # Prefer Plan/Annotation
        for r in reprs:
            ctx = r.ContextOfItems
            if (getattr(ctx, "ContextIdentifier", "") == "Annotation"
                    and getattr(ctx, "TargetView", "") in ("PLAN_VIEW", "REFLECTED_PLAN_VIEW")):
                return ctx
        # Fallback: first available
        if reprs:
            return reprs[0].ContextOfItems
    except Exception:
        pass
    return None


def _find_spatial_container(ifc):
    """Return the highest available spatial structure element in the model."""
    for cls in ("IfcBuildingStorey", "IfcBuilding", "IfcSite"):
        items = ifc.by_type(cls)
        if items:
            return items[0]
    return None


# ---------------------------------------------------------------------------
# DXF source content cache — populated by ScanDxfSourceOperator.
# ---------------------------------------------------------------------------

_dxf_blocks: list[tuple] = []
_dxf_layers: list[tuple] = []


def _block_enum_items(self, context):
    return _dxf_blocks or [("NONE", "— scan a DXF file first —", "")]


def _layer_enum_items(self, context):
    return _dxf_layers or [("NONE", "— scan a DXF file first —", "")]


def _scan_dxf_source(filepath: str) -> None:
    """Populate _dxf_blocks and _dxf_layers from *filepath*. Silent on errors."""
    global _dxf_blocks, _dxf_layers
    import os
    path = bpy.path.abspath(filepath)
    if not os.path.isfile(path):
        return
    try:
        import ezdxf
        doc = ezdxf.readfile(path)
        _dxf_blocks = [
            (b.name, b.name, "")
            for b in doc.blocks
            if not b.name.startswith("*")
        ]
        seen: set = set()
        layers: list = []
        for e in doc.modelspace():
            ln = e.dxf.layer
            if ln not in seen:
                layers.append((ln, ln, ""))
                seen.add(ln)
        _dxf_layers = layers
    except Exception:
        pass


def _on_source_mode_update(self, _context) -> None:
    if self.source_mode in ("BLOCK", "LAYER"):
        _scan_dxf_source(self.filepath)


# ---------------------------------------------------------------------------
# Subcontext enum cache — populated once in invoke(), read by the enum callback.
# ---------------------------------------------------------------------------

_subcontext_items: list[tuple] = []


def _subcontext_enum_items(self, context):
    return _subcontext_items or [("NONE", "— no contexts found —", "")]


def _build_subcontext_items(ifc) -> list[tuple]:
    items = []
    seen = set()

    for ctx in ifc.by_type("IfcGeometricRepresentationContext"):
        if ctx.is_a("IfcGeometricRepresentationSubContext"):
            continue
        label = getattr(ctx, "ContextType", "") or ctx.is_a()
        key = str(ctx.id())
        if key not in seen:
            items.append((key, label, ""))
            seen.add(key)

    for sub in ifc.by_type("IfcGeometricRepresentationSubContext"):
        parent = sub.ParentContext
        ctx_type = getattr(parent, "ContextType", "") or ""
        ctx_id = getattr(sub, "ContextIdentifier", "") or ""
        target = getattr(sub, "TargetView", "") or ""
        parts = [p for p in (ctx_type, ctx_id, target) if p]
        label = "/".join(parts)
        key = str(sub.id())
        if key not in seen:
            items.append((key, label, ""))
            seen.add(key)

    return items


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class ImportDxfAsRepresentationOperator(bpy.types.Operator):
    """Import a DXF file as IFC representation on the active element."""

    bl_idname = "bim.import_dxf_as_representation"
    bl_label = "Import DXF as Representation"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.dxf;*.DXF", options={"HIDDEN"})

    # Labels cached at invoke time — stored as registered RNA props (no underscore).
    element_label: bpy.props.StringProperty(options={"HIDDEN"})
    subcontext_label: bpy.props.StringProperty(options={"HIDDEN"})

    # Source
    source_mode: bpy.props.EnumProperty(
        name="Source",
        items=[
            ("FULL",  "All",   "Import all entities from the modelspace"),
            ("BLOCK", "Block", "Import entities from a specific block definition"),
            ("LAYER", "Layer", "Import only entities on a specific layer"),
        ],
        default="FULL",
        update=_on_source_mode_update,
    )
    source_block: bpy.props.EnumProperty(name="Block", items=_block_enum_items)
    source_layer: bpy.props.EnumProperty(name="Layer", items=_layer_enum_items)

    # Destination
    target_mode: bpy.props.EnumProperty(
        name="Target",
        items=[
            ("ACTIVE", "Active element", "Import on the currently selected IFC element"),
            ("NEW",    "New annotation", "Create a new IfcAnnotation (Plan/Annotation/PLAN_VIEW)"),
        ],
        default="ACTIVE",
    )
    import_mode: bpy.props.EnumProperty(
        name="Mode",
        items=[
            ("OVERWRITE", "Overwrite active context", "Replace the existing representation in the element's current subcontext"),
            ("CREATE",    "Use context",               "Assign the representation to a chosen subcontext"),
        ],
        default="OVERWRITE",
    )
    target_subcontext: bpy.props.EnumProperty(
        name="Context",
        items=_subcontext_enum_items,
    )

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def draw(self, _context):
        layout = self.layout

        # Source
        box = layout.box()
        box.label(text="Source", icon="IMPORT")
        row = box.row(align=True)
        row.prop_enum(self, "source_mode", "FULL")
        row.prop_enum(self, "source_mode", "BLOCK")
        row.prop_enum(self, "source_mode", "LAYER")
        if self.source_mode == "BLOCK":
            box.prop(self, "source_block", text="")
        elif self.source_mode == "LAYER":
            box.prop(self, "source_layer", text="")

        # Destination
        box = layout.box()
        box.label(text="Destination", icon="EXPORT")
        box.prop(self, "target_mode", text="")

        if self.target_mode == "ACTIVE":
            el = getattr(self, "element_label", "")
            if el:
                box.label(text=el, icon="OBJECT_DATA")
            box.prop(self, "import_mode", text="")
            if self.import_mode == "OVERWRITE":
                sub_label = getattr(self, "subcontext_label", "")
                if sub_label.startswith("!"):
                    box.label(text=sub_label[1:], icon="ERROR")
                else:
                    box.label(text=sub_label, icon="SCENE_DATA")
            else:
                box.prop(self, "target_subcontext", text="")
        else:
            box.label(text="Plan / Annotation / PLAN_VIEW", icon="SCENE_DATA")

        layout.separator()

        # Options

    def invoke(self, context, event):
        global _subcontext_items

        ifc = tool.Ifc.get()
        element = _get_selected_element()

        if element is None:
            self.target_mode = "NEW"
            self.element_label = ""
            self.subcontext_label = ""
        else:
            self.target_mode = "ACTIVE"
            self.element_label = getattr(element, "Name", None) or element.is_a()
            subcontext = _get_element_subcontext(ifc, element)
            if subcontext is not None:
                ctx_id = getattr(subcontext, "ContextIdentifier", "") or ""
                target = getattr(subcontext, "TargetView", "") or ""
                self.subcontext_label = f"{ctx_id} / {target}" if target else ctx_id
                self.import_mode = "OVERWRITE"
            else:
                self.subcontext_label = "!No representation found — choose a context below."
                self.import_mode = "CREATE"

        _subcontext_items = _build_subcontext_items(ifc) if ifc else []

        self.filepath = bpy.path.abspath("//")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        ifc = tool.Ifc.get()
        if ifc is None:
            self.report({"ERROR"}, "No IFC file loaded.")
            return {"CANCELLED"}

        import os
        from pathlib import Path
        filepath = bpy.path.abspath(self.filepath)
        if not os.path.isfile(filepath):
            self.report({"ERROR"}, f"Not a valid file: {filepath}")
            return {"CANCELLED"}

        if self.target_mode == "NEW":
            import ifcopenshell.api

            name = Path(filepath).stem
            try:
                element = ifcopenshell.api.run(
                    "root.create_entity", ifc, ifc_class="IfcAnnotation", name=name
                )
            except Exception as exc:
                self.report({"ERROR"}, f"Failed to create IfcAnnotation: {exc}")
                return {"CANCELLED"}

            container = _find_spatial_container(ifc)
            if container is not None:
                ifcopenshell.api.run(
                    "spatial.assign_container", ifc,
                    products=[element], relating_structure=container,
                )

            subcontext = get_or_create_subcontext(ifc)
        else:
            element = _get_selected_element()
            if element is None:
                self.report({"ERROR"}, "No active IFC element selected.")
                return {"CANCELLED"}

            if self.import_mode == "OVERWRITE":
                subcontext = _get_element_subcontext(ifc, element)
                if subcontext is None:
                    self.report({"ERROR"}, "Element has no existing representation to overwrite.")
                    return {"CANCELLED"}
            else:
                try:
                    subcontext = ifc.by_id(int(self.target_subcontext))
                except Exception:
                    self.report({"ERROR"}, "Invalid subcontext selected.")
                    return {"CANCELLED"}

        source_block = self.source_block if self.source_mode == "BLOCK" and self.source_block != "NONE" else None
        source_layer = self.source_layer if self.source_mode == "LAYER" and self.source_layer != "NONE" else None

        try:
            new_repr = import_dxf_as_representation(
                ifc,
                element,
                filepath,
                subcontext=subcontext,
                source_block=source_block,
                source_layer=source_layer,
            )
        except Exception as exc:
            self.report({"ERROR"}, f"DXF import failed: {exc}")
            return {"CANCELLED"}

        try:
            ifc_path = bpy.path.abspath(bpy.context.scene.BIMProperties.ifc_file)
            ifc.write(ifc_path)
        except Exception:
            pass

        n_items = len(new_repr.Items) if new_repr else 0
        self.report({"INFO"}, f"Imported {n_items} items into {getattr(element, 'Name', element)} — visible in 2D Drawing view")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class DxfIfcProperties(bpy.types.PropertyGroup):
    pass


classes = [DxfIfcProperties, ImportDxfAsRepresentationOperator]
