# Bonsai Salad — mep tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
#
# Thin Blender wrapper: reads bpy context, calls mep.core functions.

import bpy
import numpy as np
import ifcopenshell.api as api
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.system
import ifcopenshell.util.unit
from bonsai import tool
from bonsai.bim.ifc import IfcStore


class IfcTransactionMixin:
    """Route execute() through Bonsai's IFC transaction (CTRL+Z undoes the
    IFC edits too) when a model is loaded; subclasses implement _execute()."""

    def execute(self, context):
        if tool.Ifc.get() is not None:
            return IfcStore.execute_ifc_operator(self, context)
        return self._execute(context)


def sync_ifc_products(elements) -> int:
    """Materialize freshly written IFC elements as linked Blender objects
    without reloading the project (Bonsai's append-from-library pattern:
    a partial IfcImporter run on the already loaded file). Returns the
    number of objects created."""
    import logging
    from bonsai.bim import import_ifc

    settings = import_ifc.IfcImportSettings.factory(
        bpy.context, IfcStore.path, logging.getLogger("ImportIFC"))
    importer = import_ifc.IfcImporter(settings)
    importer.file = tool.Ifc.get()
    importer.has_existing_project = True  # reuse objects already loaded
    importer.process_context_filter()
    importer.material_creator.load_existing_materials()
    importer.create_generic_elements(set(elements))
    importer.place_objects_in_collections()
    return len([o for o in importer.added_data.values()
                if isinstance(o, bpy.types.Object)])


def _catalog_system_items(self, context):
    try:
        from .catalog import available_systems
        return [(name, name, "") for name in available_systems()]
    except Exception:
        return [("NONE", "No catalog found", "")]


class CheckMepDataOperator(bpy.types.Operator):
    """Load the norm tables (UNI EN 12056-2:2001, UNI EN 1401-1:2019+A1:2023)
    and the selected catalog, report what is available"""

    bl_idname = "bim.mep_check_data"
    bl_label = "Check MEP Data"
    bl_options = {"REGISTER"}

    def execute(self, context):
        from .catalog import load_system
        from .core import load_en12056, load_en1401

        props = context.scene.mep
        try:
            en12056 = load_en12056()
            en1401 = load_en1401()
            catalog = load_system(props.catalog_system)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"EN 12056-2: {len(en12056.apparecchi)} apparecchi, "
            f"{len(en12056.diramazioni)} righe diramazioni, "
            f"{len(en12056.colonne_primaria)} colonne, "
            f"{sum(len(v) for v in en12056.collettori.values())} capacità collettori. "
            f"EN 1401-1: {len(en1401)} diametri. "
            f"Catalogo {catalog.name}: {len(catalog.tubi)} tubi, "
            f"{len(catalog.raccordi)} raccordi.",
        )
        return {"FINISHED"}


# -- skeleton mesh bridge (spec §8: the tracciato is a plain edges-only mesh) --

APPLIANCE_ATTR = "mep_apparecchio"
OUTFALL_ATTR = "mep_recapito"        # boolean vertex attr on the tracciato mesh
OUTFALL_OBJ_ATTR = "mep_recapito_tipo"  # object custom prop on the receptor


def skeleton_to_mesh(skeleton, name="MEP Tracciato"):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(skeleton.verts, skeleton.edges, [])
    appliances = mesh.attributes.new(APPLIANCE_ATTR, "STRING", "POINT")
    for i, key in skeleton.appliances.items():
        # Blender 5.x string attributes take bytes; older versions take str.
        try:
            appliances.data[i].value = key.encode()
        except TypeError:
            appliances.data[i].value = key
    outfall = mesh.attributes.new(OUTFALL_ATTR, "BOOLEAN", "POINT")
    outfall.data[skeleton.outfall].value = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def mesh_to_skeleton(obj):
    from .core import Skeleton

    if obj.mode == "EDIT":
        obj.update_from_editmode()
    mesh = obj.data
    matrix = obj.matrix_world
    skeleton = Skeleton(
        verts=[tuple(matrix @ v.co) for v in mesh.vertices],
        edges=[tuple(e.vertices) for e in mesh.edges],
    )
    if APPLIANCE_ATTR in mesh.attributes:
        for i, item in enumerate(mesh.attributes[APPLIANCE_ATTR].data):
            value = item.value
            if isinstance(value, bytes):
                value = value.decode()
            if value:
                skeleton.appliances[i] = value
    if OUTFALL_ATTR in mesh.attributes:
        for i, item in enumerate(mesh.attributes[OUTFALL_ATTR].data):
            if item.value:
                skeleton.outfall = i
                break
    return skeleton


def selection_systems_summary(context):
    """(IFC elements in selection, {system label: count}, count without system).
    Drawn in the panel so the user sees existing memberships before generating."""
    elements = 0
    summary: dict[str, int] = {}
    without = 0
    for obj in context.selected_objects[:100]:
        entity = tool.Ifc.get_entity(obj)
        if entity is None or not entity.is_a("IfcProduct"):
            continue
        elements += 1
        systems = ifcopenshell.util.system.get_element_systems(entity)
        if not systems:
            without += 1
        for system in systems:
            label = system.Name or system.is_a()
            if system.is_a("IfcDistributionCircuit"):
                label += " (circuito)"
            summary[label] = summary.get(label, 0) + 1
    return elements, summary, without


def _is_tracciato(obj):
    return (obj is not None and obj.type == "MESH"
            and OUTFALL_ATTR in obj.data.attributes)


def _port_frame(ifc, entity, flow_direction="SOURCE"):
    """(world position in meters, local Z direction) of the element's port in
    that flow direction, or None. Port Z = outgoing connection direction."""
    ports = [p for p in ifcopenshell.util.system.get_ports(entity)
             if p.FlowDirection in (flow_direction, "SOURCEANDSINK")]
    if not ports:
        return None
    scale = ifcopenshell.util.unit.calculate_unit_scale(ifc)
    matrix = ifcopenshell.util.placement.get_local_placement(ports[0].ObjectPlacement)
    return (tuple(float(c) * scale for c in matrix[:3, 3]),
            tuple(float(c) for c in matrix[:3, 2]))


def _edit_pset(ifc, product, name, properties):
    pset_data = ifcopenshell.util.element.get_pset(product, name)
    pset = ifc.by_id(pset_data["id"]) if pset_data else api.run(
        "pset.add_pset", ifc, product=product, name=name)
    api.run("pset.edit_pset", ifc, pset=pset, properties=properties)


def _ensure_instance_port(ifc, entity, position_m,
                          flow_direction="SOURCE", name="Scarico") -> bool:
    """Port directly on a typeless element, at the given world position (m).
    Typed elements never get ports here: their template ports are authored on
    the type by hand and mirrored by _sync_element_ports."""
    if any(p.FlowDirection in (flow_direction, "SOURCEANDSINK")
           for p in ifcopenshell.util.system.get_ports(entity)):
        return False
    port = api.run("system.add_port", ifc, element=entity)
    port.FlowDirection = flow_direction
    port.Name = name
    matrix = np.eye(4)
    matrix[:3, 3] = position_m
    api.run("geometry.edit_object_placement", ifc, product=port, matrix=matrix)
    return True


def _sync_element_ports(ifc, entity) -> tuple[int, int, int]:
    """Mirror the type's template ports 1:1 onto the occurrence, so the
    instance ends up with exactly as many ports as its type: create missing,
    re-place drifted, remove surplus (connected surplus ports are kept).
    The type is never modified here. Returns (created, moved, removed)."""
    type_entity = ifcopenshell.util.element.get_type(entity)
    if type_entity is None:
        return (0, 0, 0)
    template_ports = ifcopenshell.util.system.get_ports(type_entity)

    # Pair instance ports to templates: by name first, then by flow direction.
    remaining = list(ifcopenshell.util.system.get_ports(entity))
    pairs = []
    for template in template_ports:
        port = next((p for p in remaining if p.Name == template.Name), None)
        if port is None:
            port = next((p for p in remaining
                         if p.FlowDirection == template.FlowDirection), None)
        if port is not None:
            remaining.remove(port)
        pairs.append((template, port))

    created = moved = removed = 0
    for port in remaining:  # surplus beyond the type's set
        if port.ConnectedTo or port.ConnectedFrom:
            continue  # never break an existing connection silently
        api.run("root.remove_product", ifc, product=port)
        removed += 1

    scale = ifcopenshell.util.unit.calculate_unit_scale(ifc)
    element_matrix = ifcopenshell.util.placement.get_local_placement(
        entity.ObjectPlacement)
    for template, port in pairs:
        offset = ifcopenshell.util.placement.get_local_placement(
            template.ObjectPlacement)
        expected = np.array(element_matrix) @ np.array(offset)
        expected[:3, 3] *= scale  # to meters for edit_object_placement
        if port is None:
            port = api.run("system.add_port", ifc, element=entity)
            created += 1
        else:
            current = ifcopenshell.util.placement.get_local_placement(
                port.ObjectPlacement)
            if (port.Name == template.Name
                    and port.FlowDirection == template.FlowDirection
                    and np.allclose(np.array(current)[:3, 3] * scale,
                                    expected[:3, 3], atol=1e-4)):
                continue
            moved += 1
        port.FlowDirection = template.FlowDirection
        port.Name = template.Name
        api.run("geometry.edit_object_placement", ifc, product=port, matrix=expected)
    return (created, moved, removed)


def _mark_ports(ifc, obj, entity, flow_direction, port_name):
    """Port bookkeeping shared by the Segna operators. Returns (ports touched,
    type name if its template port is missing)."""
    if ifcopenshell.util.element.get_type(entity) is None:
        touched = _ensure_instance_port(
            ifc, entity, tuple(obj.matrix_world.translation),
            flow_direction, port_name)
        return int(touched), None
    created, moved, _ = _sync_element_ports(ifc, entity)
    missing = None
    if _port_frame(ifc, entity, flow_direction) is None:
        type_entity = ifcopenshell.util.element.get_type(entity)
        missing = type_entity.Name or type_entity.GlobalId
    return created + bool(moved), missing


def _appliance_items(self, context):
    try:
        from .core import load_en12056
        return [(k, k, "UNI EN 12056-2:2001, prospetto 2")
                for k in sorted(load_en12056().apparecchi)]
    except Exception:
        return [("NONE", "Tabelle non disponibili", "")]


class MarkTerminalOperator(IfcTransactionMixin, bpy.types.Operator):
    """Segna gli oggetti selezionati come utilizzatori: apparecchio nel pset
    EN12056_Utilizzatore (DU da UNI EN 12056-2:2001, prospetto 2) e porta di
    scarico IfcDistributionPort SOURCE sull'elemento (all'origine dell'oggetto)"""

    bl_idname = "bim.mep_mark_terminal"
    bl_label = "Segna Utilizzatore"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def _execute(self, context):
        from .core import derive_appliance

        key = context.scene.mep.apparecchio
        ifc = tool.Ifc.get()
        ports = 0
        no_template = []
        for obj in context.selected_objects:
            obj[APPLIANCE_ATTR] = key
            entity = tool.Ifc.get_entity(obj)
            if ifc is None or entity is None:
                continue
            # Appliance kind and template port belong to the type; the
            # occurrence gets mirrored ports (never the template itself).
            # The custom pset is an override: skip it when the standard data
            # (PredefinedType, CisternCapacity) already says the same.
            type_entity = ifcopenshell.util.element.get_type(entity)
            if derive_appliance(entity) != key:
                _edit_pset(ifc, type_entity or entity, "EN12056_Utilizzatore",
                           {"Apparecchio": key})
            touched, missing = _mark_ports(ifc, obj, entity, "SOURCE", "Scarico")
            ports += touched
            if missing:
                no_template.append(missing)
        if no_template:
            self.report({"WARNING"},
                        "Type senza porta SOURCE (aggiungerla al type): "
                        + ", ".join(sorted(set(no_template))))
        self.report({"INFO"}, f"{len(context.selected_objects)} utilizzatori: {key} "
                              f"({ports} porte sincronizzate)")
        return {"FINISHED"}


class MarkOutfallOperator(IfcTransactionMixin, bpy.types.Operator):
    """Segna l'oggetto attivo come recapito (punto di consegna): tipo di
    scarico nel pset EN12056_Recapito (D.Lgs 152/2006, Parte III) e porta
    IfcDistributionPort SINK "Consegna" sull'elemento"""

    bl_idname = "bim.mep_mark_outfall"
    bl_label = "Segna Recapito"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def _execute(self, context):
        obj = context.active_object
        tipo = context.scene.mep.tipo_recapito
        obj[OUTFALL_OBJ_ATTR] = tipo
        obj.pop(APPLIANCE_ATTR, None)  # a receptor is not an appliance
        ifc = tool.Ifc.get()
        entity = tool.Ifc.get_entity(obj)
        if ifc is not None and entity is not None:
            type_entity = ifcopenshell.util.element.get_type(entity)
            _edit_pset(ifc, type_entity or entity, "EN12056_Recapito",
                       {"TipoRecapito": tipo})
            _, missing = _mark_ports(ifc, obj, entity, "SINK", "Consegna")
            if missing:
                self.report({"WARNING"}, "Il type non ha una porta SINK: "
                                         f"aggiungerla al type ({missing}).")
        self.report({"INFO"}, f"Recapito: {obj.name} ({tipo}).")
        return {"FINISHED"}


class SyncPortsOperator(IfcTransactionMixin, bpy.types.Operator):
    """Riallinea le porte degli elementi selezionati alle porte template del
    loro type (IfcRelNests): crea le mancanti, riposiziona le spostate, rimuove
    le eccedenti non connesse — ogni istanza ha esattamente le porte del type;
    i type non vengono mai modificati"""

    bl_idname = "bim.mep_sync_ports"
    bl_label = "Sincronizza Porte"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) and tool.Ifc.get() is not None

    def _execute(self, context):
        ifc = tool.Ifc.get()
        created = moved = removed = 0
        for obj in context.selected_objects:
            entity = tool.Ifc.get_entity(obj)
            if entity is not None:
                c, m, r = _sync_element_ports(ifc, entity)
                created += c
                moved += m
                removed += r
        self.report({"INFO"}, f"Porte: {created} create, {moved} riposizionate, "
                              f"{removed} rimosse.")
        return {"FINISHED"}


class GenerateTracciatoOperator(bpy.types.Operator):
    """Crea lo scheletro della linea (mesh di soli edges, giunti saldati):
    utilizzatori = oggetti selezionati segnati, recapito = oggetto attivo,
    colonna sul cursore 3D — proposta ortogonale, poi editabile in edit mode"""

    bl_idname = "bim.mep_generate_tracciato"
    bl_label = "Genera Tracciato"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        from .core import orthogonal_skeleton

        ifc = tool.Ifc.get()
        terminals = []
        for obj in context.selected_objects:
            if APPLIANCE_ATTR not in obj or obj is context.active_object:
                continue
            # Start the branch from the element's SOURCE port when it exists,
            # leaving along the port Z (the conventional connection direction).
            frame = None
            entity = tool.Ifc.get_entity(obj)
            if ifc is not None and entity is not None:
                frame = _port_frame(ifc, entity, "SOURCE")
            if frame:
                terminals.append((obj[APPLIANCE_ATTR], frame[0], frame[1]))
            else:
                terminals.append(
                    (obj[APPLIANCE_ATTR], tuple(obj.matrix_world.translation)))
        recapito = context.active_object
        if not terminals or recapito is None or APPLIANCE_ATTR in recapito:
            self.report({"ERROR"}, "Seleziona utilizzatori segnati + recapito attivo "
                                   "(oggetto segnato come recapito).")
            return {"CANCELLED"}

        # Deliver to the receptor's SINK port, arriving against its port Z.
        outfall_frame = None
        entity = tool.Ifc.get_entity(recapito)
        if ifc is not None and entity is not None:
            outfall_frame = _port_frame(ifc, entity, "SINK")
        cursor = context.scene.cursor.location
        skeleton = orthogonal_skeleton(
            terminals, stack_xy=(cursor.x, cursor.y),
            outfall=(outfall_frame[0] if outfall_frame
                     else tuple(recapito.matrix_world.translation)),
            outfall_direction=outfall_frame[1] if outfall_frame else None,
        )
        obj = skeleton_to_mesh(skeleton, name=f"Tracciato {context.scene.mep.linea}")
        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        self.report({"INFO"}, f"Tracciato creato: {len(skeleton.edges)} tratti. "
                              "Modificalo in edit mode, poi valida.")
        return {"FINISHED"}


class ApplySlopesOperator(bpy.types.Operator):
    """Ricalcola le quote z dei giunti lungo i tratti orizzontali con la
    pendenza di progetto (terminali fissi, colonne a lunghezza invariata) —
    UNI EN 12056-2:2001, prospetto 5 e prospetti B.1/B.2"""

    bl_idname = "bim.mep_apply_slopes"
    bl_label = "Applica Pendenze"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_tracciato(context.active_object)

    def execute(self, context):
        from .core import apply_slopes, validate_skeleton

        obj = context.active_object
        skeleton = mesh_to_skeleton(obj)
        errors = validate_skeleton(skeleton)
        if errors:
            self.report({"ERROR"}, "; ".join(errors))
            return {"CANCELLED"}
        sloped = apply_slopes(skeleton)
        inverse = obj.matrix_world.inverted()
        from mathutils import Vector
        for vertex, position in zip(obj.data.vertices, sloped.verts):
            vertex.co = inverse @ Vector(position)
        obj.data.update()
        self.report({"INFO"}, "Pendenze applicate.")
        return {"FINISHED"}


class ValidateTracciatoOperator(bpy.types.Operator):
    """Valida lo scheletro attivo: albero con recapito unico, niente anelli,
    vertici doppi, foglie con apparecchio — UNI EN 12056-2:2001"""

    bl_idname = "bim.mep_validate_tracciato"
    bl_label = "Valida Tracciato"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _is_tracciato(context.active_object)

    def execute(self, context):
        from .core import validate_skeleton

        errors = validate_skeleton(mesh_to_skeleton(context.active_object))
        if errors:
            for error in errors:
                self.report({"WARNING"}, error)
            return {"CANCELLED"}
        self.report({"INFO"}, "Tracciato valido.")
        return {"FINISHED"}


def _purge_circuit_objects(ifc, linea: str) -> None:
    """Delete the Blender objects of a line about to be regenerated (their IFC
    entities are removed by write_network right after)."""
    circuit = next((c for c in ifc.by_type("IfcDistributionCircuit")
                    if c.Name == linea), None)
    if circuit is None:
        return
    for rel in circuit.IsGroupedBy:
        for product in list(rel.RelatedObjects):
            if not (product.is_a("IfcPipeSegment") or product.is_a("IfcPipeFitting")):
                continue
            obj = tool.Ifc.get_object(product)
            if obj is not None:
                tool.Ifc.unlink(element=product)
                bpy.data.objects.remove(obj)


class GeneratePipesOperator(IfcTransactionMixin, bpy.types.Operator):
    """Dimensiona la linea (UNI EN 12056-2:2001) e genera IfcPipeSegment e
    IfcPipeFitting con porte connesse nell'IfcDistributionCircuit del modello
    caricato (rigenerando la linea se esiste); senza modello scrive un file
    IFC separato"""

    bl_idname = "bim.mep_generate_pipes"
    bl_label = "Genera Tubazioni"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _is_tracciato(context.active_object)

    def _route(self, context):
        from .catalog import load_system
        from .core import load_en12056, route_along, size_network, to_network

        props = context.scene.mep
        skeleton = mesh_to_skeleton(context.active_object)
        catalog = load_system(props.catalog_system)
        network = to_network(skeleton)
        sizings = size_network(network, load_en12056(),
                               system=props.sistema, usage=props.uso)
        return catalog, sizings, route_along(network, sizings, catalog=catalog)

    def _execute(self, context):
        import os
        import tempfile
        from .core import export_network, validate_skeleton, write_network

        props = context.scene.mep
        errors = validate_skeleton(mesh_to_skeleton(context.active_object))
        if errors:
            self.report({"ERROR"}, "; ".join(errors))
            return {"CANCELLED"}
        try:
            catalog, sizings, routed = self._route(context)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        summary = (f"{len(routed.pipes)} tubi, {len(routed.fittings)} raccordi, "
                   f"{len(routed.warnings)} warning catalogo")
        ifc = tool.Ifc.get()
        if ifc is not None:
            try:
                _purge_circuit_objects(ifc, props.linea)
                products = write_network(ifc, routed, sizings=sizings,
                                         linea=props.linea, catalog=catalog)
            except Exception as exc:
                self.report({"ERROR"}, f"Scrittura nel modello fallita: {exc}")
                return {"CANCELLED"}
            try:
                loaded = sync_ifc_products(products)
            except Exception as exc:
                self.report({"WARNING"},
                            f"Linea '{props.linea}' scritta ({summary}) ma "
                            f"caricamento oggetti fallito: {exc} — ricarica il "
                            "progetto per vederli.")
                return {"FINISHED"}
            self.report({"INFO"},
                        f"Linea '{props.linea}': {summary}, {loaded} oggetti caricati.")
            return {"FINISHED"}

        try:
            base_dir = os.path.dirname(bpy.path.abspath(
                context.scene.BIMProperties.ifc_file)) or tempfile.gettempdir()
        except Exception:
            base_dir = tempfile.gettempdir()
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in props.linea)
        path = os.path.join(base_dir, f"mep_{safe}.ifc")
        export_network(routed, path, sizings=sizings, linea=props.linea,
                       catalog=catalog)
        self.report({"INFO"}, f"Scritto {path} ({summary}).")
        return {"FINISHED"}


class MepProperties(bpy.types.PropertyGroup):
    tipologia: bpy.props.EnumProperty(
        name="Tipologia",
        description="Famiglia di impianto (vedi spec §1); ogni tipologia ha "
                    "regole di sizing, norme e cataloghi propri",
        items=[
            ("GRAVITA", "Gravità",
             "Scarico acque nere/grigie, pluviali — UNI EN 12056"),
            ("PRESSIONE", "Pressione",
             "Adduzione idrosanitaria (UNI 9182), antincendio (UNI 10779, "
             "EN 12845), gas (UNI 7129) — da sviluppare"),
            ("AERAULICO", "Aeraulico",
             "Canali aria, VMC, climatizzazione (UNI 10339) — da sviluppare"),
        ],
        default="GRAVITA",
    )
    catalog_system: bpy.props.EnumProperty(
        name="Catalog",
        description="Sistema di parti per geometria e computo; quote dalle schede "
                    "tecniche del produttore o dalla norma di prodotto "
                    "(PVC interrato: UNI EN 1401-1:2019+A1:2023, Table 5/6)",
        items=_catalog_system_items,
    )
    sistema: bpy.props.EnumProperty(
        name="System",
        description="Sistema di scarico secondo UNI EN 12056-2:2001, §4.2 "
                    "(in Italia si usa normalmente il Sistema I)",
        items=[
            ("I", "Sistema I",
             "Colonna di scarico unica, diramazioni riempite parzialmente (0,5) — "
             "UNI EN 12056-2:2001, §4.2"),
            ("II", "Sistema II",
             "Colonna unica, diramazioni di piccolo diametro (riempimento 0,7) — "
             "UNI EN 12056-2:2001, §4.2"),
            ("IV", "Sistema IV",
             "Colonne di scarico separate per acque nere (WC/orinatoi) e grige — "
             "UNI EN 12056-2:2001, §4.2"),
        ],
        default="I",
    )
    tipo_recapito: bpy.props.EnumProperty(
        name="Recapito",
        description="Destinazione dello scarico — D.Lgs 152/2006, Parte III",
        items=[
            ("fognatura", "Fognatura",
             "Recapito in pubblica fognatura (baffo/pozzetto di allaccio "
             "predisposto) — D.Lgs 152/2006, Parte III"),
            ("corpo_superficiale", "Corpo superficiale",
             "Scarico in corpo idrico superficiale — D.Lgs 152/2006, Parte III"),
            ("suolo", "Suolo",
             "Dispersione al suolo (subirrigazione, pozzo perdente) — "
             "D.Lgs 152/2006, Parte III"),
        ],
        default="fognatura",
    )
    linea: bpy.props.StringProperty(
        name="Linea",
        description="Nome della linea: diventa l'IfcDistributionCircuit",
        default="Linea 1",
    )
    apparecchio: bpy.props.EnumProperty(
        name="Apparecchio",
        description="Apparecchio sanitario per Segna Utilizzatore — DU da "
                    "UNI EN 12056-2:2001, prospetto 2",
        items=_appliance_items,
    )
    uso: bpy.props.EnumProperty(
        name="Usage",
        description="Coefficiente di frequenza K, usato in Qww = K·√ΣDU — "
                    "UNI EN 12056-2:2001, §6.3.1 e prospetto 3",
        items=[
            ("intermittente", "Intermittente",
             "Abitazioni, locande, uffici: K=0,5 — UNI EN 12056-2:2001, prospetto 3"),
            ("frequente", "Frequente",
             "Ospedali, scuole, ristoranti, alberghi: K=0,7 — "
             "UNI EN 12056-2:2001, prospetto 3"),
            ("molto_frequente", "Molto frequente",
             "Bagni e/o docce pubbliche: K=1,0 — UNI EN 12056-2:2001, prospetto 3"),
            ("speciale", "Speciale",
             "Laboratori: K=1,2 — UNI EN 12056-2:2001, prospetto 3"),
        ],
        default="intermittente",
    )


classes = [
    MepProperties,
    CheckMepDataOperator,
    MarkTerminalOperator,
    MarkOutfallOperator,
    SyncPortsOperator,
    GenerateTracciatoOperator,
    ApplySlopesOperator,
    ValidateTracciatoOperator,
    GeneratePipesOperator,
]
