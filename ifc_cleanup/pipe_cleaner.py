"""
pipe_cleaner — Bonsai / Blender.

Cleans up pipes/ducts whose faceted Body groups several physical segments
into a single element (multiple Items, duplicated concentric surfaces):
derives the Model/Axis/GRAPH_VIEW axes — one IfcPolyline per continuous
segment, from clusters of vertices connected by the mesh edges — and their
quantities.

SPLIT_ELEMENTS = False: axis + Qto Length added to the original element.
SPLIT_ELEMENTS = True: one new element per segment, of the class required by
the type (e.g. IfcPipeSegment), with Body, Axis, Length and estimated
NominalDiameter; children are aggregated under the original
(IfcRelAggregates). If delete_original is set, the original gives up its
geometry and keeps only psets and total Length; otherwise it keeps its own
Body and the children get independent copies of the split geometry. New
Blender objects are created on the fly and, when the original is emptied,
its mesh is cleared: no reload needed.

Usage: Scripting tab, IFC project loaded in Bonsai, objects selected. The
Model/Axis/GRAPH_VIEW sub-context is created if missing. Runs inside a
tool.Ifc.Operator: CTRL+Z undoes everything (Bonsai transaction).
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


MERGE_DISTANCE = 0.03  # m - section clustering: > vertex spacing on a
#                            section, < distance between consecutive sections
DEDUP_DISTANCE = 0.06  # m - axes closer than this = same duplicated pipe
SPLIT_ELEMENTS = True  # True: one element per segment, aggregated under the original


# --------------------------------------------------------------------------
# Extract vertices + edges from the Body representation Items


def item_coords_edges(item, unit_scale):
    """Welded vertices and unique face edges of an item, in metres;
    (None, None) if the item type is unsupported."""
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
# Skeletonisation: islands -> clusters -> cluster graph -> chains


def split_islands(n_verts, edges):
    """Connected components by edge (union-find)."""
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
    """Vertex clusters by distance (transitive merge); returns (labels,
    centroids)."""
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
    """Maximal chains of the cluster graph (edges = mesh edges between
    distinct clusters): ordering follows the surface, not proximity."""
    adj = defaultdict(set)
    for a, b in edges:
        ca, cb = labels[a], labels[b]
        if ca != cb:
            adj[ca].add(cb)
            adj[cb].add(ca)
    if not adj:
        return []  # collapsed into a single node (e.g. an elbow)

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
# Axis measurements


def path_length(p):
    return float(np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1))) if len(p) > 1 else 0.0


def mean_nearest_distance(a, b):
    """Average distance from each point of a to its nearest point in b."""
    d2 = np.sum((a[:, None, :] - b[None, :, :]) ** 2, axis=2)
    return float(np.sqrt(d2.min(axis=1)).mean())


def points_to_segment_distance(pts, a, b):
    """Distance of each point from the segment a-b."""
    ab = b - a
    denom = float(ab @ ab)
    if denom == 0.0:
        return np.linalg.norm(pts - a, axis=1)
    t = np.clip((pts - a) @ ab / denom, 0.0, 1.0)
    proj = a + t[:, None] * ab
    return np.linalg.norm(pts - proj, axis=1)


def distance_to_paths(pts, paths):
    """Minimum distance of each point from the polyline segments."""
    best = np.full(len(pts), np.inf)
    for p in paths:
        for a, b in zip(p[:-1], p[1:]):
            best = np.minimum(best, points_to_segment_distance(pts, a, b))
    return best


def estimate_diameter(coords, paths):
    """Estimated diameter: 2x the median distance of the vertices from the
    axis."""
    return 2.0 * float(np.median(distance_to_paths(coords, paths)))


# --------------------------------------------------------------------------
# Deduplicating concentric surfaces and grouping by continuous pipe


def dedup_paths_indexed(paths, dedup_distance):
    """Discards paths nearly coincident with a longer one already kept
    (asymmetric test: a short axis over part of a long one is not a
    duplicate). Returns (kept indices, duplicate -> kept map)."""
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
    """One group per continuous segment: {"items", "paths", "coords"
    (leader)}. Duplicate items and collapsed fittings are appended to the
    nearest group. Returns (groups, skipped_items)."""
    item_data = []   # (item, coords) with at least one chain
    cand_paths = []  # (index into item_data, path)
    chainless = []   # (item, coords) with no chains
    skipped = []     # unsupported items

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

    # one group per leader item (owns at least one kept path)
    groups_by_leader = {}
    for pi in kept:
        ii, path = cand_paths[pi]
        g = groups_by_leader.setdefault(ii, {"item_idxs": [ii], "paths": []})
        g["paths"].append(path)

    # items with only duplicate paths: to the group of the path they duplicate
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

    # collapsed fittings: to the group with the nearest axis
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
# Writing to IFC


def get_or_create_axis_context(ifc_file):
    """Model/Axis/GRAPH_VIEW sub-context, created if missing."""
    axis_context = ifcopenshell.util.representation.get_context(
        ifc_file, "Model", "Axis", "GRAPH_VIEW"
    )
    if axis_context is not None:
        return axis_context

    model_context = ifcopenshell.util.representation.get_context(ifc_file, "Model")
    if model_context is None:
        raise Exception("Model context not found in the project.")
    axis_context = ifcopenshell.api.run(
        "context.add_context",
        ifc_file,
        context_type="Model",
        context_identifier="Axis",
        target_view="GRAPH_VIEW",
        parent=model_context,
    )
    print("Created Model/Axis/GRAPH_VIEW sub-context.")
    return axis_context


def create_axis_shape(ifc_file, axis_context, paths, unit_scale):
    """Axis/Curve3D ShapeRepresentation, one IfcPolyline per path."""
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
    """Creates or updates an occurrence's pset/qto."""
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
    """Simple mode: axis + total Length added to the original element."""
    paths = [p for g in groups for p in g["paths"]]
    shape_rep = create_axis_shape(ifc_file, axis_context, paths, unit_scale)

    product_shape = element.Representation
    product_shape.Representations = list(product_shape.Representations) + [shape_rep]

    set_length_quantity(ifc_file, element, sum(path_length(p) for p in paths), unit_scale)
    return paths


def occurrence_class(element, element_type, ifc_file):
    """Occurrence class required by the type in IFC4 (e.g.
    IfcCableCarrierSegmentType -> IfcCableCarrierSegment); the element's own
    class if already compatible or typeless."""
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
                        skipped_items, unit_scale, delete_original=True):
    """Split mode: one child per group (Body, Axis, Length, diameter),
    aggregated under the parent. If delete_original, the parent gives up its
    geometry and keeps only psets and total Length; otherwise it keeps its
    own Body and children get independent copies of the split geometry."""
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
                print(f"  {child.Name}: type not assignable ({e}), left untyped")
        child.ObjectPlacement = ifc_file.createIfcLocalPlacement(
            PlacementRelTo=element.ObjectPlacement,
            RelativePlacement=ifc_file.createIfcAxis2Placement3D(
                ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None
            ),
        )

        # delete_original: children take the original items, ceded by the
        # parent below; otherwise each child gets an independent deep copy.
        body_items = list(g["items"]) if delete_original else [
            ifcopenshell.util.element.copy_deep(ifc_file, it) for it in g["items"]
        ]
        body = ifc_file.createIfcShapeRepresentation(
            ContextOfItems=body_rep.ContextOfItems,
            RepresentationIdentifier="Body",
            RepresentationType=body_rep.RepresentationType,
            Items=body_items,
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

        print(f"  {child.Name}: {len(g['paths'])} polylines, {length:.2f} m, "
              f"estimated D {diameter * 1000:.0f} mm ({len(g['items'])} items)")
        children.append(child)

    ifcopenshell.api.run(
        "aggregate.assign_object", ifc_file,
        products=children, relating_object=element,
    )

    if delete_original:
        # the parent gives up its Body: only unsupported items remain, if any
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
# Updating the Blender scene without reloading the project


def create_blender_objects(elements):
    """Blender objects for the new elements (Bonsai's append-from-library
    pattern: a partial IfcImporter run on the already loaded file)."""
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
    """Empty mesh for the parent object (it remains as a linked
    container)."""
    obj.data = bpy.data.meshes.new(obj.data.name + "_container")


# --------------------------------------------------------------------------
# Main flow


def process_element(ifc_file, element, axis_context, unit_scale, delete_original=True):
    """Returns (message, newly created elements)."""
    body_rep = None
    if element.Representation:
        for rep in element.Representation.Representations:
            if rep.RepresentationIdentifier == "Body":
                body_rep = rep
                break
    if body_rep is None:
        raise Exception("no Body representation to derive axes from.")

    groups, skipped = extract_axis_groups(body_rep, unit_scale)
    if skipped:
        print(f"  (unsupported items, left on the parent: "
              f"{', '.join(it.is_a() for it in skipped)})")
    if not groups:
        raise Exception(
            f"no axis could be derived with MERGE_DISTANCE={MERGE_DISTANCE}. "
            "Increase the threshold or check the mesh."
        )

    if SPLIT_ELEMENTS:
        children = split_into_elements(
            ifc_file, element, axis_context, body_rep, groups, skipped,
            unit_scale, delete_original=delete_original,
        )
        return f"split into {len(children)} aggregated elements", children

    paths = add_axes_to_element(ifc_file, element, axis_context, groups, unit_scale)
    tot = sum(path_length(p) for p in paths)
    return (f"created Model/Axis/GRAPH_VIEW representation "
            f"({len(paths)} polylines, {tot:.2f} m total)"), []


def main(delete_original=True):
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        print("No active IFC project in Bonsai.")
        return

    selected = [o for o in bpy.context.selected_objects if tool.Ifc.get_entity(o)]
    if not selected:
        print("No IFC object selected.")
        return

    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)
    axis_context = get_or_create_axis_context(ifc_file)

    created = []   # new IFC elements to materialise in Blender
    emptied = []   # Blender objects of the emptied parents

    for obj in selected:
        element = tool.Ifc.get_entity(obj)
        label = element.Name or element.GlobalId

        if SPLIT_ELEMENTS and element.IsDecomposedBy:
            print(f"{label}: already decomposed, skipping.")
            continue
        already_axis = element.Representation and any(
            r.RepresentationIdentifier == "Axis" for r in element.Representation.Representations
        )
        if already_axis:
            print(f"{label}: Axis representation already present, skipping.")
            continue

        try:
            outcome, children = process_element(
                ifc_file, element, axis_context, unit_scale,
                delete_original=delete_original,
            )
            if children:
                created.extend(children)
                if delete_original:
                    emptied.append(obj)
            print(f"{label}: {outcome}.")
        except Exception as e:
            print(f"{label}: ERROR - {e}")

    if created:
        try:
            create_blender_objects(created)
            for obj in emptied:
                clear_parent_object(obj)
            print(f"Created {len(created)} Blender objects on the fly.")
        except Exception as e:
            print(f"Scene update failed ({e}): "
                  "reload the project to see the new objects.")

    print("Done.")


class PipeCleaner(bpy.types.Operator, tool.Ifc.Operator):
    """Targets IFC exports from ArchiCAD's Coordination View, which
    merges multiple physical pipe/duct segments into a single faceted
    element.
    Splits a pipe/duct into one child element per segment along the
    source facesets and aggregates the results under the original
    element. Each child gets its own Body, axis representation,
    segment length and estimated diameter. The original element is
    kept as a container, retaining its GUID and property sets."""

    bl_idname = "bim.pipe_cleaner"
    bl_label = "Pipe Cleaner: split grouped pipes into axes"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        props = getattr(context.scene, "ifc_cleanup", None)
        delete_original = props.delete_original_geometry if props else True
        main(delete_original=delete_original)


if __name__ == "__main__":
    # replaces any already-registered class (re-runs)
    registered = getattr(bpy.types, "BIM_OT_pipe_cleaner", None)
    if registered is not None:
        bpy.utils.unregister_class(registered)
    bpy.utils.register_class(PipeCleaner)
    bpy.ops.bim.pipe_cleaner()
