# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""DXF entity → IFC geometry conversion (pure Python, no bpy)."""

from __future__ import annotations

import math
from typing import Optional

import ifcopenshell
import ifcopenshell.util.unit
from ezdxf.math import bulge_to_arc as _ezdxf_bulge_to_arc


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _pt2(model: ifcopenshell.file, x: float, y: float, scale: float = 1.0):
    return model.createIfcCartesianPoint([float(x) * scale, float(y) * scale])


def _axis2d(model: ifcopenshell.file, cx: float, cy: float, scale: float = 1.0):
    return model.createIfcAxis2Placement2D(
        Location=_pt2(model, cx, cy, scale)
    )


def _trim_point(model: ifcopenshell.file, cx: float, cy: float, r: float,
                angle: float, scale: float = 1.0):
    """The point at *angle* (radians) on the circle (cx, cy, r), for a CARTESIAN trim."""
    return _pt2(model, cx + r * math.cos(angle), cy + r * math.sin(angle), scale)


def angle_unit_scale(model: ifcopenshell.file) -> float:
    """
    Radians -> the model's plane angle unit, for IfcParameterValue trims.

    IfcTrimmedCurve parameters on a conic are angles expressed in the project's
    PLANEANGLEUNIT, so writing degrees into a radian model sweeps the arc well
    past where it should stop. A model declaring no plane angle unit is read as
    radians. These parameters are written alongside CARTESIAN trim points but
    are not what IfcOpenShell reads: see the note on MasterRepresentation.
    """
    try:
        unit = ifcopenshell.util.unit.get_project_unit(model, "PLANEANGLEUNIT")
    except Exception:
        return 1.0
    if unit is None:
        return 1.0
    if unit.is_a("IfcConversionBasedUnit"):
        try:
            # ConversionFactor is radians per one of these units.
            factor = float(unit.ConversionFactor.ValueComponent.wrappedValue)
        except Exception:
            return 1.0
        return 1.0 / factor if factor else 1.0
    prefix = getattr(unit, "Prefix", None)
    if prefix:
        try:
            return 1.0 / ifcopenshell.util.unit.get_prefix_multiplier(prefix)
        except Exception:
            return 1.0
    return 1.0


# ---------------------------------------------------------------------------
# Bulge → arc helper
# ---------------------------------------------------------------------------

def bulge_to_arc(p1: tuple, p2: tuple, bulge: float) -> tuple:
    """
    Return (center_xy, radius, start_angle_rad, end_angle_rad) for a DXF bulge.

    Delegates to ezdxf rather than deriving the centre here: the centre sits on
    the chord's perpendicular bisector at the apothem sqrt(r^2 - (d/2)^2), not at
    r, and the side it falls on depends on whether |bulge| > 1 (a major arc).
    ezdxf always hands back a counter-clockwise arc, swapping the angles for a
    negative bulge instead of reversing direction.
    """
    center, start, end, radius = _ezdxf_bulge_to_arc(p1, p2, bulge)
    return (center.x, center.y), radius, start, end


# ---------------------------------------------------------------------------
# Entity converters
# ---------------------------------------------------------------------------

def _line_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
    p1 = entity.dxf.start
    p2 = entity.dxf.end
    return model.createIfcPolyline([
        _pt2(model, p1.x, p1.y, scale),
        _pt2(model, p2.x, p2.y, scale),
    ])


def _lwpolyline_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
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
            items.append(model.createIfcTrimmedCurve(
                BasisCurve=circle,
                Trim1=[model.createIfcParameterValue(start * angle_scale),
                       _trim_point(model, cx, cy, r, start, scale)],
                Trim2=[model.createIfcParameterValue(end * angle_scale),
                       _trim_point(model, cx, cy, r, end, scale)],
                # bulge_to_arc already normalises to a counter-clockwise arc.
                SenseAgreement=True,
                MasterRepresentation="CARTESIAN",
            ))

    if not items:
        return None
    if len(items) == 1:
        return items[0]
    # Wrap multiple segments in a GeometricCurveSet
    return model.createIfcGeometricCurveSet(Elements=items)


def _polyline_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
    pts = [v.dxf.location for v in entity.vertices]
    if len(pts) < 2:
        return None
    return model.createIfcPolyline([_pt2(model, p.x, p.y, scale) for p in pts])


def _arc_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
    c = entity.dxf.center
    r = entity.dxf.radius * scale
    start_deg = entity.dxf.start_angle
    end_deg = entity.dxf.end_angle
    circle = model.createIfcCircle(
        Position=_axis2d(model, c.x, c.y, scale),
        Radius=r,
    )
    start, end = math.radians(start_deg), math.radians(end_deg)
    # Trimmed by points, not by parameter: a project that declares no
    # PLANEANGLEUNIT (Bonsai does not add one) makes IfcOpenShell take the
    # complementary arc from parameter trims, turning a 60 degree swing into
    # 300. The angles ride along for readers that prefer PARAMETER.
    return model.createIfcTrimmedCurve(
        BasisCurve=circle,
        Trim1=[model.createIfcParameterValue(start * angle_scale),
               _trim_point(model, c.x, c.y, entity.dxf.radius, start, scale)],
        Trim2=[model.createIfcParameterValue(end * angle_scale),
               _trim_point(model, c.x, c.y, entity.dxf.radius, end, scale)],
        SenseAgreement=True,
        MasterRepresentation="CARTESIAN",
    )


def _circle_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
    c = entity.dxf.center
    r = entity.dxf.radius * scale
    return model.createIfcCircle(
        Position=_axis2d(model, c.x, c.y, scale),
        Radius=r,
    )


def _ellipse_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
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


def _spline_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
    """Approximate spline as polyline from control points."""
    try:
        pts = list(entity.control_points)
    except Exception:
        return None
    if len(pts) < 2:
        return None
    return model.createIfcPolyline([_pt2(model, p[0], p[1], scale) for p in pts])


def _hatch_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
    """Extract boundary loops of a HATCH as IfcPolyline items."""
    items = []
    try:
        for path in entity.paths:
            pts = []
            if hasattr(path, "vertices"):
                # PolylinePath: vertices are (x, y, bulge) tuples
                pts = [(v[0], v[1]) for v in path.vertices]
            elif hasattr(path, "edges"):
                # EdgePath
                for edge in path.edges:
                    if hasattr(edge, "start"):
                        pts.append((edge.start[0], edge.start[1]))
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


def _text_to_ifc(model: ifcopenshell.file, entity, scale: float, angle_scale: float = 1.0):
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
    angle_scale: float = 1.0,
) -> Optional[object]:
    """Convert a single ezdxf entity to an IFC geometry item, or None if unsupported."""
    dxftype = entity.dxftype()
    converter = _CONVERTERS.get(dxftype)
    if converter is None:
        return None
    try:
        return converter(model, entity, scale, angle_scale)
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
    angle_scale: float = 1.0,
):
    """Convert a DXF block definition to IfcRepresentationMap."""
    items = [dxf_entity_to_ifc(model, e, scale, angle_scale) for e in block_def]
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
    rotation = math.radians(getattr(t, "rotation", 0.0))
    cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    target = model.createIfcCartesianTransformationOperator2D(
        Axis1=model.createIfcDirection([cos_r, sin_r]),
        Axis2=model.createIfcDirection([-sin_r, cos_r]),
        LocalOrigin=model.createIfcCartesianPoint([float(t.insert.x) * scale, float(t.insert.y) * scale]),
        Scale=float(getattr(t, "xscale", 1.0)),
    )
    return model.createIfcMappedItem(
        MappingSource=repr_map,
        MappingTarget=target,
    )
