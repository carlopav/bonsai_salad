# Bonsai Salad — sheets_to_pdf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""What a built sheet needs before typst can render it faithfully."""

import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

_NAMESPACES = (
    ("", SVG_NS),
    ("xlink", XLINK_NS),
    ("dc", "http://purl.org/dc/elements/1.1/"),
    ("cc", "http://creativecommons.org/ns#"),
    ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
    ("inkscape", "http://www.inkscape.org/namespaces/inkscape"),
    ("sodipodi", "http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd"),
)

_LENGTH = re.compile(r"^\s*(-?\d*\.?\d+)\s*(em|ex|px)?\s*$")
_TEXT_TAGS = (f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}tspan")
_MAX_DEPTH = 8


def _register_namespaces():
    for prefix, uri in _NAMESPACES:
        try:
            ET.register_namespace(prefix, uri)
        except Exception:
            pass


def _length(value, font_size):
    """A length in user units, None when it cannot be resolved."""
    match = _LENGTH.match(value or "")
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2)
    if unit == "em":
        return number * font_size if font_size else None
    if unit == "ex":
        return number * font_size / 2 if font_size else None
    return number


def fold_text_dy(element, font_size=None):
    """Fold dy into y wherever a text carries both, and report whether anything moved.

    resvg, the renderer typst reads SVG with, misplaces the first glyph of a
    tspan that carries an absolute y and a dy under a text whose font-size
    attribute is 0 — how Bonsai writes the multi-line cells of a schedule
    header. Folding the shift into y is what the spec asks for anyway, so it is
    applied wherever the em resolves against a font-size attribute. The rest is
    left alone: a dy that only stacks a line under the previous one, and the
    annotations of a drawing, sized from the stylesheet and rendered correctly.
    """
    own = element.get("font-size")
    size = _length(own, font_size) if own is not None else None
    if size is None:
        size = font_size

    changed = False
    if element.tag in _TEXT_TAGS and element.get("dy") is not None:
        y = _length(element.get("y"), size)
        shift = _length(element.get("dy"), size)
        if y is not None and shift is not None:
            element.set("y", f"{y + shift:.6g}")
            del element.attrib["dy"]
            changed = True

    for child in element:
        changed = fold_text_dy(child, size) or changed
    return changed


def _inline_images(root, svg_dir, depth):
    """Replace <image> references to other SVGs with their content."""
    parents = {child: parent for parent in root.iter() for child in parent}

    referenced = []
    for image in root.iter(f"{{{SVG_NS}}}image"):
        href = unquote(image.get(f"{{{XLINK_NS}}}href") or image.get("href") or "")
        if not href.lower().endswith(".svg"):
            continue
        path = href if os.path.isabs(href) else os.path.join(svg_dir, href.replace("/", os.sep))
        path = os.path.normpath(path)
        if os.path.isfile(path):
            referenced.append((image, path))

    for image, path in referenced:
        parent = parents.get(image)
        if parent is None:
            continue
        sub_root, _ = _load(path, depth + 1)
        for attribute in ("x", "y", "width", "height"):
            value = image.get(attribute)
            if value is not None:
                sub_root.set(attribute, value)
        index = list(parent).index(image)
        parent.remove(image)
        parent.insert(index, sub_root)

    return bool(referenced)


def _load(svg_path, depth=0):
    root = ET.parse(svg_path).getroot()
    changed = fold_text_dy(root)
    if depth < _MAX_DEPTH:
        changed = _inline_images(root, os.path.dirname(svg_path), depth) or changed
    return root, changed


def prepare(svg_path):
    """The sheet as typst should read it, None when the file on disk already is."""
    _register_namespaces()
    root, changed = _load(svg_path)
    if not changed:
        return None
    xml = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + xml).encode("utf-8")
