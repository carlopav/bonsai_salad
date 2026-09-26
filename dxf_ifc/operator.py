# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Thin Blender wrapper: reads bpy context, calls dxf_ifc.core functions.

import collections

import bpy

from bonsai import tool

from .core import import_dxf_as_elements, import_dxf_as_representation, get_or_create_subcontext
from .core import mapping as layer_mapping


# ---------------------------------------------------------------------------
# Blender / Bonsai context helpers
# ---------------------------------------------------------------------------

def _get_selected_element():
    return tool.Ifc.get_entity(bpy.context.active_object)


def _create_annotation_object(context, name: str, object_type: str = "LINEWORK"):
    """Create a Blender object linked to a new IfcAnnotation. Returns (obj, element)."""
    import bonsai.core.geometry as core_geometry
    import bonsai.core.root as core_root

    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    context.scene.collection.objects.link(obj)
    element = core_root.assign_class(
        tool.Ifc,
        tool.Collector,
        tool.Root,
        obj=obj,
        ifc_class="IfcAnnotation",
        predefined_type=object_type,
        should_add_representation=False,
    )
    # Products need an ObjectPlacement before they carry a representation.
    core_geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
    return obj, element


def _create_mapped_object(context, name: str, ifc_class: str, predefined_type=None):
    """
    Create a Blender object linked to a new IFC element of *ifc_class*.

    Returns (obj, element), or (None, None) if the class cannot be created in
    this project's schema.
    """
    import bonsai.core.geometry as core_geometry
    import bonsai.core.root as core_root

    ifc = tool.Ifc.get()
    if not layer_mapping.is_valid_class(ifc, ifc_class):
        return None, None

    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    context.scene.collection.objects.link(obj)
    try:
        element = core_root.assign_class(
            tool.Ifc,
            tool.Collector,
            tool.Root,
            obj=obj,
            ifc_class=ifc_class,
            predefined_type=predefined_type or None,
            should_add_representation=False,
        )
    except Exception:
        # A class Bonsai will not assign (or a predefined type it rejects):
        # drop the half-made object rather than leave it in the scene.
        bpy.data.objects.remove(obj)
        return None, None
    # Products need an ObjectPlacement before they carry a representation.
    core_geometry.edit_object_placement(tool.Ifc, tool.Geometry, tool.Surveyor, obj=obj)
    return obj, element


def _place_in_spatial_tree(ifc, element) -> None:
    """
    Put *element* somewhere in the spatial tree if nothing else has.

    Spatial structure elements (IfcSpace and friends) are aggregated into their
    parent, ordinary elements are contained by it, so the two take different
    relationships.
    """
    import ifcopenshell.api
    import ifcopenshell.util.element

    if ifcopenshell.util.element.get_container(element):
        return
    if element.is_a("IfcSpatialStructureElement") or element.is_a("IfcSpatialElement"):
        parent = None
        for cls in ("IfcBuildingStorey", "IfcBuilding", "IfcSite", "IfcProject"):
            items = ifc.by_type(cls)
            if items:
                parent = items[0]
                break
        if parent is None or ifcopenshell.util.element.get_parent(element):
            return
        try:
            ifcopenshell.api.run("aggregate.assign_object", ifc,
                                 products=[element], relating_object=parent)
        except Exception:
            pass
        return
    container = _find_spatial_container(ifc)
    if container is None:
        return
    try:
        ifcopenshell.api.run("spatial.assign_container", ifc,
                             products=[element], relating_structure=container)
    except Exception:
        pass


def _reload_object_geometry(obj, representation) -> None:
    import bonsai.core.geometry as core_geometry

    core_geometry.switch_representation(tool.Ifc, tool.Geometry, obj=obj, representation=representation)


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


def _resolve_dxf(op):
    """
    The DXF *op* should read, or None after reporting why there isn't one.

    Blender leaves `filepath` holding the folder when the accept button is
    pressed without a file highlighted, and calling an operator bare from the
    console runs execute() without the browser opening at all. Neither should be
    a dead end, so a folder holding exactly one DXF is used.
    """
    import os
    from pathlib import Path

    candidate = bpy.path.abspath(op.filepath) if op.filepath else ""
    if candidate and os.path.isfile(candidate):
        return candidate

    directory = getattr(op, "directory", "") or ""
    filename = getattr(op, "filename", "") or ""
    if directory and filename:
        joined = os.path.join(bpy.path.abspath(directory), filename)
        if os.path.isfile(joined):
            return joined

    folder = candidate if candidate and os.path.isdir(candidate) else ""
    if not folder and directory:
        folder = bpy.path.abspath(directory)
    if folder and os.path.isdir(folder):
        found = sorted(p for p in Path(folder).iterdir()
                       if p.is_file() and p.suffix.lower() == ".dxf")
        if len(found) == 1:
            return str(found[0])
        if not found:
            op.report({"ERROR"}, f"No .dxf file in {Path(folder).name}. Select one.")
            return None
        names = ", ".join(p.name for p in found[:3])
        more = f" and {len(found) - 3} more" if len(found) > 3 else ""
        op.report({"ERROR"}, f"Select which DXF: {names}{more}")
        return None

    if not candidate:
        op.report({"ERROR"}, "No DXF chosen. Use the panel button, or pass "
                             "filepath= when calling this from Python.")
    else:
        op.report({"ERROR"}, f"Not a valid file: {candidate}")
    return None


def _resolve_mapping_path(mapping_filepath: str, dxf_filepath: str, scene=None):
    """
    Return (Path, where_it_came_from) for the mapping CSV, or (None, reason).

    A CSV sitting beside the drawing beats the panel's setting. The panel field
    is a default for drawings that have none of their own, and it is sticky:
    left pointing at the last drawing's CSV it would quietly classify a new
    drawing against another one's layer names, which match nothing and send
    every layer to the fallback class.
    """
    from pathlib import Path

    # Explicit argument, for scripted calls: this one really does win.
    if mapping_filepath:
        p = Path(bpy.path.abspath(mapping_filepath))
        return (p, "given") if p.is_file() else (None, "the given path is not a file")

    if dxf_filepath:
        dxf = Path(bpy.path.abspath(dxf_filepath))
        if dxf.is_dir():
            found = sorted(p for p in dxf.iterdir()
                           if p.is_file() and p.suffix.lower() == ".dxf")
            dxf = found[0] if len(found) == 1 else dxf
        for candidate in (dxf.with_suffix(".layers.csv"),
                          dxf.with_suffix(".csv"),
                          dxf.parent / "layer_mapping.csv"):
            if candidate.is_file():
                return candidate, "beside the DXF"

    if scene is not None:
        props = getattr(scene, "dxf_ifc", None)
        panel_path = getattr(props, "mapping_filepath", "") if props else ""
        if panel_path:
            p = Path(bpy.path.abspath(panel_path))
            if p.is_file():
                return p, "from the panel"

    shipped = Path(__file__).parent / "templates" / "layer_mapping.csv"
    if shipped.is_file():
        return shipped, "add-on template"
    return None, "no mapping CSV found"


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
        # Layers as the importer sees them, blocks expanded. Listing only the
        # layers of top-level entities offers layers that hold nothing but block
        # references, which import as nothing, and hides every layer that exists
        # only inside a block.
        _dxf_layers = [
            (name, f"{name}  ({count})", f"{count} entities")
            for name, count in layer_mapping.dxf_layers(path).items()
        ]
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

class ImportDxfAsRepresentationOperator(bpy.types.Operator, tool.Ifc.Operator):
    """Import a DXF file as IFC representation on the active element."""

    bl_idname = "bim.import_dxf_as_representation"
    bl_label = "Import DXF as Representation"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    # The browser fills these even when the accept button is pressed without a
    # file highlighted, which leaves filepath holding the folder on its own.
    directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    filename: bpy.props.StringProperty(options={"HIDDEN"})
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
            ("MAPPED", "Elements by layer", "Create one IFC element per DXF layer, classed by a mapping CSV"),
        ],
        default="ACTIVE",
    )
    # Deliberately not subtype="FILE_PATH": this operator already runs inside a
    # file browser, and Blender refuses to open a second one ("Cannot activate a
    # file selector dialog, one already open"). The browsable field lives on the
    # sidebar panel instead; this one exists so scripts can override it.
    mapping_filepath: bpy.props.StringProperty(
        name="Mapping CSV",
        description="Layer to IFC class mapping. Leave blank to use the panel's setting, "
                    "a CSV beside the DXF, or the template shipped with the add-on",
        default="",
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

    def draw(self, context):
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
        elif self.target_mode == "MAPPED":
            box.label(text="One element per DXF layer", icon="OUTLINER")
            resolved, origin = _resolve_mapping_path(
                self.mapping_filepath, self.filepath, context.scene)
            if resolved is None:
                box.label(text="No mapping CSV found", icon="ERROR")
                box.label(text="Set one in the Bonsai Salad panel")
            else:
                box.label(text=resolved.name, icon="FILE_TEXT")
                # Which file won matters: the panel setting is sticky, and a
                # previous drawing's CSV classifies nothing in this one.
                box.label(text=f"({origin})")
            box.label(text="Plan / Annotation / PLAN_VIEW", icon="SCENE_DATA")
        else:
            box.label(text="Plan / Annotation / PLAN_VIEW", icon="SCENE_DATA")

        layout.separator()

        # Options

    def invoke(self, context, event):
        global _subcontext_items

        ifc = tool.Ifc.get()
        element = _get_selected_element()

        if self.target_mode == "MAPPED":
            self.element_label = ""
            self.subcontext_label = ""
        elif element is None:
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

    def _execute(self, context):
        ifc = tool.Ifc.get()
        if ifc is None:
            self.report({"ERROR"}, "No IFC file loaded.")
            return {"CANCELLED"}

        from pathlib import Path
        filepath = _resolve_dxf(self)
        if filepath is None:
            return {"CANCELLED"}

        if self.target_mode == "MAPPED":
            return self._execute_mapped(context, ifc, filepath)

        if self.target_mode == "NEW":
            name = Path(filepath).stem
            try:
                obj, element = _create_annotation_object(context, name)
            except Exception as exc:
                self.report({"ERROR"}, f"Failed to create IfcAnnotation: {exc}")
                return {"CANCELLED"}

            if element is None:
                self.report({"ERROR"}, "Failed to create IfcAnnotation.")
                return {"CANCELLED"}

            # assign_class already containers non-drawing annotations when a default
            # container is set; fall back to any spatial element otherwise.
            import ifcopenshell.api
            import ifcopenshell.util.element

            if not tool.Root.is_drawing_annotation(element) and not ifcopenshell.util.element.get_container(element):
                container = _find_spatial_container(ifc)
                if container is not None:
                    ifcopenshell.api.run(
                        "spatial.assign_container", ifc,
                        products=[element], relating_structure=container,
                    )

            subcontext = get_or_create_subcontext(ifc)
        else:
            obj = context.active_object
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

        # Without this the IFC holds the geometry but the viewport still shows the old mesh.
        if obj is not None:
            try:
                _reload_object_geometry(obj, new_repr)
            except Exception as exc:
                self.report({"WARNING"}, f"Representation written but reload failed: {exc}")

        if self.target_mode == "NEW":
            # switch_representation may swap the object data-block, so re-fetch it.
            new_obj = tool.Ifc.get_object(element)
            if new_obj is not None:
                new_obj.select_set(True)
                context.view_layer.objects.active = new_obj

        n_items = len(new_repr.Items) if new_repr else 0
        self.report({"INFO"}, f"Imported {n_items} items into {getattr(element, 'Name', element)} — visible in 2D Drawing view")
        return {"FINISHED"}

    def _execute_mapped(self, context, ifc, filepath):
        """One IFC element per DXF layer, classed by the mapping CSV."""
        # filepath here is the resolved DXF; self.filepath may still be the
        # folder the browser was left in, which finds no CSV beside anything.
        mapping_path, origin = _resolve_mapping_path(
            self.mapping_filepath, filepath, context.scene)
        if mapping_path is None:
            self.report({"ERROR"}, f"No mapping CSV: {origin}. Generate one with the panel button.")
            return {"CANCELLED"}
        try:
            rules = layer_mapping.load_mapping(mapping_path)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read {mapping_path.name}: {exc}")
            return {"CANCELLED"}

        subcontext = get_or_create_subcontext(ifc)
        created, rejected = [], []

        def create_element(layer_name, ifc_class, predefined_type, name):
            obj, element = _create_mapped_object(context, name, ifc_class, predefined_type)
            if element is None:
                rejected.append((layer_name, ifc_class))
                return None
            _place_in_spatial_tree(ifc, element)
            created.append((layer_name, obj, element))
            return element

        source_block = self.source_block if self.source_mode == "BLOCK" and self.source_block != "NONE" else None
        source_layer = self.source_layer if self.source_mode == "LAYER" and self.source_layer != "NONE" else None

        try:
            results = import_dxf_as_elements(
                ifc, filepath, create_element,
                subcontext=subcontext,
                mapping_rules=rules,
                source_block=source_block,
                source_layer=source_layer,
            )
        except Exception as exc:
            # Objects already made for earlier layers would otherwise linger
            # without the geometry that justifies them.
            for _layer, obj, _el in created:
                try:
                    bpy.data.objects.remove(obj)
                except Exception:
                    pass
            self.report({"ERROR"}, f"DXF import failed: {exc}")
            return {"CANCELLED"}

        by_element = {el.id(): obj for _layer, obj, el in created}
        for _layer_name, element, representation in results:
            obj = by_element.get(element.id())
            if obj is None:
                continue
            try:
                _reload_object_geometry(obj, representation)
            except Exception as exc:
                self.report({"WARNING"}, f"{element.Name}: representation written but reload failed: {exc}")

        classes = collections.Counter(el.is_a() for _l, _o, el in created)
        summary = ", ".join(f"{n}x {c}" for c, n in classes.most_common())
        msg = (f"Created {len(results)} elements using {mapping_path.name} "
               f"({origin}): {summary}")
        if rejected:
            msg += f" — skipped {len(rejected)} ({', '.join(f'{l} as {c}' for l, c in rejected[:3])})"
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg + " — visible in 2D Drawing view")
        return {"FINISHED"}


class WriteDxfLayerMappingOperator(bpy.types.Operator):
    """Write a layer mapping CSV pre-filled with the layers found in a DXF."""

    bl_idname = "bim.write_dxf_layer_mapping"
    bl_label = "Generate Layer Mapping CSV"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    # The browser fills these even when the accept button is pressed without a
    # file highlighted, which leaves filepath holding the folder on its own.
    directory: bpy.props.StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    filename: bpy.props.StringProperty(options={"HIDDEN"})
    filter_glob: bpy.props.StringProperty(default="*.dxf;*.DXF", options={"HIDDEN"})

    def invoke(self, context, event):
        self.filepath = bpy.path.abspath("//")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from pathlib import Path

        filepath = _resolve_dxf(self)
        if filepath is None:
            return {"CANCELLED"}
        try:
            layers = layer_mapping.dxf_layers(filepath)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not read DXF: {exc}")
            return {"CANCELLED"}
        if not layers:
            self.report({"ERROR"}, "No layers with geometry found in that DXF.")
            return {"CANCELLED"}

        # Sits beside the DXF, where the importer looks for it by default.
        out = Path(filepath).with_suffix(".layers.csv")
        try:
            layer_mapping.write_mapping_template(
                out, layers, comment=f"seeded from {Path(filepath).name}",
                model=tool.Ifc.get())
        except Exception as exc:
            self.report({"ERROR"}, f"Could not write {out.name}: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Wrote {out.name} — {len(layers)} layers, edit it then import")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class DxfIfcProperties(bpy.types.PropertyGroup):
    # Browsable here rather than on the import operator: that one runs inside a
    # file browser, and Blender allows only one open at a time.
    mapping_filepath: bpy.props.StringProperty(
        name="Layer Mapping",
        description="CSV mapping DXF layers to IFC classes, used by the "
                    "'Elements by layer' import. Leave blank to use a CSV sitting "
                    "beside the DXF, or the template shipped with the add-on",
        subtype="FILE_PATH",
        default="",
    )


classes = [DxfIfcProperties, ImportDxfAsRepresentationOperator,
           WriteDxfLayerMappingOperator]
