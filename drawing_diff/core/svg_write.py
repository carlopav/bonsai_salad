"""Writes the comparison drawing as an SVG on the project drawing's canvas."""

import os
from dataclasses import dataclass

import numpy as np
from lxml import etree

from .svg_read import IFC_NS, SVG_NS, paper_to_model

GROUPS = (
    ("diff-invariato", "unchanged"),
    ("diff-demolizione", "demolished"),
    ("diff-nuovo", "added"),
)
# Written before the fills: the section cut is the subject of the drawing and
# covers what is projected behind it, the way a poché does.
LINE_GROUPS = (
    ("diff-proiezione-invariata", "lines_unchanged"),
    ("diff-proiezione-demolita", "lines_demolished"),
    ("diff-proiezione-nuova", "lines_added"),
)
LINEWORK_ATTRIBUTE = "data-diff-linework"
EXCLUDE_ATTRIBUTE = "data-diff-exclude"
COLORS_ATTRIBUTE = "data-diff-colors"
# The stroke-width of Bonsai's own .projection.
LINE_WIDTH = "0.25"


@dataclass(frozen=True)
class Colors:
    unchanged: str = "#D2D2D2"  # rgb(210, 210, 210), the .cut grey of the drawings
    demolished: str = "#F5C518"
    added: str = "#E30613"


@dataclass(frozen=True)
class Settings:
    """How a comparison was made, kept on the drawing itself. The colours are
    part of it: on a comparison drawing they are the legend, so a regeneration
    must not repaint a delivered sheet with whatever the panel holds now."""

    linework: bool = False
    exclude: str = ""
    colors: Colors = Colors()


def rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def write(path, result, section, canvas, settings=Settings()):
    colors = settings.colors
    root = etree.Element(
        SVG_NS + "svg",
        nsmap={None: SVG_NS[1:-1], "ifc": IFC_NS[1:-1]},
        attrib={
            "baseProfile": "full",
            "version": "1.1",
            "width": canvas.width,
            "height": canvas.height,
            "viewBox": canvas.view_box,
            "data-scale": canvas.data_scale,
            LINEWORK_ATTRIBUTE: "true" if settings.linework else "false",
            EXCLUDE_ATTRIBUTE: settings.exclude,
            COLORS_ATTRIBUTE: ",".join((colors.unchanged, colors.demolished, colors.added)),
        },
    )
    style = etree.SubElement(etree.SubElement(root, SVG_NS + "defs"), SVG_NS + "style", type="text/css")
    style.text = etree.CDATA(_css(colors))

    group = etree.SubElement(root, SVG_NS + "g", attrib={"class": "section"})
    group.set(IFC_NS + "matrix3", _matrix_to_string(section.matrix3))

    model_to_paper = np.linalg.inv(paper_to_model(section.plane, section.matrix3))
    if settings.linework:
        for group_id, field in LINE_GROUPS:
            subgroup = etree.SubElement(group, SVG_NS + "g", id=group_id)
            for line in getattr(result, field):
                etree.SubElement(subgroup, SVG_NS + "path", d=_polyline_data(line, model_to_paper))
    for group_id, field in GROUPS:
        subgroup = etree.SubElement(group, SVG_NS + "g", id=group_id)
        for polygon in getattr(result, field):
            etree.SubElement(subgroup, SVG_NS + "path", d=_path_data(polygon, model_to_paper))

    etree.ElementTree(root).write(str(path), xml_declaration=True, encoding="UTF-8", pretty_print=True)


def read_settings(path, fallback):
    """A comparison declares how it was made, so a regeneration cannot drift
    from the drawing it rewrites. Comparisons written before these attributes
    are recognised by their projection groups; the filter they used is lost,
    and the fallback (the panel) answers for it."""
    if not os.path.isfile(path):
        return fallback
    root = etree.parse(str(path)).getroot()
    linework = root.get(LINEWORK_ATTRIBUTE)
    exclude = root.get(EXCLUDE_ATTRIBUTE)
    return Settings(
        linework=_has_linework(root) if linework is None else linework == "true",
        exclude=fallback.exclude if exclude is None else exclude,
        colors=_read_colors(root, fallback.colors),
    )


def _read_colors(root, fallback):
    written = (root.get(COLORS_ATTRIBUTE) or "").split(",")
    if len(written) != 3:
        return fallback
    return Colors(*(color.strip() for color in written))


def _has_linework(root):
    ids = {group_id for group_id, _ in LINE_GROUPS}
    return any(group.get("id") in ids for group in root.iter(SVG_NS + "g"))


def _css(colors):
    return (
        f"#diff-invariato path {{ fill: {colors.unchanged}; stroke: none; fill-rule: evenodd; }}\n"
        f"#diff-demolizione path {{ fill: {colors.demolished}; stroke: none; fill-rule: evenodd; }}\n"
        f"#diff-nuovo path {{ fill: {colors.added}; stroke: none; fill-rule: evenodd; }}\n"
        f"#diff-proiezione-invariata path {{ fill: none; stroke: {colors.unchanged}; "
        f"stroke-width: {LINE_WIDTH}; }}\n"
        f"#diff-proiezione-demolita path {{ fill: none; stroke: {colors.demolished}; "
        f"stroke-width: {LINE_WIDTH}; }}\n"
        f"#diff-proiezione-nuova path {{ fill: none; stroke: {colors.added}; stroke-width: {LINE_WIDTH}; }}\n"
    )


def _path_data(polygon, matrix):
    """Holes as extra subpaths, the way Bonsai re-emits its own cut polygons
    (bim/module/drawing/operator.py:1645-1660)."""
    rings = [polygon.exterior, *polygon.interiors]
    return " ".join(_ring_data(ring, matrix) for ring in rings)


def _ring_data(ring, matrix):
    return _points_data(ring.coords[:-1], matrix) + " Z"


def _polyline_data(line, matrix):
    return _points_data(line.coords, matrix)


def _points_data(coords, matrix):
    points = np.array(coords)
    paper = (matrix @ np.column_stack([points, np.ones(len(points))]).T).T
    return "M" + " L".join(f"{x:.4f},{y:.4f}" for x, y, _ in paper)


def _matrix_to_string(matrix):
    return "[" + ",".join("[" + ",".join(f"{v:.6f}" for v in row) + "]" for row in matrix) + "]"
