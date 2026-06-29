# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""DXF entity → IFC geometry conversion (pure Python, no bpy)."""

from __future__ import annotations

import math
from typing import Optional

import ifcopenshell


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _pt2(model: ifcopenshell.file, x: float, y: float, scale: float = 1.0):
    return model.createIfcCartesianPoint([x * scale, y * scale])


def _axis2d(model: ifcopenshell.file, cx: float, cy: float, scale: float = 1.0):
    return model.createIfcAxis2Placement2D(
        Location=_pt2(model, cx, cy, scale)
    )


# ---------------------------------------------------------------------------
# Bulge → arc helper
# ---------------------------------------------------------------------------

def bulge_to_arc(p1: tuple, p2: tuple, bulge: float) -> tuple:
    """Return (center_xy, radius, start_angle_rad, end_angle_rad)."""
    d = math.dist(p1, p2)
    r = d * (1 + bulge ** 2) / (4 * abs(bulge))
    alpha = 2 * math.atan(bulge)
    mid_angle = math.atan2(p2[1] - p1[1], p2[0] - p1[0]) - math.pi / 2
    cx = (p1[0] + p2[0]) / 2 - r * math.cos(mid_angle) * math.copysign(1, bulge)
    cy = (p1[1] + p2[1]) / 2 - r * math.sin(mid_angle) * math.copysign(1, bulge)
    start = math.atan2(p1[1] - cy, p1[0] - cx)
    end = math.atan2(p2[1] - cy, p2[0] - cx)
    return (cx, cy), r, start, end


# ---------------------------------------------------------------------------
# Entity converters
# ---------------------------------------------------------------------------

def _line_to_ifc(model: ifcopenshell.file, entity, scale: float):
    p1 = entity.dxf.start
    p2 = entity.dxf.end
    return model.createIfcPolyline([
        _pt2(model, p1.x, p1.y, scale),
        _pt2(model, p2.x, p2.y, scale),
    ])


def _lwpolyline_to_ifc(model: ifcopenshell.file, entity, scale: float):
    """LWPOLYLINE → list of IfcPolyline / IfcTrimmedCurve items."""
    points = list(entity.get_points("xyb"))
    if not points:
        return None

    items = []
    n = len(points)
    closed = entity.closed

    for i in range(n):
        x1, y1, bulge = points[i]
        x2, y2, _ = points[(i + 1) % n]

        if i == n - 1 and not closed:
            break

        if abs(bulge) < 1e-9:
            items.append(model.createIfcPolyline([
                _pt2(model, x1, y1, scale),
                _pt2(model, x2, y2, scale),
            ]))
        else:
            (cx, cy), r, start, end = bulge_to_arc((x1, y1), (x2, y2), bulge)
            circle = model.createIfcCircle(
                Position=_axis2d(model, cx, cy, scale),
                Radius=r * scale,
            )
            t1 = model.createIfcParameterValue(math.degrees(start))
            t2 = model.createIfcParameterValue(math.degrees(end))
            items.append(model.createIfcTrimmedCurve(
                BasisCurve=circle,
                Trim1=[t1],
                Trim2=[t2],
                SenseAgreement=bulge > 0,
                MasterRepresentation="PARAMETER",
            ))

    if not items:
        return None
    if len(items) == 1:
        return items[0]
    # Wrap multiple segments in a GeometricCurveSet
    return model.createIfcGeometricCurveSet(Elements=items)


def _polyline_to_ifc(model: ifcopenshell.file, entity, scale: float):
    pts = [v.dxf.location for v in entity.vertices]
    if len(pts) < 2:
        return None
    return model.createIfcPolyline([_pt2(model, p.x, p.y, scale) for p in pts])


def _arc_to_ifc(model: ifcopenshell.file, entity, scale: float):
    c = entity.dxf.center
    r = entity.dxf.radius * scale
    start_deg = entity.dxf.start_angle
    end_deg = entity.dxf.end_angle
    circle = model.createIfcCircle(
        Position=_axis2d(model, c.x, c.y, scale),
        Radius=r,
    )
    return model.createIfcTrimmedCurve(
        BasisCurve=circle,
        Trim1=[model.createIfcParameterValue(start_deg)],
        Trim2=[model.createIfcParameterValue(end_deg)],
        SenseAgreement=True,
        MasterRepresentation="PARAMETER",
    )


def _circle_to_ifc(model: ifcopenshell.file, entity, scale: float):
    c = entity.dxf.center
    r = entity.dxf.radius * scale
    return model.createIfcCircle(
        Position=_axis2d(model, c.x, c.y, scale),
        Radius=r,
    )


def _ellipse_to_ifc(model: ifcopenshell.file, entity, scale: float):
    c = entity.dxf.center
    major = entity.dxf.major_axis
    semi_major = math.sqrt(major.x ** 2 + major.y ** 2) * scale
    semi_minor = semi_major * entity.dxf.ratio
    angle = math.degrees(math.atan2(major.y, major.x))
    ref_dir = model.createIfcDirection([math.cos(math.radians(angle)), math.sin(math.radians(angle))])
    placement = model.createIfcAxis2Placement2D(
        Location=_pt2(model, c.x, c.y, scale),
        RefDirection=ref_dir,
    )
    return model.createIfcEllipse(
        Position=placement,
        SemiAxis1=semi_major,
        SemiAxis2=semi_minor,
    )


def _spline_to_ifc(model: ifcopenshell.file, entity, scale: float):
    """Approximate spline as polyline from control points."""
    try:
        pts = list(entity.control_points)
    except Exception:
        return None
    if len(pts) < 2:
        return None
    return model.createIfcPolyline([_pt2(model, p[0], p[1], scale) for p in pts])


def _hatch_to_ifc(model: ifcopenshell.file, entity, scale: float):
    """Extract outer boundary loops of a HATCH as IfcPolyline items."""
    items = []
    try:
        for path in entity.paths:
            pts = []
            for edge in path.edges:
                if hasattr(edge, "start"):
                    pts.append(edge.start)
            if len(pts) >= 2:
                ifc_pts = [_pt2(model, p[0], p[1], scale) for p in pts]
                ifc_pts.append(ifc_pts[0])  # close loop
                items.append(model.createIfcPolyline(ifc_pts))
    except Exception:
        pass
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    return model.createIfcGeometricCurveSet(Elements=items)


def _text_to_ifc(model: ifcopenshell.file, entity, scale: float):
    try:
        text = entity.dxf.text
        insert = entity.dxf.insert
        placement = model.createIfcAxis2Placement2D(
            Location=_pt2(model, insert.x, insert.y, scale)
        )
        return model.createIfcTextLiteral(
            Literal=text,
            Placement=placement,
            Path="RIGHT",
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_CONVERTERS = {
    "LINE":       _line_to_ifc,
    "LWPOLYLINE": _lwpolyline_to_ifc,
    "POLYLINE":   _polyline_to_ifc,
    "ARC":        _arc_to_ifc,
    "CIRCLE":     _circle_to_ifc,
    "ELLIPSE":    _ellipse_to_ifc,
    "SPLINE":     _spline_to_ifc,
    "HATCH":      _hatch_to_ifc,
    "TEXT":       _text_to_ifc,
    "MTEXT":      _text_to_ifc,
}


def dxf_entity_to_ifc(
    model: ifcopenshell.file,
    entity,
    scale: float = 1.0,
) -> Optional[object]:
    """Convert a single ezdxf entity to an IFC geometry item, or None if unsupported."""
    dxftype = entity.dxftype()
    converter = _CONVERTERS.get(dxftype)
    if converter is None:
        return None
    try:
        return converter(model, entity, scale)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Block helpers (INSERT → IfcMappedItem)
# ---------------------------------------------------------------------------

def block_to_representation_map(
    model: ifcopenshell.file,
    block_def,
    subcontext,
    scale: float = 1.0,
):
    """Convert a DXF block definition to IfcRepresentationMap."""
    items = [dxf_entity_to_ifc(model, e, scale) for e in block_def]
    items = [i for i in items if i is not None]
    if not items:
        return None
    curve_set = model.createIfcGeometricCurveSet(Elements=items)
    shape_repr = model.createIfcShapeRepresentation(
        ContextOfItems=subcontext,
        RepresentationIdentifier="Annotation",
        RepresentationType="GeometricCurveSet",
        Items=[curve_set],
    )
    origin = model.createIfcAxis2Placement2D(
        Location=model.createIfcCartesianPoint([0.0, 0.0])
    )
    return model.createIfcRepresentationMap(
        MappingOrigin=origin,
        MappedRepresentation=shape_repr,
    )


def insert_to_mapped_item(model: ifcopenshell.file, insert_entity, repr_map, scale: float = 1.0):
    """Convert a DXF INSERT to IfcMappedItem using a pre-built IfcRepresentationMap."""
    t = insert_entity.dxf
    target = model.createIfcCartesianTransformationOperator2D(
        Axis1=None,
        Axis2=None,
        LocalOrigin=model.createIfcCartesianPoint([t.insert.x * scale, t.insert.y * scale]),
        Scale=getattr(t, "xscale", 1.0),
    )
    return model.createIfcMappedItem(
        MappingSource=repr_map,
        MappingTarget=target,
    )
