# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""Main DXF → IFC representation import pipeline (pure Python, no bpy)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import ezdxf
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.representation

from .converter import (
    dxf_entity_to_ifc,
    block_to_representation_map,
    insert_to_mapped_item,
)
from .styles import (
    layer_colour,
    lineweight_to_mm,
    linetype_to_ifc_font,
)


# ---------------------------------------------------------------------------
# Unit scale detection
# ---------------------------------------------------------------------------

_INSUNITS_TO_METERS: dict[int, float] = {
    0:  1.0,       # unitless → assume metres
    1:  0.0254,    # inches
    2:  0.3048,    # feet
    4:  1e-3,      # mm
    5:  1e-2,      # cm
    6:  1.0,       # metres
    7:  1e3,       # km
    14: 1e-6,      # microns
    15: 1e-2,      # decimetres
    17: 1e6,       # megametres
}


def _dxf_scale(doc) -> float:
    """Return scale factor to convert DXF document units → metres."""
    try:
        insunits = doc.header.get("$INSUNITS", 4)
        return _INSUNITS_TO_METERS.get(insunits, 1e-3)
    except Exception:
        return 1e-3  # assume mm if header unreadable


# ---------------------------------------------------------------------------
# Subcontext helper
# ---------------------------------------------------------------------------

def get_or_create_subcontext(
    model: ifcopenshell.file,
    context_identifier: str = "Plan",
    target_view: str = "PLAN_VIEW",
    subcontext_identifier: str = "Annotation",
):
    """Return existing subcontext or create it under the matching parent context."""
    parent = ifcopenshell.util.representation.get_context(model, context_identifier)
    if parent is None:
        parent = ifcopenshell.api.run(
            "context.add_context",
            model,
            context_type="Plan",
        )

    for sub in model.by_type("IfcGeometricRepresentationSubContext"):
        if (
            sub.ContextIdentifier == subcontext_identifier
            and sub.TargetView == target_view
            and sub.ParentContext == parent
        ):
            return sub

    return ifcopenshell.api.run(
        "context.add_context",
        model,
        context_type="Plan",
        context_identifier=subcontext_identifier,
        target_view=target_view,
        parent=parent,
    )


# ---------------------------------------------------------------------------
# Pset helper (optional)
# ---------------------------------------------------------------------------

def _write_pset(
    model: ifcopenshell.file,
    element,
    dxf_path: Path,
    layer_name: str,
    linetype_name: str,
    aci_index: int,
    lineweight_raw: int,
):
    import ifcopenshell.api
    pset = ifcopenshell.api.run("pset.add_pset", model, product=element, name="Pset_DXFSource")
    ifcopenshell.api.run(
        "pset.edit_pset",
        model,
        pset=pset,
        properties={
            "DXF_SourceFile":  str(dxf_path),
            "DXF_Layer":       layer_name,
            "DXF_Linetype":    linetype_name,
            "DXF_Color":       str(aci_index),
            "DXF_Lineweight":  str(lineweight_raw),
            "DXF_ImportDate":  datetime.now().isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def import_dxf_as_representation(
    model: ifcopenshell.file,
    element,
    dxf_path: Path,
    subcontext=None,
    *,
    write_pset: bool = False,
    skip_layers: Optional[list[str]] = None,
) -> object:
    """
    Parse *dxf_path* and assign its geometry as an IFC representation on *element*.

    Parameters
    ----------
    model       : open ifcopenshell.file
    element     : target IFC product (already exists in model)
    dxf_path    : path to the DXF file
    subcontext  : IfcGeometricRepresentationSubContext; created if None
    write_pset  : if True, attach Pset_DXFSource to element with DXF metadata
    skip_layers : layer names to ignore (e.g. ["DEFPOINTS"])

    Returns
    -------
    The new IfcShapeRepresentation.
    """
    dxf_path = Path(dxf_path)
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    scale = _dxf_scale(doc)

    if subcontext is None:
        subcontext = get_or_create_subcontext(model)

    _skip = set(skip_layers or []) | {"DEFPOINTS"}

    # 1. Remove any existing representation in this subcontext
    if element.Representation:
        to_remove = [
            r for r in element.Representation.Representations
            if r.ContextOfItems == subcontext
        ]
        for r in to_remove:
            ifcopenshell.api.run(
                "geometry.remove_representation",
                model,
                product=element,
                representation=r,
            )

    # 2. Pre-build block definitions → IfcRepresentationMap
    block_maps: dict[str, object] = {}
    for block_def in doc.blocks:
        block_name = block_def.name
        if block_name.startswith("*"):
            continue
        repr_map = block_to_representation_map(model, block_def, subcontext, scale)
        if repr_map is not None:
            block_maps[block_name] = repr_map

    # 3. Group modelspace entities by layer
    layer_entities: dict[str, list] = defaultdict(list)
    insert_entities: list = []

    for entity in msp:
        layer_name = entity.dxf.layer
        if layer_name in _skip:
            continue
        if entity.dxftype() == "INSERT":
            insert_entities.append(entity)
        else:
            layer_entities[layer_name].append(entity)

    all_items: list = []
    ifc_layers: list = []

    # 4. Per-layer geometry + style
    for layer_name, entities in layer_entities.items():
        ifc_items = []
        for entity in entities:
            item = dxf_entity_to_ifc(model, entity, scale)
            if item is not None:
                # IfcGeometricCurveSet returned by lwpolyline multi-segment
                if item.is_a("IfcGeometricCurveSet"):
                    ifc_items.extend(item.Elements)
                else:
                    ifc_items.append(item)

        if not ifc_items:
            continue

        curve_set = model.createIfcGeometricCurveSet(Elements=ifc_items)
        all_items.append(curve_set)

        dxf_layer = doc.layers.get(layer_name)
        linetype_name = "CONTINUOUS"
        lineweight_raw = -3
        aci_index = 7
        if dxf_layer is not None:
            try:
                linetype_name = dxf_layer.dxf.linetype or "CONTINUOUS"
            except Exception:
                pass
            try:
                lineweight_raw = dxf_layer.dxf.lineweight
            except Exception:
                pass
            try:
                aci_index = dxf_layer.color
            except Exception:
                pass

        font = linetype_to_ifc_font(model, linetype_name)
        lw_mm = lineweight_to_mm(lineweight_raw)
        colour = layer_colour(model, dxf_layer) if dxf_layer else model.createIfcColourRgb(None, 1.0, 1.0, 1.0)

        curve_style = model.createIfcCurveStyle(
            Name=layer_name,
            CurveFont=font,
            CurveColour=colour,
            CurveWidth=model.createIfcPositiveLengthMeasure(lw_mm) if lw_mm else None,
        )

        ifc_layer = model.createIfcPresentationLayerWithStyle(
            Name=layer_name,
            AssignedItems=[curve_set],
            LayerOn=True,
            LayerFrozen=False,
            LayerBlocked=False,
            LayerStyles=[model.createIfcPresentationStyleAssignment([curve_style])],
        )
        ifc_layers.append(ifc_layer)

        if write_pset:
            _write_pset(
                model, element, dxf_path,
                layer_name, linetype_name, aci_index, lineweight_raw,
            )

    # 5. INSERT entities → IfcMappedItem
    for insert in insert_entities:
        block_name = insert.dxf.name
        repr_map = block_maps.get(block_name)
        if repr_map is None:
            continue
        mapped_item = insert_to_mapped_item(model, insert, repr_map, scale)
        all_items.append(mapped_item)

    if not all_items:
        raise ValueError(f"No geometry found in {dxf_path.name}")

    # 6. Build ShapeRepresentation and assign
    new_repr = model.createIfcShapeRepresentation(
        ContextOfItems=subcontext,
        RepresentationIdentifier="Annotation",
        RepresentationType="GeometricCurveSet",
        Items=all_items,
    )

    ifcopenshell.api.run(
        "geometry.assign_representation",
        model,
        product=element,
        representation=new_repr,
    )

    return new_repr
