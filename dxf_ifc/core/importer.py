# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""Main DXF -> IFC representation import pipeline (pure Python, no bpy)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

import ezdxf
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.representation

from .converter import dxf_entity_to_ifc
from .styles import (
    layer_colour,
    lineweight_to_mm,
    linetype_to_ifc_font,
)


# ---------------------------------------------------------------------------
# Unit scale detection
# ---------------------------------------------------------------------------

_INSUNITS_TO_METERS: dict[int, float] = {
    0:  1.0,
    1:  0.0254,
    2:  0.3048,
    4:  1e-3,
    5:  1e-2,
    6:  1.0,
    7:  1e3,
    14: 1e-6,
    15: 1e-2,
    17: 1e6,
}


def _dxf_scale(doc) -> float:
    try:
        insunits = doc.header.get("$INSUNITS", 4)
        return _INSUNITS_TO_METERS.get(insunits, 1e-3)
    except Exception:
        return 1e-3


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
        parent = ifcopenshell.api.run("context.add_context", model, context_type="Plan")

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
# Main pipeline
# ---------------------------------------------------------------------------

def import_dxf_as_representation(
    model: ifcopenshell.file,
    element,
    dxf_path: Path,
    subcontext=None,
    *,
    skip_layers: Optional[list[str]] = None,
    source_block: Optional[str] = None,
    source_layer: Optional[str] = None,
) -> object:
    """
    Parse *dxf_path* and assign its geometry as an IFC representation on *element*.

    Parameters
    ----------
    model        : open ifcopenshell.file
    element      : target IFC product (already exists in model)
    dxf_path     : path to the DXF file
    subcontext   : IfcGeometricRepresentationSubContext; created if None
    skip_layers  : layer names to ignore (e.g. ["DEFPOINTS"])
    source_block : if set, import only entities from this block definition
    source_layer : if set, import only entities on this layer (from modelspace)

    Returns
    -------
    The new IfcShapeRepresentation.
    """
    dxf_path = Path(dxf_path)
    doc = ezdxf.readfile(str(dxf_path))
    scale = _dxf_scale(doc)

    if source_block:
        if source_block not in doc.blocks:
            raise ValueError(f"Block '{source_block}' not found in {dxf_path.name}")
        msp = doc.blocks[source_block]
    else:
        msp = doc.modelspace()

    if subcontext is None:
        subcontext = get_or_create_subcontext(model)

    _skip = set(skip_layers or []) | {"DEFPOINTS"}
    _only_layer = source_layer or None

    # 1. Remove any existing representation in this subcontext directly
    if element.Representation:
        pds = element.Representation
        pds.Representations = [r for r in pds.Representations if r.ContextOfItems != subcontext]

    # 2. Group modelspace entities by layer, expanding INSERTs inline
    layer_entities: dict[str, list] = defaultdict(list)

    for entity in msp:
        if entity.dxftype() == "INSERT":
            # Expand block to world-space entities via ezdxf
            for virtual in entity.virtual_entities():
                layer_name = virtual.dxf.layer
                if layer_name in _skip:
                    continue
                if _only_layer and layer_name != _only_layer:
                    continue
                layer_entities[layer_name].append(virtual)
        else:
            layer_name = entity.dxf.layer
            if layer_name in _skip:
                continue
            if _only_layer and layer_name != _only_layer:
                continue
            layer_entities[layer_name].append(entity)

    all_items: list = []

    # 4. Per-layer geometry + style
    for layer_name, entities in layer_entities.items():
        ifc_items = []
        for entity in entities:
            item = dxf_entity_to_ifc(model, entity, scale)
            if item is None:
                continue
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
        if dxf_layer is not None:
            try:
                linetype_name = dxf_layer.dxf.linetype or "CONTINUOUS"
            except Exception:
                pass
            try:
                lineweight_raw = dxf_layer.dxf.lineweight
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

        model.createIfcPresentationLayerWithStyle(
            Name=layer_name,
            AssignedItems=[curve_set],
            LayerOn=True,
            LayerFrozen=False,
            LayerBlocked=False,
            LayerStyles=[model.createIfcPresentationStyleAssignment([curve_style])],
        )

    if not all_items:
        raise ValueError(f"No geometry found in {dxf_path.name}")

    # 6. Build ShapeRepresentation and assign directly (avoids API wrapping)
    new_repr = model.createIfcShapeRepresentation(
        ContextOfItems=subcontext,
        RepresentationIdentifier="Annotation",
        RepresentationType="GeometricCurveSet",
        Items=all_items,
    )

    pds = element.Representation
    if pds is None:
        pds = model.createIfcProductDefinitionShape(Representations=[new_repr])
        element.Representation = pds
    else:
        pds.Representations = list(pds.Representations) + [new_repr]

    return new_repr
