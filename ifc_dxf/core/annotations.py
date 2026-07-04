"""Text and dimension annotation writing for DXF output."""

import math
import re

import numpy as np
import ifcopenshell.util.element
import ifcopenshell.util.selector

from .camera import world_matrix_col_major
from .ifc_query import _get_drawing_annotations, get_assigned_product, get_type_block_name
from .dxf_template import _ensure_dim_style


# Paper-space heights in mm, mirroring the Bonsai CSS class names.
# At export time: model_height [m] = paper_mm * 0.001 / scale_factor
# e.g. "regular" 2.5 mm at 1:100 -> 0.25 m in model space.
_TEXT_STYLES_MM = {
    "title":     7.0,
    "header":    5.0,
    "large":     3.5,
    "regular":   2.5,
    "small":     1.8,
    "DIMENSION": 1.8,
    "GRID":      3.5,
}
_DEFAULT_TEXT_MM = 2.5  # "regular"

# Bonsai stacks a tag's text literals as successive lines all placed at the
# same local (0, 0) -- line order/spacing is a rendering-time concern, not
# stored in IfcTextLiteralWithExtent.Placement. Each literal is shifted down
# (-Y) by index * (txt_height * this factor) to lay them out top-to-bottom.
_LINE_SPACING_FACTOR = 1.68

# Entities inside a BLOCK definition use BYBLOCK so the INSERT (on its own
# layer) controls colour, linetype and lineweight -- same convention as
# dxf_writer._BB and plan_symbols block content.
_BLOCK_CONTENT_ATTRIBS = {"layer": "0", "color": 0, "linetype": "BYBLOCK", "lineweight": -2}

# IFC BoxAlignment -> TEXT (halign, valign).
# Actual values (verified against bonsai's svgwriter.get_box_alignment_parameters):
# "top-left", "top-middle", "top-right", "middle-left", "center", "middle-right",
# "bottom-left", "bottom-middle", "bottom-right" -- note "middle" (not "center")
# in the top/bottom rows, but bare "center" for the middle row.
# halign: 0=left 1=center 2=right
# valign: 0=baseline 1=bottom 2=middle 3=top
_BOX_ALIGN_TO_TEXT = {
    "top-left":     (0, 3), "top-middle":    (1, 3), "top-right":    (2, 3),
    "middle-left":  (0, 2), "center":        (1, 2), "middle-right": (2, 2),
    "bottom-left":  (0, 1), "bottom-middle": (1, 1), "bottom-right": (2, 1),
}


def _annotation_polylines_2d(ann, cam_inv_np):
    """Extract 2D projected polylines from an IfcAnnotation.

    Returns list of polylines; each polyline is a list of (x, y) tuples
    in drawing (camera-projected) space.
    """
    if not ann.Representation:
        return []

    wm = world_matrix_col_major(ann)
    # project() = cam_inv @ world_point (translation + rotation).
    cam_inv = cam_inv_np

    def _world_to_2d(lx, ly):
        """Local 2D point -> world 3D -> camera 2D."""
        # Annotation geometry is in local XY (2D IfcCartesianPointList2D).
        # The ObjectPlacement places them in world space.
        wx = wm[0]*lx + wm[4]*ly + wm[12]
        wy = wm[1]*lx + wm[5]*ly + wm[13]
        wz = wm[2]*lx + wm[6]*ly + wm[14]
        # camera projection (orthographic): cam_inv @ (wx, wy, wz, 1)
        cx = cam_inv[0,0]*wx + cam_inv[0,1]*wy + cam_inv[0,2]*wz + cam_inv[0,3]
        cy = cam_inv[1,0]*wx + cam_inv[1,1]*wy + cam_inv[1,2]*wz + cam_inv[1,3]
        return (float(cx), float(cy))

    polylines = []
    for rep in ann.Representation.Representations:
        if rep.ContextOfItems.ContextIdentifier not in ("Annotation",):
            continue
        for item in rep.Items:
            if not item.is_a("IfcGeometricCurveSet"):
                continue
            for curve in item.Elements:
                if not curve.is_a("IfcIndexedPolyCurve"):
                    continue
                pt_list = curve.Points
                if not pt_list.is_a("IfcCartesianPointList2D"):
                    continue
                coords = pt_list.CoordList
                pts = [_world_to_2d(c[0], c[1]) for c in coords]
                if len(pts) >= 2:
                    polylines.append(pts)
    return polylines


def _mark_dim_annotative(dim_entity, doc):
    """Add AcadAnnotative XDATA to a DIMENSION entity.

    This is the minimum flag BricsCAD/AutoCAD need to treat the entity as
    annotative and display it at the correct paper size for the current scale.
    """
    from ezdxf.lldxf.types import DXFTag
    try:
        if "AcadAnnotative" not in doc.appids:
            doc.appids.new("AcadAnnotative")
        dim_entity.set_xdata("AcadAnnotative", [
            DXFTag(1000, "AnnotativeData"),
            DXFTag(1002, "{"),
            DXFTag(1070, 1),
            DXFTag(1070, 1),
            DXFTag(1002, "}"),
        ])
    except Exception:
        pass


def _write_dimension_annotations(msp, doc, annotations, cam_inv_np, scale_factor):
    """Write DIMENSION annotations as native DXF DIMENSION entities.

    Each IfcAnnotation(ObjectType='DIMENSION') stores a chain of 2D points
    (the Blender Curve spline). Each consecutive pair is one dimension segment:
    the stored points ARE the dimension-line endpoints (Bonsai places the
    annotation curve at the desired dimension line position, not at the
    measured-object edges).

    add_aligned_dim(p1, p2, distance=0) puts the dimension line exactly at
    p1-p2. Extension lines are zero-length (invisible). Text is auto-computed
    from the point distance unless overridden by BBIM_Dimension pset.
    """
    _DIM_LAYER = "IfcAnnotation_Dimension"
    dim_style_name = _ensure_dim_style(doc, scale_factor)
    # The template style is annotative (dimscale=0), which we leave untouched so
    # the template alone owns dimension appearance. But ezdxf bakes the dimension
    # picture block at creation time, and dimscale=0 would render it ~1:1 (text
    # and arrows ~100x too small in model space). Override dimscale per entity so
    # the baked geometry is paper-correct, without mutating the shared style.
    dim_scale = 1.0 / scale_factor   # e.g. 100 for 1:100

    for ann in annotations:
        if getattr(ann, "ObjectType", None) != "DIMENSION":
            continue

        ann_psets   = ifcopenshell.util.element.get_psets(ann)
        bbim_dim    = ann_psets.get("BBIM_Dimension", {})
        prefix      = bbim_dim.get("TextPrefix", "") or ""
        suffix      = bbim_dim.get("TextSuffix", "") or ""
        show_desc   = bbim_dim.get("ShowDescriptionOnly", False)
        description = getattr(ann, "Description", None) or ""

        polylines = _annotation_polylines_2d(ann, cam_inv_np)
        for pts in polylines:
            for i in range(len(pts) - 1):
                p0 = pts[i]
                p1 = pts[i + 1]
                dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                length = (dx*dx + dy*dy) ** 0.5
                if length < 1e-6:
                    continue

                if show_desc and description:
                    text = description
                elif prefix or suffix:
                    text = f"{prefix}{length:.3f}{suffix}"
                else:
                    text = "<>"   # let DXF auto-compute from geometry

                dim = msp.add_aligned_dim(
                    p1=p0,
                    p2=p1,
                    distance=0,
                    text=text,
                    dimstyle=dim_style_name,
                    override={"dimscale": dim_scale},
                    dxfattribs={"layer": _DIM_LAYER},
                )
                dim.render()
                _mark_dim_annotative(dim.dimension, doc)


def _make_text_annotative(doc, text_entity, insert_pt, scale_handle, angle_deg=0.0):
    """Add a single annotative scale representation to a TEXT entity.

    Creates the extension-dict chain:
      entity -> AcDbContextDataManager -> ACDB_ANNOTATIONSCALES -> *A1
    where *A1 is an ACDB_TEXTOBJECTCONTEXTDATA_CLASS referencing the given
    SCALE entity handle. Only one representation (current drawing scale) is
    written; BricsCAD/AutoCAD accept this and will add more when the user
    changes CANNOSCALE interactively.
    """
    from ezdxf.lldxf.types import DXFTag
    from ezdxf.lldxf.tags import Tags

    if text_entity.has_extension_dict:
        ext_dict = text_entity.get_extension_dict()
    else:
        ext_dict = text_entity.new_extension_dict()

    # ExtensionDict wraps the actual Dictionary object
    d = ext_dict.dictionary
    ctx_mgr     = d.add_new_dict("AcDbContextDataManager")
    anno_scales = ctx_mgr.add_new_dict("ACDB_ANNOTATIONSCALES")

    ctx = doc.objects.new_entity("ACDB_TEXTOBJECTCONTEXTDATA_CLASS", dxfattribs={})
    ctx.__class__ = type("CTX", (ctx.__class__,), {"DXFTYPE": "ACDB_TEXTOBJECTCONTEXTDATA_CLASS"})
    px, py = float(insert_pt[0]), float(insert_pt[1])
    ctx.xtags.subclasses = [Tags(), Tags([
        DXFTag(100, "AcDbObjectContextData"),
        DXFTag(70, 4),
        DXFTag(290, 1),   # 1 = active / current scale
    ]), Tags([
        DXFTag(100, "AcDbAnnotScaleObjectContextData"),
        DXFTag(340, scale_handle),
        DXFTag(70, 0),
        DXFTag(50, math.radians(angle_deg)),
        # Coordinates must be written as separate group codes (10/20/30),
        # not as a tuple -- DXFTagStorage writes raw tags without expansion.
        DXFTag(10, px), DXFTag(20, py), DXFTag(30, 0.0),
        DXFTag(11, 0.0), DXFTag(21, 0.0), DXFTag(31, 0.0),
    ])]
    anno_scales.add(key="*A1", entity=ctx)

    # BricsCAD/AutoCAD check for the AcadAnnotative XDATA block on the entity
    # to recognise it as annotative -- the extension dict alone is not enough.
    if "AcadAnnotative" not in doc.appids:
        doc.appids.new("AcadAnnotative")
    text_entity.set_xdata("AcadAnnotative", [
        DXFTag(1000, "AnnotativeData"),
        DXFTag(1002, "{"),
        DXFTag(1070, 1),
        DXFTag(1070, 1),
        DXFTag(1002, "}"),
    ])


_TEMPLATE_CMD_RE = re.compile(r"``.+?``")
_TEMPLATE_VAR_RE = re.compile(r"\{\{.*?\}\}")


def _resolve_text_literal_variables(text, product):
    """Resolve Bonsai's tag template syntax against product's attributes/psets.

    Mirrors tool.Drawing.replace_text_literal_variables (bonsai/tool/drawing.py):
    ``formula`` -> ifcopenshell.util.selector.format(formula, product)
    {{Attribute}} / {{Pset.Prop}} -> ifcopenshell.util.selector.get_element_value(...)
    Returns text unchanged if product is None (nothing to resolve against) --
    plain, non-templated text is a safe no-op here.
    """
    if not product:
        return text

    for command in re.findall(_TEMPLATE_CMD_RE, text):
        try:
            value = ifcopenshell.util.selector.format(command[2:-2], product)
        except Exception:
            value = ""
        text = text.replace(command, str(value))

    for variable in re.findall(_TEMPLATE_VAR_RE, text):
        try:
            value = ifcopenshell.util.selector.get_element_value(product, variable[2:-2])
        except Exception:
            value = None
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value)
        text = text.replace(variable, "" if value is None else str(value))

    return text


def _has_template_vars(text):
    """True if text contains Bonsai's ``formula`` or {{Attribute}} template syntax."""
    return bool(_TEMPLATE_CMD_RE.search(text) or _TEMPLATE_VAR_RE.search(text))


def _find_annotation_repr(ann):
    """Return (representation, ifc_type, mapping_op) for the "Annotation" context.

    Three cases, all observed in real Bonsai output:
    1. Plain/untyped text: own Representation, Items are IfcTextLiteralWithExtent
       directly -> (rep, None, None).
    2. Typed tag, geometry shared via IfcMappedItem (the common case: a space/
       door/window tag's own Representation.Items is [IfcMappedItem], pointing
       at the type's literal-containing representation) -> (type's rep, ifc_type,
       the IfcMappedItem's MappingTarget transform op).
    3. Typed tag with no own Representation at all -> falls back directly to the
       type's RepresentationMaps -> (type's rep, ifc_type, None).
    ifc_type is None only for case 1 (untyped).
    """
    own_rep = None
    if ann.Representation:
        for rep in ann.Representation.Representations:
            if getattr(rep.ContextOfItems, "ContextIdentifier", None) == "Annotation":
                own_rep = rep
                break

    if own_rep is not None:
        items = own_rep.Items
        if items and all(item.is_a("IfcMappedItem") for item in items):
            mapped_item = items[0]
            mapped_rep = mapped_item.MappingSource.MappedRepresentation
            ifc_type = ifcopenshell.util.element.get_type(ann)
            return mapped_rep, ifc_type, mapped_item.MappingTarget
        return own_rep, None, None

    ifc_type = ifcopenshell.util.element.get_type(ann)
    if ifc_type is not None:
        for rm in (getattr(ifc_type, "RepresentationMaps", None) or []):
            rep = rm.MappedRepresentation
            if getattr(rep.ContextOfItems, "ContextIdentifier", None) == "Annotation":
                return rep, ifc_type, None

    return None, None, None


def _literal_local_xy(item, mapping_op=None, y_line_offset=0.0):
    """Return (x, y) from IfcTextLiteralWithExtent.Placement, or (0, 0).

    y_line_offset shifts the literal's own local Y before mapping_op is
    applied (see _LINE_SPACING_FACTOR: Bonsai stacks a tag's literals as
    successive lines all placed at the same local point, so line order has
    to be reconstructed here instead of read from IFC).

    Applies mapping_op (an IfcCartesianTransformationOperator, from an
    IfcMappedItem wrapping the type's geometry) if given.
    """
    placement = getattr(item, "Placement", None)
    if placement is None or placement.Location is None:
        x, y = 0.0, 0.0
    else:
        c = placement.Location.Coordinates
        x, y = float(c[0]), float(c[1])
    y += y_line_offset

    if mapping_op is None:
        return x, y

    origin = mapping_op.LocalOrigin.Coordinates
    ox, oy = float(origin[0]), float(origin[1])
    scale = float(getattr(mapping_op, "Scale", None) or 1.0)
    if mapping_op.Axis1:
        d = mapping_op.Axis1.DirectionRatios
        xa = np.array([float(d[0]), float(d[1])])
    else:
        xa = np.array([1.0, 0.0])
    xn = np.linalg.norm(xa)
    xa = xa / xn if xn > 1e-9 else np.array([1.0, 0.0])
    ya = np.array([-xa[1], xa[0]])
    axis2 = getattr(mapping_op, "Axis2", None)
    if axis2:
        d = axis2.DirectionRatios
        ya_candidate = np.array([float(d[0]), float(d[1])])
        yn = np.linalg.norm(ya_candidate)
        if yn > 1e-9:
            ya = ya_candidate / yn

    nx = ox + (x * xa[0] + y * ya[0]) * scale
    ny = oy + (x * xa[1] + y * ya[1]) * scale
    return float(nx), float(ny)


def _write_text_annotations(msp, doc, annotations, cam_inv_np, scale_factor,
                            current_scale_handle):
    """Write TEXT annotations as annotative DXF TEXT entities, or as a shared
    BLOCK+ATTRIB (one ATTDEF per text literal) when the annotation shares its
    geometry with an IfcTypeProduct (e.g. a space tag: one BLOCK per tag type,
    one INSERT per tagged IfcSpace with resolved {{Name}}/{{Area}} values).

    Text height: paper_mm * 0.001 / scale_factor (model-space metres).
    Plain (untyped) TEXT entities get a single annotative scale representation
    for the current drawing scale via _make_text_annotative().
    """
    _TXT_LAYER = "IfcAnnotation_Text"
    seen_blocks = {}

    for ann in annotations:
        if getattr(ann, "ObjectType", None) != "TEXT":
            continue

        rep, ifc_type, mapping_op = _find_annotation_repr(ann)
        if rep is None:
            continue

        # style class -> paper height
        classes_str = (
            ifcopenshell.util.element.get_pset(ann, "EPset_Annotation", "Classes") or ""
        )
        style = next(
            (c for c in classes_str.split() if c in _TEXT_STYLES_MM), "regular"
        )
        paper_mm   = _TEXT_STYLES_MM[style]
        txt_height = paper_mm * 0.001 / scale_factor   # model-space metres

        # world position -> 2D
        wm = world_matrix_col_major(ann)
        ci = cam_inv_np

        def _world_to_2d(lx, ly):
            wx = wm[0]*lx + wm[4]*ly + wm[12]
            wy = wm[1]*lx + wm[5]*ly + wm[13]
            wz = wm[2]*lx + wm[6]*ly + wm[14]
            cx = ci[0,0]*wx + ci[0,1]*wy + ci[0,2]*wz + ci[0,3]
            cy = ci[1,0]*wx + ci[1,1]*wy + ci[1,2]*wz + ci[1,3]
            return float(cx), float(cy)

        # rotation from ObjectPlacement X-axis
        ox, oy = _world_to_2d(0.0, 0.0)
        x1, y1 = _world_to_2d(1.0, 0.0)
        angle_deg = float(np.degrees(np.arctan2(y1 - oy, x1 - ox)))

        literals = [item for item in rep.Items if item.is_a("IfcTextLiteralWithExtent")]

        line_height = txt_height * _LINE_SPACING_FACTOR

        if ifc_type is None:
            # Plain, untyped text -- one DXF TEXT entity per literal, stacked
            # top-to-bottom by index (see _LINE_SPACING_FACTOR).
            for i, item in enumerate(literals):
                lx, ly = _literal_local_xy(item, mapping_op, y_line_offset=-i * line_height)
                px, py = _world_to_2d(lx, ly)
                halign, valign = _BOX_ALIGN_TO_TEXT.get(
                    getattr(item, "BoxAlignment", None), (0, 0)
                )
                text_dxfattribs = {
                    "layer":    _TXT_LAYER,
                    "style":    style,
                    "height":   txt_height,
                    "rotation": angle_deg,
                    "insert":   (px, py),
                    "halign":   halign,
                    "valign":   valign,
                }
                if halign or valign:
                    text_dxfattribs["align_point"] = (px, py)
                text_entity = msp.add_text(item.Literal or "", dxfattribs=text_dxfattribs)
                if current_scale_handle:
                    _make_text_annotative(doc, text_entity, (px, py),
                                          current_scale_handle, angle_deg)
            continue

        # Typed tag (e.g. space tag): shared BLOCK with one ATTDEF per literal
        # that has {{...}}/``...`` template syntax (raw template as default
        # text; resolved per-instance below). Literals with no template vars
        # are static across every instance of the type, so they're baked into
        # the block as plain TEXT instead of an attribute.
        _, block_name = get_type_block_name(ann)
        if block_name not in seen_blocks:
            blk = doc.blocks.new(name=block_name)
            for i, item in enumerate(literals):
                lx, ly = _literal_local_xy(item, mapping_op, y_line_offset=-i * line_height)
                halign, valign = _BOX_ALIGN_TO_TEXT.get(
                    getattr(item, "BoxAlignment", None), (0, 0)
                )
                content_attribs = {
                    **_BLOCK_CONTENT_ATTRIBS,
                    "style":  style,
                    "height": txt_height,
                    "halign": halign,
                    "valign": valign,
                }
                if halign or valign:
                    content_attribs["align_point"] = (lx, ly)

                literal_text = item.Literal or ""
                if _has_template_vars(literal_text):
                    blk.add_attdef(
                        tag=f"LITERAL_{i}",
                        insert=(lx, ly),
                        text=literal_text,
                        dxfattribs=content_attribs,
                    )
                else:
                    blk.add_text(literal_text, dxfattribs={
                        **content_attribs, "insert": (lx, ly),
                    })
            seen_blocks[block_name] = True

        product = get_assigned_product(ann)
        px, py = _world_to_2d(0.0, 0.0)
        blockref = msp.add_blockref(block_name, (px, py), dxfattribs={
            "layer":    _TXT_LAYER,
            "rotation": angle_deg,
        })
        attribs = {
            f"LITERAL_{i}": _resolve_text_literal_variables(item.Literal or "", product)
            for i, item in enumerate(literals)
            if _has_template_vars(item.Literal or "")
        }
        if attribs:
            blockref.add_auto_attribs(attribs)
