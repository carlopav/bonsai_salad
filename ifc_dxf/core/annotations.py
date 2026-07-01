"""Text and dimension annotation writing for DXF output."""

import math

import numpy as np
import ifcopenshell.util.element

from .camera import world_matrix_col_major
from .ifc_query import _get_drawing_annotations
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

# IFC BoxAlignment -> TEXT (halign, valign)
# halign: 0=left 1=center 2=right
# valign: 0=baseline 1=bottom 2=middle 3=top
_BOX_ALIGN_TO_TEXT = {
    "top-left":     (0, 3), "top-center":    (1, 3), "top-right":    (2, 3),
    "middle-left":  (0, 2), "center":        (1, 2), "middle-right": (2, 2),
    "bottom-left":  (0, 1), "bottom-center": (1, 1), "bottom-right": (2, 1),
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


def _write_text_annotations(msp, doc, annotations, cam_inv_np, scale_factor,
                            current_scale_handle):
    """Write TEXT annotations as annotative DXF TEXT entities.

    Text height: paper_mm * 0.001 / scale_factor (model-space metres).
    Each entity gets a single annotative scale representation for the current
    drawing scale via _make_text_annotative().
    """
    _TXT_LAYER = "IfcAnnotation_Text"

    for ann in annotations:
        if getattr(ann, "ObjectType", None) != "TEXT":
            continue
        if not ann.Representation:
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

        # text content + alignment from IfcTextLiteralWithExtent
        for rep in ann.Representation.Representations:
            if rep.ContextOfItems.ContextIdentifier != "Annotation":
                continue
            for item in rep.Items:
                if not item.is_a("IfcTextLiteralWithExtent"):
                    continue

                literal = item.Literal or ""
                px, py  = _world_to_2d(0.0, 0.0)

                # Use left-baseline (DXF TEXT default) -- matches what BricsCAD
                # writes for native annotative text and avoids align_point issues.
                # BoxAlignment from IFC is ignored for DXF (SVG-only concept).
                text = msp.add_text(literal, dxfattribs={
                    "layer":    _TXT_LAYER,
                    "style":    style,
                    "height":   txt_height,
                    "rotation": angle_deg,
                    "insert":   (px, py),
                })

                if current_scale_handle:
                    _make_text_annotative(doc, text, (px, py),
                                          current_scale_handle, angle_deg)
