"""Reads Bonsai drawing SVGs into section geometry in model coordinates."""

import ast
import collections
import functools
import re
from dataclasses import dataclass, field

import numpy as np
import shapely
import shapely.affinity
from lxml import etree

SVG_NS = "{http://www.w3.org/2000/svg}"
IFC_NS = "{http://www.ifcopenshell.org/ns}"

# IFC base-64 GlobalIds: 22 characters, the first one carrying only 2 bits.
GUID_PATTERN = re.compile(r"[0-3][0-9A-Za-z_$]{21}$")

# IfcSpace is in here because a room changes shape whenever a wall moves: on a
# drawing where spaces are visible it would invent square metres of demolition.
DEFAULT_EXCLUDE = ("IfcFurniture", "IfcSanitaryTerminal", "IfcGeographicElement", "IfcSpace")


@dataclass(frozen=True)
class Canvas:
    """Root attributes of the drawing, copied over to the comparison SVG."""

    width: str
    height: str
    view_box: str
    data_scale: str


@dataclass(frozen=True)
class CutPolygon:
    geometry: shapely.Polygon
    ifc_class: str
    guids: tuple


@dataclass(frozen=True)
class ProjectionLine:
    geometry: shapely.LineString
    ifc_class: str
    guids: tuple


@dataclass
class Section:
    name: str
    plane: np.ndarray
    matrix3: np.ndarray
    scale: float
    polygons: list
    discarded_subpaths: int
    empty_groups: int
    lines: list = field(default_factory=list)
    excluded_classes: collections.Counter = field(default_factory=collections.Counter)


def parse_exclude(text):
    """The exclusion filter as the panel writes it: a comma separated list of
    class tokens, with the spaces and the empty entries dropped."""
    return tuple(token for token in (part.strip() for part in (text or "").split(",")) if token)


def read_sections(path, exclude=DEFAULT_EXCLUDE, linework=False):
    """Returns the drawing canvas and its section groups."""
    root = etree.parse(str(path)).getroot()
    canvas = Canvas(
        width=root.get("width"),
        height=root.get("height"),
        view_box=root.get("viewBox"),
        data_scale=root.get("data-scale"),
    )
    scale = _scale_denominator(canvas.data_scale)
    sections = []
    for group in root.iter(SVG_NS + "g"):
        if "section" not in (group.get("class") or "").split():
            continue
        plane = np.array(ast.literal_eval(group.get(IFC_NS + "plane")), dtype=float)
        matrix3 = np.array(ast.literal_eval(group.get(IFC_NS + "matrix3")), dtype=float)
        content = _read_groups(group, paper_to_model(plane, matrix3), exclude, linework)
        sections.append(
            Section(
                name=group.get(IFC_NS + "name") or "",
                plane=plane,
                matrix3=matrix3,
                scale=scale,
                polygons=content.polygons,
                discarded_subpaths=content.discarded,
                empty_groups=content.empty,
                lines=content.lines,
                excluded_classes=content.excluded,
            )
        )
    return canvas, sections


def _scale_denominator(data_scale):
    numerator, _, denominator = (data_scale or "").partition(":")
    if not denominator:
        # The cached *-linework.svg files carry no data-scale: Bonsai writes it
        # on the finished drawing, which is the file to compare.
        raise ValueError(f"The drawing has no usable data-scale ({data_scale!r}): is this a cached linework SVG?")
    return float(denominator) / float(numerator)


def paper_to_model(plane, matrix3):
    """Paper millimetres to model metres: matrix3 maps the 2D section frame
    (with Y negated) to paper, ifc:plane maps that frame to the model."""
    negate_y = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]])
    plane_2d = np.array(
        [
            [plane[0][0], plane[0][1], plane[0][3]],
            [plane[1][0], plane[1][1], plane[1][3]],
            [0.0, 0.0, 1.0],
        ]
    )
    # A vertical plane collapses onto the model XY: sections and elevations
    # would come out as zero area slivers rather than as an error.
    if abs(np.linalg.det(plane_2d[:2, :2])) < 0.999:
        raise ValueError("The section plane is not horizontal: only plan views can be compared.")
    return plane_2d @ negate_y @ np.linalg.inv(matrix3)


def group_guids(group, tokens):
    """Both provenances: the projection groups carry ifc:guid, while the cut
    groups mostly carry the GUIDs of the elements Bonsai merged as class
    tokens (250 of the 268 cut groups of the real plan)."""
    guids = tuple(t for t in tokens if GUID_PATTERN.match(t))
    guid = group.get(IFC_NS + "guid")
    if guid and guid not in guids:
        guids = (guid, *guids)
    return guids


@dataclass
class _Content:
    polygons: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    discarded: int = 0
    empty: int = 0
    excluded: collections.Counter = field(default_factory=collections.Counter)


def _read_groups(section, transform, exclude, linework):
    """Everything the drawing shows takes part, except what the filter names:
    an element exported under an unexpected class on one side only would be a
    false demolition, so nothing is dropped for not being recognised."""
    content = _Content()
    for group in section.iter(SVG_NS + "g"):
        tokens = (group.get("class") or "").split()
        if "cut" in tokens:
            read = _read_cut
        elif linework and "projection" in tokens:
            read = _read_projection
        else:
            continue
        excluded = _excluded_token(tokens, exclude)
        if excluded is not None:
            content.excluded[excluded] += 1
            continue
        read(group, tokens, transform, content)
    return content


def _excluded_token(tokens, exclude):
    """Bonsai's merge unions the classes of the elements it joins, so a group
    can carry both a filtered class and a wanted one: dropping it whole would
    take the wall away with the furniture. Excluding by class therefore only
    bites when no other class is left. A token that is not a class - a material,
    a layer - was named for exactly these groups, and takes them out as asked."""
    excluded = [t for t in tokens if t in exclude]
    if not excluded:
        return None
    by_token = next((t for t in excluded if not t.startswith("Ifc")), None)
    if by_token is not None:
        return by_token
    if any(t.startswith("Ifc") and t not in exclude for t in tokens):
        return None
    return excluded[0]


def _read_cut(group, tokens, transform, content):
    # fill-rule is a per element rule: sibling paths fill on their own,
    # only the subpaths of one path are each other's holes.
    geometries = []
    for path in group.findall(SVG_NS + "path"):
        rings, path_discarded = subpath_rings(path.get("d"))
        geometry, degenerate = _even_odd(rings)
        content.discarded += path_discarded + degenerate
        geometries.append(geometry)
    parts = polygonal_parts(_transform(shapely.union_all(geometries), transform))
    if not parts:
        content.empty += 1
        return
    ifc_class = _ifc_class(tokens)
    guids = group_guids(group, tokens)
    content.polygons.extend(CutPolygon(geometry=part, ifc_class=ifc_class, guids=guids) for part in parts)


def _read_projection(group, tokens, transform, content):
    polylines = []
    for path in group.findall(SVG_NS + "path"):
        coords, path_discarded = subpath_lines(path.get("d"))
        content.discarded += path_discarded
        polylines.extend(shapely.LineString(c) for c in coords)
    if not polylines:
        content.empty += 1
        return
    ifc_class = _ifc_class(tokens)
    guids = group_guids(group, tokens)
    geometry = _transform(shapely.MultiLineString(polylines), transform)
    content.lines.extend(
        ProjectionLine(geometry=part, ifc_class=ifc_class, guids=guids) for part in linear_parts(geometry)
    )


def _ifc_class(tokens):
    return next((t for t in tokens if t.startswith("Ifc")), "")


def subpath_points(d):
    """Splits a path's `d` into the points of each subpath, with the guards
    Bonsai applies to its own linework (bim/module/drawing/operator.py:1697-1710):
    real drawings contain a missing `d` and `d="M Z"`. A subpath ending in `z`
    comes back closed. Returns the subpaths and how many were unreadable."""
    if not d:
        return [], 0
    subpaths = []
    discarded = 0
    for subpath in d.split("M")[1:]:
        coords = []
        for token in ("M" + subpath.strip(" Zz")).split():
            try:
                x, y = (round(float(o), 3) for o in token[1:].split(","))
            except ValueError:
                coords = []
                break
            coords.append((x, y))
        if not coords:
            discarded += 1
            continue
        if subpath.strip().lower().endswith("z"):
            coords.append(coords[0])
        subpaths.append(coords)
    return subpaths, discarded


def subpath_rings(d):
    """The subpaths that enclose an area: two-point subpaths and open ones are
    no polygon and are dropped, counted."""
    subpaths, discarded = subpath_points(d)
    rings = []
    for coords in subpaths:
        if len(coords) > 2 and coords[0] == coords[-1]:
            rings.append(coords)
        else:
            discarded += 1
    return rings, discarded


def subpath_lines(d):
    """The subpaths as they are: projection paths are open, so only a subpath
    collapsed onto a single point, `z` or no `z`, has nothing to draw."""
    subpaths, discarded = subpath_points(d)
    polylines = []
    for coords in subpaths:
        if len(set(coords)) > 1:
            polylines.append(coords)
        else:
            discarded += 1
    return polylines, discarded


def _even_odd(rings):
    """Multiple subpaths of a cut path are holes (`fill-rule: evenodd`); the XOR
    handles nesting at any depth, and make_valid keeps self-intersecting rings
    from breaking it. Rings left without any area (two-point subpaths, collapsed
    spurs) are dropped and counted."""
    polygons = []
    degenerate = 0
    for ring in rings:
        polygon = shapely.make_valid(shapely.Polygon(ring))
        if polygonal_parts(polygon):
            polygons.append(polygon)
        else:
            degenerate += 1
    if not polygons:
        return shapely.Polygon(), degenerate
    return functools.reduce(shapely.symmetric_difference, polygons), degenerate


def polygonal_parts(geometry):
    """Degenerate rings survive make_valid as lines and collections: keep the
    areal parts only."""
    parts = []
    for part in shapely.get_parts(geometry):
        if part.is_empty:
            continue
        if isinstance(part, shapely.Polygon):
            parts.append(part)
        elif isinstance(part, (shapely.MultiPolygon, shapely.GeometryCollection)):
            parts.extend(polygonal_parts(part))
    return parts


def linear_parts(geometry):
    """The linear parts of whatever a boolean between polylines returns, which
    can be a collection with points in it."""
    parts = []
    for part in shapely.get_parts(geometry):
        if part.is_empty:
            continue
        if isinstance(part, shapely.LineString):
            parts.append(part)
        elif isinstance(part, (shapely.MultiLineString, shapely.GeometryCollection)):
            parts.extend(linear_parts(part))
    return parts


def _transform(geometry, matrix):
    return shapely.affinity.affine_transform(
        geometry,
        [matrix[0][0], matrix[0][1], matrix[1][0], matrix[1][1], matrix[0][2], matrix[1][2]],
    )
