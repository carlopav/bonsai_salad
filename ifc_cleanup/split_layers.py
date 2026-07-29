"""
split_layers — Bonsai / Blender.

Splits each selected IFC element built from a material layer set
(IfcMaterialLayerSetUsage / IfcMaterialLayerSet) into an aggregate: the
original element is repurposed as the parent container (keeping its GUID,
psets, openings and all IFC relationships) and gives up only its
layer-based Body geometry; one IfcBuildingElementPart child is created per
layer, carrying that layer's material, name and thickness. Openings kept on
the parent aggregate cut the children automatically.

The single source of truth for each part is its layer: every part gets a
single-layer IfcMaterialLayerSetUsage (bare set when the source had no
usage) mirroring the split-off layer, and its Body is generated from that
layer — a single IfcExtrudedAreaSolid whose thickness, offset along the
thickness axis and direction come from the usage (OffsetFromReferenceLine /
DirectionSense / LayerSetDirection) and whose footprint is the parent's
bounding box on the two perpendicular axes. Body / usage are coherent by
construction, so a later Bonsai parametric edit reproduces the same solid.

Parts split off walls sharing the same IfcWallType and the same layer build
up share one IfcBuildingElementPartType (a run-level registry keyed by
source type + layer), so counter-walls of same-type walls reuse the same
part type. The type carries the layer material only, no geometry.

Usage: select one or more IFC objects, run. Runs inside a tool.Ifc.Operator:
CTRL+Z undoes everything (Bonsai transaction).
"""

import bpy
import numpy as np
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.element
import ifcopenshell.util.unit
import bonsai.tool as tool

# LayerSetDirection -> index of the thickness axis in the element's local frame
AXIS_INDEX = {"AXIS1": 0, "AXIS2": 1, "AXIS3": 2}


# --------------------------------------------------------------------------
# Reading the layer set


def get_layer_set_usage(element):
    """(layers, axis_index, offset_m, sign, unit_scale, has_usage) for a
    layered element, or None. offset_m is the reference-line offset in
    metres; sign is +1 for a POSITIVE direction sense, -1 for NEGATIVE;
    has_usage is False for a bare layer set (defaults AXIS2/POSITIVE/0)."""
    material = ifcopenshell.util.element.get_material(element)
    if material is None:
        return None
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(tool.Ifc.get())
    if material.is_a("IfcMaterialLayerSetUsage"):
        layer_set = material.ForLayerSet
        axis = AXIS_INDEX.get(material.LayerSetDirection, 1)
        offset = (material.OffsetFromReferenceLine or 0.0) * unit_scale
        sign = -1.0 if material.DirectionSense == "NEGATIVE" else 1.0
        has_usage = True
    elif material.is_a("IfcMaterialLayerSet"):
        layer_set = material
        axis, offset, sign, has_usage = 1, 0.0, 1.0, False
    else:
        return None
    layers = layer_set.MaterialLayers or []
    if not layers:
        return None
    return layers, axis, offset, sign, unit_scale, has_usage


# --------------------------------------------------------------------------
# Per-layer geometry: extrude the single layer, coherent with its usage


def footprint_extents(obj, axis):
    """(u_axis, u0, u1, v_axis, v0, v1) in metres: bounding box of the
    object's mesh on the two axes perpendicular to the thickness axis, read
    from the loaded Blender mesh in the object's LOCAL frame. Bonsai keeps the
    placement in matrix_world, so local mesh coordinates are the IFC
    representation frame (same frame as the layer set axis and the bands) and
    are already in metres. None if the object has no mesh vertices."""
    if obj.type != "MESH" or obj.data is None or len(obj.data.vertices) == 0:
        return None
    co = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    u_axis, v_axis = (a for a in (0, 1, 2) if a != axis)
    u = co[:, u_axis]
    v = co[:, v_axis]
    return u_axis, float(u.min()), float(u.max()), v_axis, float(v.min()), float(v.max())


def layer_extrusion(ifc_file, axis, band, footprint, unit_scale):
    """Single-layer IfcExtrudedAreaSolid coherent with the usage: a centred
    IfcRectangleProfileDef on the footprint (u, v) plane extruded along the
    thickness axis over the band [band[0], band[1]]. Placement local Z is the
    thickness axis (AXIS1/2/3), local X the u axis; extrusion runs +Z for the
    band depth. Coordinates in metres, written in project units."""
    u_axis, u0, u1, v_axis, v0, v1 = footprint
    depth = (band[1] - band[0]) / unit_scale
    origin = [0.0, 0.0, 0.0]
    origin[axis] = band[0]
    origin[u_axis] = (u0 + u1) / 2.0
    origin[v_axis] = (v0 + v1) / 2.0
    z_dir = [0.0, 0.0, 0.0]
    z_dir[axis] = 1.0
    x_dir = [0.0, 0.0, 0.0]
    x_dir[u_axis] = 1.0
    position = ifc_file.createIfcAxis2Placement3D(
        ifc_file.createIfcCartesianPoint(tuple(c / unit_scale for c in origin)),
        ifc_file.createIfcDirection(tuple(z_dir)),
        ifc_file.createIfcDirection(tuple(x_dir)),
    )
    profile = ifc_file.createIfcRectangleProfileDef("AREA", None, None, (u1 - u0) / unit_scale, (v1 - v0) / unit_scale)
    return ifc_file.createIfcExtrudedAreaSolid(profile, position, ifc_file.createIfcDirection((0.0, 0.0, 1.0)), depth)


def layer_bands(layers, offset, sign, unit_scale):
    """(lo, hi) extent in metres along the thickness axis for each layer,
    stacked from the reference-line offset in the direction sense."""
    bands = []
    cursor = offset
    for layer in layers:
        thickness = (layer.LayerThickness or 0.0) * unit_scale
        nxt = cursor + sign * thickness
        bands.append((min(cursor, nxt), max(cursor, nxt)))
        cursor = nxt
    return bands


# --------------------------------------------------------------------------
# Writing to IFC


def part_placement(ifc_file, parent):
    """Identity placement relative to the parent element."""
    return ifc_file.createIfcLocalPlacement(
        PlacementRelTo=parent.ObjectPlacement,
        RelativePlacement=ifc_file.createIfcAxis2Placement3D(
            ifc_file.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None
        ),
    )


def get_or_create_part_type(ifc_file, registry, source_type, parent, layer, thickness_m):
    """Shared IfcBuildingElementPartType per (source element type, layer):
    counter-walls of same-type walls with the same build up reuse one type.
    The type carries the layer material only, no geometry. Returns None when
    the schema has no IfcBuildingElementPartType (IFC2X3) — the flag is
    latched so the fallback is reported once."""
    if registry.get("_unsupported"):
        return None
    material = layer.Material
    key_type = source_type.GlobalId if source_type else parent.is_a()
    key = (key_type, material.id() if material else None, round(thickness_m, 6), layer.Name)
    part_type = registry.get(key)
    if part_type is not None:
        return part_type

    type_label = source_type.Name if source_type else parent.is_a()
    material_label = material.Name if material else "NoMaterial"
    name = f"{type_label}-{material_label}-{round(thickness_m * 1000)}mm"
    try:
        part_type = ifcopenshell.api.run(
            "root.create_entity", ifc_file, ifc_class="IfcBuildingElementPartType", name=name
        )
    except Exception:
        registry["_unsupported"] = True
        return None
    if material:
        ifcopenshell.api.run("material.assign_material", ifc_file, products=[part_type], material=material)
    registry[key] = part_type
    return part_type


def attach_single_layer_usage(ifc_file, part, layer, band, axis, sign, has_usage, unit_scale):
    """Give the part a single-layer material layer set that mirrors the
    split-off layer, so it stays parametrically editable. When the source
    element had a usage, wrap it in an IfcMaterialLayerSetUsage carrying the
    original LayerSetDirection/DirectionSense and an OffsetFromReferenceLine
    placing the single layer exactly on its band; a bare source set yields a
    bare set (Bonsai infers the direction from class/pset). This is the part's
    material association and the single source of truth its Body is built
    from."""
    layer_set = ifcopenshell.api.run(
        "material.add_material_set", ifc_file, name=layer.Name or "Layer", set_type="IfcMaterialLayerSet"
    )
    new_layer = ifcopenshell.api.run("material.add_layer", ifc_file, layer_set=layer_set, material=layer.Material)
    attributes = {"LayerThickness": (band[1] - band[0]) / unit_scale}
    for attr in ("Name", "Description", "Category", "Priority", "IsVentilated"):
        if hasattr(layer, attr) and getattr(layer, attr) is not None:
            attributes[attr] = getattr(layer, attr)
    ifcopenshell.api.run("material.edit_layer", ifc_file, layer=new_layer, attributes=attributes)

    if not has_usage:
        ifcopenshell.api.run(
            "material.assign_material", ifc_file, products=[part], type="IfcMaterialLayerSet", material=layer_set
        )
        return
    rel = ifcopenshell.api.run(
        "material.assign_material", ifc_file, products=[part], type="IfcMaterialLayerSetUsage", material=layer_set
    )
    usage = rel.RelatingMaterial
    usage.LayerSetDirection = {0: "AXIS1", 1: "AXIS2", 2: "AXIS3"}[axis]
    usage.DirectionSense = "POSITIVE" if sign > 0 else "NEGATIVE"
    usage.OffsetFromReferenceLine = (band[0] if sign > 0 else band[1]) / unit_scale


def create_part(
    ifc_file, parent, body_context, footprint, layer, band, axis, sign, has_usage, unit_scale, registry, source_type
):
    """One IfcBuildingElementPart for a layer: a single-layer material layer
    set usage as the single source of truth, name, thickness pset, a shared
    part type and a Body extruded from that layer (thickness, offset and
    direction taken from the usage, footprint from the parent)."""
    name = layer.Name or (layer.Material.Name if layer.Material else None)
    part = ifcopenshell.api.run("root.create_entity", ifc_file, ifc_class="IfcBuildingElementPart", name=name)
    part.ObjectPlacement = part_placement(ifc_file, parent)

    solid = layer_extrusion(ifc_file, axis, band, footprint, unit_scale)
    body = ifc_file.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )
    part.Representation = ifc_file.createIfcProductDefinitionShape(Representations=[body])

    thickness_m = band[1] - band[0]
    pset = ifcopenshell.api.run("pset.add_pset", ifc_file, product=part, name="Salad_MaterialLayer")
    ifcopenshell.api.run("pset.edit_pset", ifc_file, pset=pset, properties={"LayerThickness": thickness_m / unit_scale})

    part_type = get_or_create_part_type(ifc_file, registry, source_type, parent, layer, thickness_m)
    if part_type is not None:
        ifcopenshell.api.run(
            "type.assign_type",
            ifc_file,
            related_objects=[part],
            relating_type=part_type,
            should_map_representations=False,
        )
    # last, so the single-layer usage is the part's final material association
    if layer.Material:
        attach_single_layer_usage(ifc_file, part, layer, band, axis, sign, has_usage, unit_scale)
    return part


def body_representation(element):
    if not element.Representation:
        return None
    for rep in element.Representation.Representations:
        if rep.RepresentationIdentifier == "Body":
            return rep
    return None


def process_element(ifc_file, element, obj, registry):
    """Repurposes element as the aggregate parent and creates its layer
    parts. Returns the list of new parts, or raises on a hard failure."""
    usage = get_layer_set_usage(element)
    if usage is None:
        raise Exception("no material layer set")
    layers, axis, offset, sign, unit_scale, has_usage = usage

    body_rep = body_representation(element)
    if body_rep is None:
        raise Exception("no Body representation to split")
    body_context = body_rep.ContextOfItems

    footprint = footprint_extents(obj, axis)
    if footprint is None:
        raise Exception("no mesh geometry to derive the footprint from")

    source_type = ifcopenshell.util.element.get_type(element)
    bands = layer_bands(layers, offset, sign, unit_scale)
    parts = [
        create_part(
            ifc_file,
            element,
            body_context,
            footprint,
            layer,
            band,
            axis,
            sign,
            has_usage,
            unit_scale,
            registry,
            source_type,
        )
        for layer, band in zip(layers, bands)
    ]

    ifcopenshell.api.run("aggregate.assign_object", ifc_file, products=parts, relating_object=element)

    # the parent gives up its layer Body; every other relationship stays put
    ifcopenshell.api.run("geometry.unassign_representation", ifc_file, product=element, representation=body_rep)
    ifcopenshell.api.run("geometry.remove_representation", ifc_file, representation=body_rep)
    return parts


# --------------------------------------------------------------------------
# Updating the Blender scene without reloading the project


def create_blender_objects(elements):
    """Blender objects for the new parts (Bonsai's append-from-library
    pattern: a partial IfcImporter run on the already loaded file)."""
    import logging
    from bonsai.bim.ifc import IfcStore
    import bonsai.bim.import_ifc as import_ifc

    settings = import_ifc.IfcImportSettings.factory(bpy.context, IfcStore.path, logging.getLogger("ImportIFC"))
    importer = import_ifc.IfcImporter(settings)
    importer.file = tool.Ifc.get()
    importer.process_context_filter()
    importer.material_creator.load_existing_materials()
    importer.create_generic_elements(set(elements))
    importer.place_objects_in_collections()


def clear_parent_object(obj):
    """Empty mesh for the parent object (it remains as a linked container)."""
    obj.data = bpy.data.meshes.new(obj.data.name + "_container")


# --------------------------------------------------------------------------
# Main flow


def main():
    ifc_file = tool.Ifc.get()
    if ifc_file is None:
        return "No active IFC project in Bonsai."

    selected = [o for o in bpy.context.selected_objects if tool.Ifc.get_entity(o)]
    if not selected:
        return "No IFC object selected."

    created = []
    emptied = []
    registry = {}  # run-level shared IfcBuildingElementPartType per (source type, layer)
    split = skipped = 0

    for obj in selected:
        element = tool.Ifc.get_entity(obj)
        label = element.Name or element.GlobalId
        if element.IsDecomposedBy:
            print(f"{label}: already decomposed, skipping.")
            skipped += 1
            continue
        try:
            parts = process_element(ifc_file, element, obj, registry)
        except Exception as e:
            print(f"{label}: skipped - {e}.")
            skipped += 1
            continue
        if not parts:
            skipped += 1
            continue
        created.extend(parts)
        emptied.append(obj)
        split += 1
        print(f"{label}: split into {len(parts)} layer parts.")

    if created:
        try:
            create_blender_objects(created)
            for obj in emptied:
                clear_parent_object(obj)
            print(f"Created {len(created)} Blender objects on the fly.")
        except Exception as e:
            print(f"Scene update failed ({e}): reload the project to see the new objects.")

    if registry.get("_unsupported"):
        print("IfcBuildingElementPartType not in this schema (IFC2X3): parts left untyped.")
    return f"Split {split}, skipped {skipped}."


class SplitLayers(bpy.types.Operator, tool.Ifc.Operator):
    """Splits each selected layered IFC element (wall, slab, ...) into an
    aggregate of IfcBuildingElementPart layers. The original element is kept
    as the parent container: it keeps its GUID, property sets, openings and
    all IFC relationships (wall/wall, wall/slab, space boundaries, spatial
    containment, type), giving up only its layer-based Body geometry. Each
    part carries a single-layer material layer set usage as its single source
    of truth (name, thickness, material), and a Body extruded from that layer
    (thickness/offset/direction from the usage, footprint from the parent).
    Parts of same-type walls with the same build up share one
    IfcBuildingElementPartType. Selected objects with no material layer set,
    or already decomposed, are skipped and reported."""

    bl_idname = "bim.split_layers"
    bl_label = "Split Layers into parts"
    bl_options = {"REGISTER", "UNDO"}

    def _execute(self, context):
        self.report({"INFO"}, main())


# KNOWN LIMITATIONS
# - The part Body is the parametric extrusion of the single layer, not the
#   real shape of the original solid. For straight rectangular walls/slabs it
#   coincides with the source; for L-shaped footprints, mitred ends or
#   clipped/slanted tops the parts are boxed to the parent's bounding box on
#   the two perpendicular axes. This is the accepted price of "layer is the
#   only source of truth".
# - Bands come from the usage's OffsetFromReferenceLine; a body modelled off
#   its reference line yields bands offset from where the source solid sat.
# - Parametric editability of the parts is best-effort. Bonsai's layered
#   regenerators (recalculate_walls for LAYER2, slab change_thickness for
#   LAYER3) are gated by usage type, not element class, so an
#   IfcBuildingElementPart with a single-layer usage is engine-eligible; but
#   whether Bonsai's material panel exposes layer editing for a selected
#   IfcBuildingElementPart is not guaranteed. If full parametric behaviour is
#   required, the parts would need to be a class the wall/slab authoring
#   tools target (e.g. IfcWall/IfcSlab) — not decided here.
