# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""Main DXF -> IFC representation import pipeline (pure Python, no bpy)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

import ezdxf
from ezdxf.upright import upright
import ifcopenshell
import ifcopenshell.api
import ifcopenshell.util.representation
import ifcopenshell.util.unit

from . import mapping as _mapping
from .converter import angle_unit_scale, dxf_entity_to_ifc
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
# Shared pipeline pieces
# ---------------------------------------------------------------------------

def _open_source(model, dxf_path, source_block, source_layer):
    """Return (doc, entity_source, scale, angle_scale) for a DXF import."""
    doc = ezdxf.readfile(str(dxf_path))
    # _dxf_scale gives metres per DXF unit; the IFC stores lengths in the
    # project's own unit, so divide that out or a mm project lands 1000x small.
    project_scale = ifcopenshell.util.unit.calculate_unit_scale(model) or 1.0
    scale = _dxf_scale(doc) / project_scale
    angle_scale = angle_unit_scale(model)
    if source_block:
        if source_block not in doc.blocks:
            raise ValueError(f"Block '{source_block}' not found in {Path(dxf_path).name}")
        msp = doc.blocks[source_block]
    else:
        msp = doc.modelspace()
    return doc, msp, scale, angle_scale, project_scale


# A block referencing a block referencing a block is ordinary; a block that
# reaches itself is not, and only a malformed file does it.
_MAX_BLOCK_DEPTH = 16


def _expand(entity, depth: int = 0):
    """
    Yield the drawable entities of *entity*, expanding nested INSERTs.

    ezdxf's virtual_entities() goes one level down: a block holding another
    block hands back the inner INSERT, which has no converter and would be
    dropped silently, taking everything inside it with it.

    A mirrored INSERT (negative scale) expands into entities whose coordinates
    are given in an inverted OCS, extrusion (0, 0, -1). Read raw they land
    mirrored about the Y axis, so each one is flipped upright. Other extrusions
    are left alone by ezdxf.
    """
    if entity.dxftype() == "INSERT":
        if depth >= _MAX_BLOCK_DEPTH:
            return
        try:
            children = list(entity.virtual_entities())
        except Exception:
            return
        for child in children:
            yield from _expand(child, depth + 1)
        return
    upright(entity)
    yield entity


def _group_by_layer(msp, skip_layers, only_layer) -> dict:
    """Layer name -> entities to convert, with blocks expanded inline."""
    layer_entities: dict[str, list] = defaultdict(list)

    for entity in msp:
        for leaf in _expand(entity):
            layer_name = leaf.dxf.layer
            if layer_name in skip_layers:
                continue
            if only_layer and layer_name != only_layer:
                continue
            layer_entities[layer_name].append(leaf)
    return layer_entities


def _layer_curve_set(model, doc, layer_name, entities, scale, angle_scale, project_scale):
    """
    Convert one layer's entities into a single IfcGeometricCurveSet and give it
    an IfcPresentationLayerWithStyle. Returns None if nothing converted.
    """
    ifc_items = []
    for entity in entities:
        item = dxf_entity_to_ifc(model, entity, scale, angle_scale)
        if item is None:
            continue
        if item.is_a("IfcGeometricCurveSet"):
            ifc_items.extend(item.Elements)
        else:
            ifc_items.append(item)

    if not ifc_items:
        return None

    curve_set = model.createIfcGeometricCurveSet(Elements=ifc_items)

    dxf_layer = doc.layers.get(layer_name) if layer_name in doc.layers else None
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
    # lineweight_to_mm returns mm; CurveWidth is a length in project units.
    lw = lineweight_to_mm(lineweight_raw) * 1e-3 / project_scale
    colour = layer_colour(model, dxf_layer) if dxf_layer else model.createIfcColourRgb(None, 1.0, 1.0, 1.0)

    curve_style = model.createIfcCurveStyle(
        Name=layer_name,
        CurveFont=font,
        CurveColour=colour,
        CurveWidth=model.createIfcPositiveLengthMeasure(lw) if lw else None,
    )
    model.createIfcPresentationLayerWithStyle(
        Name=layer_name,
        AssignedItems=[curve_set],
        LayerOn=True,
        LayerFrozen=False,
        LayerBlocked=False,
        LayerStyles=[model.createIfcPresentationStyleAssignment([curve_style])],
    )
    return curve_set


def _assign_representation(model, element, subcontext, items):
    """Build an IfcShapeRepresentation from *items* and attach it to *element*."""
    new_repr = model.createIfcShapeRepresentation(
        ContextOfItems=subcontext,
        RepresentationIdentifier="Annotation",
        RepresentationType="GeometricCurveSet",
        Items=list(items),
    )
    pds = element.Representation
    if pds is None:
        element.Representation = model.createIfcProductDefinitionShape(Representations=[new_repr])
    else:
        pds.Representations = list(pds.Representations) + [new_repr]
    return new_repr


def _drop_existing(model, element, subcontext):
    """Remove any representation *element* already has in *subcontext*."""
    if element.Representation:
        pds = element.Representation
        pds.Representations = [r for r in pds.Representations if r.ContextOfItems != subcontext]


def _empty_reason(dxf_path, source_block, source_layer) -> str:
    """Say which filter emptied the import, rather than blaming the file."""
    where = []
    if source_block:
        where.append(f"block '{source_block}'")
    if source_layer:
        where.append(f"layer '{source_layer}'")
    if where:
        return (f"No geometry in {' and '.join(where)} of {Path(dxf_path).name}. "
                f"Blocks are expanded on import, so a layer holding only block "
                f"references imports as nothing; its contents come in under "
                f"their own layers.")
    return f"No geometry found in {Path(dxf_path).name}"


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
    doc, msp, scale, angle_scale, project_scale = _open_source(
        model, dxf_path, source_block, source_layer)

    if subcontext is None:
        subcontext = get_or_create_subcontext(model)

    _skip = set(skip_layers or []) | {"DEFPOINTS"}
    layer_entities = _group_by_layer(msp, _skip, source_layer or None)

    _drop_existing(model, element, subcontext)

    all_items = []
    for layer_name, entities in layer_entities.items():
        curve_set = _layer_curve_set(
            model, doc, layer_name, entities, scale, angle_scale, project_scale)
        if curve_set is not None:
            all_items.append(curve_set)

    if not all_items:
        raise ValueError(_empty_reason(dxf_path, source_block, source_layer))

    return _assign_representation(model, element, subcontext, all_items)


def import_dxf_as_elements(
    model: ifcopenshell.file,
    dxf_path: Path,
    create_element,
    subcontext=None,
    *,
    mapping_rules: Optional[list] = None,
    skip_layers: Optional[list[str]] = None,
    source_block: Optional[str] = None,
    source_layer: Optional[str] = None,
) -> list[tuple]:
    """
    Parse *dxf_path* and build one IFC element per DXF layer, classed by the
    mapping rules, each carrying that layer's linework.

    Parameters
    ----------
    model          : open ifcopenshell.file
    dxf_path       : path to the DXF file
    create_element : callback (layer_name, ifc_class, predefined_type, name) ->
                     IFC product, or None to skip the layer. The caller owns
                     element creation so this module stays free of bpy; in
                     Blender it goes through Bonsai so the object is linked.
    subcontext     : IfcGeometricRepresentationSubContext; created if None
    mapping_rules  : rules from dxf_ifc.core.mapping.load_mapping; when None
                     every layer falls back to mapping.FALLBACK_CLASS
    skip_layers    : layer names to ignore (DEFPOINTS always is)
    source_block   : if set, read entities from this block definition
    source_layer   : if set, read only this layer

    Returns
    -------
    A list of (layer_name, element, representation) in descending entity count,
    covering only the layers that produced geometry.
    """
    dxf_path = Path(dxf_path)
    doc, msp, scale, angle_scale, project_scale = _open_source(
        model, dxf_path, source_block, source_layer)

    if subcontext is None:
        subcontext = get_or_create_subcontext(model)

    _skip = set(skip_layers or []) | {"DEFPOINTS"}
    layer_entities = _group_by_layer(msp, _skip, source_layer or None)
    if not layer_entities:
        raise ValueError(_empty_reason(dxf_path, source_block, source_layer))

    rules = mapping_rules or []
    results = []
    # Busiest layer first, so a partial result is the useful part of the drawing.
    for layer_name, entities in sorted(layer_entities.items(), key=lambda kv: -len(kv[1])):
        curve_set = _layer_curve_set(
            model, doc, layer_name, entities, scale, angle_scale, project_scale)
        if curve_set is None:
            continue
        rule = _mapping.resolve(layer_name, rules)
        element = create_element(
            layer_name, rule.ifc_class, rule.predefined_type,
            _mapping.element_name(layer_name, rule),
        )
        if element is None:
            continue
        _drop_existing(model, element, subcontext)
        results.append((layer_name, element,
                        _assign_representation(model, element, subcontext, [curve_set])))

    if not results:
        raise ValueError(_empty_reason(dxf_path, source_block, source_layer))
    return results
