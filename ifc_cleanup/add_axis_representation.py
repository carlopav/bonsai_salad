"""
Bonsai / Blender script — Step 2
Per ogni oggetto IFC selezionato (tubo), crea una rappresentazione
Model/Axis/GRAPH_VIEW: una polilinea (IfcPolyline) che rappresenta l'asse
del tubo, ricavata raggruppando ("merge by distance") i vertici della mesh
per sezione trasversale e collegando i centroidi in ordine lungo lo sviluppo
del tubo.

Rispetto a un semplice bmesh.ops.remove_doubles: qui il punto risultante di
ogni cluster è il CENTROIDE esatto (media delle posizioni), non un vertice
qualunque del gruppo; e l'ordinamento dei centroidi lungo l'asse usa una
catena "nearest neighbour" a partire dall'estremo secondo la direzione
principale (PCA), robusta anche con più di due sezioni o un lieve tracciato
non perfettamente rettilineo.

Da eseguire nella tab "Scripting" di Blender con un progetto IFC caricato
in Bonsai e gli oggetti del tubo selezionati. Presuppone che la sub-context
Model/Axis/GRAPH_VIEW esista già nel progetto (in caso contrario va creata
prima, es. con lo strumento di gestione contesti di Bonsai).

SERVE PER PULIRE TUBI CHE PRESENTANO IfcPoligonalFaceSet CON VERTICI 
DUPLICATI O NON ORDINATI, CHE NON POSSONO ESSERE USATI COME AXIS.
"""

import bpy
import numpy as np
import ifcopenshell
import ifcopenshell.util.representation
import ifcopenshell.util.unit
import bonsai.tool as tool


MERGE_DISTANCE = 0.03  # metri (unità Blender) - soglia di clustering per individuare le sezioni del tubo
# deve essere: > distanza massima tra vertici adiacenti sulla stessa sezione
#              < distanza minima tra due sezioni consecutive del tubo


def get_local_vertices(obj):
    """Vertici della mesh valutata, in coordinate locali dell'oggetto
    (stesso sistema di riferimento usato dalla Body representation)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    coords = np.array([v.co[:] for v in mesh.vertices])
    eval_obj.to_mesh_clear()
    return coords


def cluster_by_distance(coords, threshold):
    """Raggruppa i vertici per distanza (union-find, merge transitivo come in
    Blender), poi ritorna il CENTROIDE di ciascun cluster."""
    n = len(coords)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if np.linalg.norm(coords[i] - coords[j]) <= threshold:
                union(i, j)

    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    return np.array([coords[idxs].mean(axis=0) for idxs in clusters.values()])


def order_along_axis(centroids):
    """Ordina i centroidi lungo lo sviluppo del tubo: parte dall'estremo
    secondo la direzione principale (PCA) e prosegue per vicino più prossimo."""
    if len(centroids) <= 2:
        # con solo 2 punti l'ordine per direzione principale è già sufficiente
        mean = centroids.mean(axis=0)
        _, _, vt = np.linalg.svd(centroids - mean)
        direction = vt[0]
        proj = (centroids - mean) @ direction
        return centroids[np.argsort(proj)]

    mean = centroids.mean(axis=0)
    _, _, vt = np.linalg.svd(centroids - mean)
    direction = vt[0]
    proj = (centroids - mean) @ direction
    start_idx = int(np.argmin(proj))  # estremo del tubo

    remaining = list(range(len(centroids)))
    ordered_idx = [start_idx]
    remaining.remove(start_idx)

    while remaining:
        last = centroids[ordered_idx[-1]]
        dists = [np.linalg.norm(last - centroids[i]) for i in remaining]
        nearest = remaining[int(np.argmin(dists))]
        ordered_idx.append(nearest)
        remaining.remove(nearest)

    return centroids[ordered_idx]


def add_axis_representation(ifc_file, element, obj, unit_scale, merge_distance=MERGE_DISTANCE):
    axis_context = ifcopenshell.util.representation.get_context(
        ifc_file, "Model", "Axis", "GRAPH_VIEW"
    )
    if axis_context is None:
        raise Exception(
            "Sub-context Model/Axis/GRAPH_VIEW non trovata nel progetto: va creata prima di eseguire lo script."
        )

    coords = get_local_vertices(obj)
    centroids = cluster_by_distance(coords, merge_distance)

    if len(centroids) < 2:
        raise Exception(
            f"{obj.name}: trovato un solo punto d'asse con MERGE_DISTANCE={merge_distance}. "
            "Aumenta la soglia o controlla la mesh."
        )

    ordered = order_along_axis(centroids)
    ifc_points = [
        ifc_file.createIfcCartesianPoint(tuple(float(c) / unit_scale for c in p))
        for p in ordered
    ]

    polyline = ifc_file.createIfcPolyline(Points=ifc_points)

    shape_rep = ifc_file.createIfcShapeRepresentation(
        ContextOfItems=axis_context,
        RepresentationIdentifier="Axis",
        RepresentationType="Curve3D",
        Items=[polyline],
    )

    product_shape = element.Representation
    if product_shape is None:
        product_shape = ifc_file.createIfcProductDefinitionShape(
            Name=None, Description=None, Representations=[shape_rep]
        )
        element.Representation = product_shape
    else:
        reps = list(product_shape.Representations)
        reps.append(shape_rep)
        product_shape.Representations = reps

    return shape_rep, len(ordered)


def main():
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        print("Nessun progetto IFC attivo in Bonsai.")
        return

    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(ifc_file)

    selected = [o for o in bpy.context.selected_objects if tool.Ifc.get_entity(o)]
    if not selected:
        print("Nessun oggetto IFC selezionato.")
        return

    for obj in selected:
        element = tool.Ifc.get_entity(obj)
        label = element.Name or element.GlobalId

        already_axis = element.Representation and any(
            r.RepresentationIdentifier == "Axis" for r in element.Representation.Representations
        )
        if already_axis:
            print(f"{label}: rappresentazione Axis già presente, salto.")
            continue

        try:
            _, n_points = add_axis_representation(ifc_file, element, obj, unit_scale)
            print(f"{label}: creata rappresentazione Model/Axis/GRAPH_VIEW ({n_points} punti).")
        except Exception as e:
            print(f"{label}: ERRORE - {e}")

    print("Completato.")


main()
