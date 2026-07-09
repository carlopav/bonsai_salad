"""
pipe_cleaner — Bonsai / Blender.

Ripulisce tubi/condotti la cui Body faccettata raggruppa più tratti fisici in
un solo elemento (Items multipli, superfici concentriche duplicate): ricava
gli assi Model/Axis/GRAPH_VIEW — una IfcPolyline per tratto continuo, da
cluster di vertici collegati secondo gli spigoli della mesh — e le quantità.

SPLIT_ELEMENTS = False: assi + Qto Length sull'elemento originale.
SPLIT_ELEMENTS = True: un nuovo elemento per tratto, della classe richiesta
dal tipo (es. IfcPipeSegment), con Body, Axis, Length e NominalDiameter
stimato; i figli vengono aggregati all'originale (IfcRelAggregates), che cede
la geometria e tiene psets e Length totale. Gli oggetti Blender dei figli
sono creati al volo e la mesh del padre svuotata: niente reload.

Uso: tab Scripting, progetto IFC caricato in Bonsai, oggetti selezionati.
La sub-context Model/Axis/GRAPH_VIEW viene creata se manca. Gira dentro un
operatore tool.Ifc.Operator: CTRL+Z annulla tutto (transazione Bonsai).
"""

import bpy
import numpy as np
from collections import defaultdict
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.representation
import ifcopenshell.util.type
import ifcopenshell.util.unit
import bonsai.tool as tool


MERGE_DISTANCE = 0.03  # m - clustering sezioni: > passo vertici sulla sezione,
#                            < distanza tra sezioni consecutive
DEDUP_DISTANCE = 0.06  # m - assi più vicini di così = stesso tubo duplicato
SPLIT_ELEMENTS = True  # True: un elemento per tratto, aggregati all'originale


# --------------------------------------------------------------------------
# Estrazione vertici + spigoli dagli Items della Body representation


def item_coords_edges(item, unit_scale):
    """Vertici saldati e spigoli unici delle facce di un item, in metri;
    (None, None) se il tipo di item non è supportato."""
    pts_index = {}
    coords_list = []
    edges = set()

    def widx(c):
        key = tuple(np.round(np.array(c, dtype=float) * unit_scale, 6))
        if key not in pts_index:
            pts_index[key] = len(coords_list)
            coords_list.append(key)
        return pts_index[key]

    def add_loop(ids):
        for a, b in zip(ids, ids[1:] + ids[:1]):
            if a != b:
                edges.add((min(a, b), max(a, b)))

    def add_faces(faces):
        for face in faces:
            for bound in face.Bounds:
                loop = bound.Bound
                if loop.is_a("IfcPolyLoop"):
                    add_loop([widx(p.Coordinates) for p in loop.Polygon])

    if item.is_a("IfcShellBasedSurfaceModel"):
        for shell in item.SbsmBoundary:
            add_faces(shell.CfsFaces)
    elif item.is_a("IfcFaceBasedSurfaceModel"):
        for shell in item.FbsmFaces:
            add_faces(shell.CfsFaces)
    elif item.is_a("IfcFacetedBrep"):
        add_faces(item.Outer.CfsFaces)
    elif item.is_a("IfcPolygonalFaceSet"):
        base = [widx(c) for c in item.Coordinates.CoordList]
        for face in item.Faces:
            add_loop([base[i - 1] for i in face.CoordIndex])
    elif item.is_a("IfcTriangulatedFaceSet"):
        base = [widx(c) for c in item.Coordinates.CoordList]
        for tri in item.CoordIndex:
            add_loop([base[i - 1] for i in tri])
    else:
        return None, None

    return np.array(coords_list), sorted(edges)


# --------------------------------------------------------------------------
# Scheletrizzazione: isole -> cluster -> grafo dei cluster -> catene


def split_islands(n_verts, edges):
    """Componenti connesse per spigoli (union-find)."""
    parent = list(range(n_verts))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    islands = defaultdict(list)
    for i in range(n_verts):
        islands[find(i)].append(i)
    return [np.array(v) for v in islands.values()]


def cluster_labels(coords, threshold):
    """Cluster di vertici per distanza (merge transitivo); ritorna
    (labels, centroidi)."""
    n = len(coords)
    labels = np.full(n, -1, dtype=int)
    th2 = threshold * threshold
    centroids = []
    for i in range(n):
        if labels[i] >= 0:
            continue
        lab = len(centroids)
        labels[i] = lab
        stack = [i]
        members = []
        while stack:
            k = stack.pop()
            members.append(k)
            d2 = np.sum((coords - coords[k]) ** 2, axis=1)
            neigh = np.nonzero((d2 <= th2) & (labels < 0))[0]
            labels[neigh] = lab
            stack.extend(neigh.tolist())
        centroids.append(coords[members].mean(axis=0))
    return labels, np.array(centroids)


def chains_from_cluster_graph(centroids, labels, edges):
    """Catene massimali del grafo dei cluster (archi = spigoli mesh tra
    cluster diversi): l'ordinamento segue la superficie, non la vicinanza."""
    adj = defaultdict(set)
    for a, b in edges:
        ca, cb = labels[a], labels[b]
        if ca != cb:
            adj[ca].add(cb)
            adj[cb].add(ca)
    if not adj:
        return []  # collassato in un nodo solo (es. gomito)

    used = set()
    chains = []
    joints = [v for v in adj if len(adj[v]) != 2]
    starts = joints if joints else [next(iter(adj))]
    for s in starts:
        for nb in list(adj[s]):
            e = frozenset((s, nb))
            if e in used:
                continue
            used.add(e)
            chain = [s, nb]
            prev, cur = s, nb
            while len(adj[cur]) == 2:
                nxt = next(x for x in adj[cur] if x != prev)
                e = frozenset((cur, nxt))
                if e in used:
                    break
                used.add(e)
                chain.append(nxt)
                prev, cur = cur, nxt
            chains.append(np.array([centroids[c] for c in chain]))
    return chains


# --------------------------------------------------------------------------
# Misure sugli assi


def path_length(p):
    return float(np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1))) if len(p) > 1 else 0.0


def mean_nearest_distance(a, b):
    """Distanza media dei punti di a dal più vicino di b."""
    d2 = np.sum((a[:, None, :] - b[None, :, :]) ** 2, axis=2)
    return float(np.sqrt(d2.min(axis=1)).mean())


def points_to_segment_distance(pts, a, b):
    """Distanza di ogni punto dal segmento a-b."""
    ab = b - a
    denom = float(ab @ ab)
    if denom == 0.0:
        return np.linalg.norm(pts - a, axis=1)
    t = np.clip((pts - a) @ ab / denom, 0.0, 1.0)
    proj = a + t[:, None] * ab
    return np.linalg.norm(pts - proj, axis=1)


def distance_to_paths(pts, paths):
    """Distanza minima di ogni punto dai segmenti delle polilinee."""
    best = np.full(len(pts), np.inf)
    for p in paths:
        for a, b in zip(p[:-1], p[1:]):
            best = np.minimum(best, points_to_segment_distance(pts, a, b))
    return best


def estimate_diameter(coords, paths):
    """Diametro stimato: 2x mediana della distanza dei vertici dall'asse."""
    return 2.0 * float(np.median(distance_to_paths(coords, paths)))


# --------------------------------------------------------------------------
# Deduplicazione superfici concentriche e raggruppamento per tubo continuo


def dedup_paths_indexed(paths, dedup_distance):
    """Scarta i path quasi coincidenti con uno più lungo già tenuto (test
    asimmetrico: un asse corto su un tratto di uno lungo non è duplicato).
    Ritorna (indici tenuti, mappa duplicato -> tenuto)."""
    order = sorted(range(len(paths)), key=lambda i: path_length(paths[i]), reverse=True)
    kept = []
    dup_of = {}
    for i in order:
        match = None
        for k in kept:
            if (mean_nearest_distance(paths[i], paths[k]) <= dedup_distance
                    and mean_nearest_distance(paths[k], paths[i]) <= dedup_distance * 2):
                match = k
                break
        if match is None:
            kept.append(i)
        else:
            dup_of[i] = match
    return kept, dup_of


def extract_axis_groups(body_rep, unit_scale, merge_distance=MERGE_DISTANCE,
                        dedup_distance=DEDUP_DISTANCE):
    """Un gruppo per tratto continuo: {"items", "paths", "coords" (capofila)}.
    Item duplicati e raccordi collassati si accodano al gruppo più vicino.
    Ritorna (groups, skipped_items)."""
    item_data = []   # (item, coords) con almeno una catena
    cand_paths = []  # (indice in item_data, path)
    chainless = []   # (item, coords) senza catene
    skipped = []     # item non supportati

    for item in body_rep.Items:
        coords, edges = item_coords_edges(item, unit_scale)
        if coords is None:
            skipped.append(item)
            continue
        paths = []
        for isl in split_islands(len(coords), edges):
            isl_pos = {int(g): k for k, g in enumerate(isl)}
            sub_edges = [(isl_pos[a], isl_pos[b]) for a, b in edges
                         if a in isl_pos and b in isl_pos]
            labels, cents = cluster_labels(coords[isl], merge_distance)
            paths.extend(chains_from_cluster_graph(cents, labels, sub_edges))
        paths = [p for p in paths if len(p) >= 2]
        if paths:
            idx = len(item_data)
            item_data.append((item, coords))
            cand_paths.extend((idx, p) for p in paths)
        else:
            chainless.append((item, coords))

    kept, dup_of = dedup_paths_indexed([p for _, p in cand_paths], dedup_distance)

    # un gruppo per item capofila (possiede almeno un path tenuto)
    groups_by_leader = {}
    for pi in kept:
        ii, path = cand_paths[pi]
        g = groups_by_leader.setdefault(ii, {"item_idxs": [ii], "paths": []})
        g["paths"].append(path)

    # item con soli path duplicati: al gruppo del path che duplicano
    for ii in range(len(item_data)):
        if ii in groups_by_leader:
            continue
        for pi, (oi, _) in enumerate(cand_paths):
            if oi == ii and pi in dup_of:
                leader = cand_paths[dup_of[pi]][0]
                groups_by_leader[leader]["item_idxs"].append(ii)
                break

    groups = []
    for leader, g in groups_by_leader.items():
        groups.append({
            "items": [item_data[i][0] for i in g["item_idxs"]],
            "paths": g["paths"],
            "coords": item_data[leader][1],
        })

    # raccordi collassati: al gruppo con l'asse più vicino
    for item, coords in chainless:
        centre = coords.mean(axis=0)[None, :]
        best, best_d = None, np.inf
        for g in groups:
            d = float(distance_to_paths(centre, g["paths"])[0])
            if d < best_d:
                best, best_d = g, d
        if best is not None:
            best["items"].append(item)

    return groups, skipped


# --------------------------------------------------------------------------
# Scrittura IFC


def get_or_create_axis_context(ifc_file):
    """Sub-context Model/Axis/GRAPH_VIEW, creata se manca."""
    axis_context = ifcopenshell.util.representation.get_context(
        ifc_file, "Model", "Axis", "GRAPH_VIEW"
    )
    if axis_context is not None:
        return axis_context

    model_context = ifcopenshell.util.representation.get_context(ifc_file, "Model")
    if model_context is None:
        raise Exception("context Model non trovata nel progetto.")
    axis_context = ifcopenshell.api.run(
        "context.add_context",
        ifc_file,
        context_type="Model",
        context_identifier="Axis",
        target_view="GRAPH_VIEW",
        parent=model_context,
    )
    print("Sub-context Model/Axis/GRAPH_VIEW creata.")
    return axis_context


def create_axis_shape(ifc_file, axis_context, paths, unit_scale):
    """ShapeRepresentation Axis/Curve3D, una IfcPolyline per path."""
    polylines = []
    for path in paths:
        ifc_points = [
            ifc_file.createIfcCartesianPoint(tuple(float(c) / unit_scale for c in p))
            for p in path
        ]
        polylines.append(ifc_file.createIfcPolyline(Points=ifc_points))
    return ifc_file.createIfcShapeRepresentation(
        ContextOfItems=axis_context,
        RepresentationIdentifier="Axis",
        RepresentationType="Curve3D",
        Items=polylines,
    )


def write_pset(ifc_file, element, name, properties, is_qto=False):
    """Crea o aggiorna un pset/qto dell'occorrenza."""
    existing = ifcopenshell.util.element.get_pset(element, name, should_inherit=False)
    if existing:
        pset = ifc_file.by_id(existing["id"])
    else:
        pset = ifcopenshell.api.run(
            "pset.add_qto" if is_qto else "pset.add_pset",
            ifc_file, product=element, name=name,
        )
    ifcopenshell.api.run(
        "pset.edit_qto" if is_qto else "pset.edit_pset",
        ifc_file, properties=properties,
        **{"qto" if is_qto else "pset": pset},
    )


def set_length_quantity(ifc_file, element, length_m, unit_scale):
    write_pset(ifc_file, element, "Qto_PipeSegmentBaseQuantities",
               {"Length": length_m / unit_scale}, is_qto=True)


def set_diameter_pset(ifc_file, element, diameter_m, unit_scale):
    write_pset(ifc_file, element, "Pset_PipeSegmentTypeCommon",
               {"NominalDiameter": diameter_m / unit_scale})


def add_axes_to_element(ifc_file, element, axis_context, groups, unit_scale):
    """Modalità semplice: assi + Length totale sull'elemento originale."""
    paths = [p for g in groups for p in g["paths"]]
    shape_rep = create_axis_shape(ifc_file, axis_context, paths, unit_scale)

    product_shape = element.Representation
    product_shape.Representations = list(product_shape.Representations) + [shape_rep]

    set_length_quantity(ifc_file, element, sum(path_length(p) for p in paths), unit_scale)
    return paths


def occurrence_class(element, element_type, ifc_file):
    """Classe occorrenza richiesta dal tipo in IFC4 (es.
    IfcCableCarrierSegmentType -> IfcCableCarrierSegment); quella
    dell'elemento se già compatibile o senza tipo."""
    cls = element.is_a()
    if element_type is None:
        return cls
    applicable = ifcopenshell.util.type.get_applicable_entities(
        element_type.is_a(), ifc_file.schema
    )
    if not applicable or cls in applicable:
        return cls
    return applicable[0]


def split_into_elements(ifc_file, element, axis_context, body_rep, groups,
                        skipped_items, unit_scale):
    """Modalità split: un figlio per gruppo (Body, Axis, Length, diametro),
    aggregati al padre che cede la geometria e tiene psets e Length totale."""
    element_type = ifcopenshell.util.element.get_type(element)
    child_class = occurrence_class(element, element_type, ifc_file)
    label = element.Name or element.GlobalId
    children = []
    total_length = 0.0

    for i, g in enumerate(groups, 1):
        child = ifcopenshell.api.run(
            "root.create_entity", ifc_file,
            ifc_class=child_class, name=f"{label}-{i:02d}",
        )
        if element_type is not None:
            try:
                ifcopenshell.api.run(
                    "type.assign_type", ifc_file,
                    related_objects=[child], relating_type=element_type,
                )
            except Exception as e:
                print(f"  {child.Name}: tipo non assegnabile ({e}), lasciato senza tipo")
        child.ObjectPlacement = ifc_file.createIfcLocalPlacement(
            PlacementRelTo=element.ObjectPlacement,
            RelativePlacement=ifc_file.createIfcAxis2Placement3D(
                ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None
            ),
        )

        body = ifc_file.createIfcShapeRepresentation(
            ContextOfItems=body_rep.ContextOfItems,
            RepresentationIdentifier="Body",
            RepresentationType=body_rep.RepresentationType,
            Items=g["items"],
        )
        axis = create_axis_shape(ifc_file, axis_context, g["paths"], unit_scale)
        child.Representation = ifc_file.createIfcProductDefinitionShape(
            Representations=[body, axis]
        )

        length = sum(path_length(p) for p in g["paths"])
        total_length += length
        set_length_quantity(ifc_file, child, length, unit_scale)
        diameter = estimate_diameter(g["coords"], g["paths"])
        set_diameter_pset(ifc_file, child, diameter, unit_scale)

        print(f"  {child.Name}: {len(g['paths'])} polilinee, {length:.2f} m, "
              f"D stimato {diameter * 1000:.0f} mm ({len(g['items'])} items)")
        children.append(child)

    ifcopenshell.api.run(
        "aggregate.assign_object", ifc_file,
        products=children, relating_object=element,
    )

    # il padre cede la Body: restano solo gli eventuali item non supportati
    if skipped_items:
        body_rep.Items = skipped_items
    else:
        product_shape = element.Representation
        reps = [r for r in product_shape.Representations if r != body_rep]
        ifc_file.remove(body_rep)
        if reps:
            product_shape.Representations = reps
        else:
            element.Representation = None
            ifc_file.remove(product_shape)

    set_length_quantity(ifc_file, element, total_length, unit_scale)
    return children


# --------------------------------------------------------------------------
# Aggiornamento della scena Blender senza ricaricare il progetto


def create_blender_objects(elements):
    """Oggetti Blender per i nuovi elementi (pattern append-da-libreria di
    Bonsai: IfcImporter parziale sul file già caricato)."""
    import logging
    from bonsai.bim.ifc import IfcStore
    import bonsai.bim.import_ifc as import_ifc

    settings = import_ifc.IfcImportSettings.factory(
        bpy.context, IfcStore.path, logging.getLogger("ImportIFC")
    )
    importer = import_ifc.IfcImporter(settings)
    importer.file = tool.Ifc.get()
    importer.process_context_filter()
    importer.material_creator.load_existing_materials()
    importer.create_generic_elements(set(elements))
    importer.place_objects_in_collections()


def clear_parent_object(obj):
    """Mesh vuota per l'oggetto del padre (resta come contenitore linkato)."""
    obj.data = bpy.data.meshes.new(obj.data.name + "_container")


# --------------------------------------------------------------------------
# Flusso principale


def process_element(ifc_file, element, axis_context, unit_scale):
    """Ritorna (messaggio, nuovi elementi creati)."""
    body_rep = None
    if element.Representation:
        for rep in element.Representation.Representations:
            if rep.RepresentationIdentifier == "Body":
                body_rep = rep
                break
    if body_rep is None:
        raise Exception("nessuna Body representation da cui ricavare gli assi.")

    groups, skipped = extract_axis_groups(body_rep, unit_scale)
    if skipped:
        print(f"  (items non supportati, lasciati al padre: "
              f"{', '.join(it.is_a() for it in skipped)})")
    if not groups:
        raise Exception(
            f"nessun asse ricavabile con MERGE_DISTANCE={MERGE_DISTANCE}. "
            "Aumenta la soglia o controlla la mesh."
        )

    if SPLIT_ELEMENTS:
        children = split_into_elements(
            ifc_file, element, axis_context, body_rep, groups, skipped, unit_scale
        )
        return f"splittato in {len(children)} elementi aggregati", children

    paths = add_axes_to_element(ifc_file, element, axis_context, groups, unit_scale)
    tot = sum(path_length(p) for p in paths)
    return (f"creata rappresentazione Model/Axis/GRAPH_VIEW "
            f"({len(paths)} polilinee, {tot:.2f} m totali)"), []


def main():
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        print("Nessun progetto IFC attivo in Bonsai.")
        return

    selected = [o for o in bpy.context.selected_objects if tool.Ifc.get_entity(o)]
    if not selected:
        print("Nessun oggetto IFC selezionato.")
        return

    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
    axis_context = get_or_create_axis_context(ifc_file)

    created = []   # nuovi elementi IFC da materializzare in Blender
    emptied = []   # oggetti Blender dei padri svuotati

    for obj in selected:
        element = tool.Ifc.get_entity(obj)
        label = element.Name or element.GlobalId

        if SPLIT_ELEMENTS and element.IsDecomposedBy:
            print(f"{label}: già decomposto, salto.")
            continue
        already_axis = element.Representation and any(
            r.RepresentationIdentifier == "Axis" for r in element.Representation.Representations
        )
        if already_axis:
            print(f"{label}: rappresentazione Axis già presente, salto.")
            continue

        try:
            outcome, children = process_element(ifc_file, element, axis_context, unit_scale)
            if children:
                created.extend(children)
                emptied.append(obj)
            print(f"{label}: {outcome}.")
        except Exception as e:
            print(f"{label}: ERRORE - {e}")

    if created:
        try:
            create_blender_objects(created)
            for obj in emptied:
                clear_parent_object(obj)
            print(f"Creati al volo {len(created)} oggetti Blender.")
        except Exception as e:
            print(f"Aggiornamento della scena fallito ({e}): "
                  "ricarica il progetto per vedere i nuovi oggetti.")

    print("Completato.")


class PipeCleaner(bpy.types.Operator, tool.Ifc.Operator):
    """Assi GRAPH_VIEW (ed eventuale split) per i tubi selezionati."""

    bl_idname = "bim.pipe_cleaner"
    bl_label = "Pipe cleaner: assi GRAPH_VIEW tubi raggruppati"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        main()


if __name__ == "__main__":
    # sostituisce l'eventuale classe già registrata (ri-esecuzioni)
    registered = getattr(bpy.types, "BIM_OT_pipe_cleaner", None)
    if registered is not None:
        bpy.utils.unregister_class(registered)
    bpy.utils.register_class(PipeCleaner)
    bpy.ops.bim.pipe_cleaner()
