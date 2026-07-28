"""Builders for synthetic drawings with the structure of Bonsai's own SVGs."""

# Identity rotation, cut at 1.6 m: the plane of the sample project.
PLANE = "[[1.000000,0.000000,0.000000,0.000000],[0.000000,1.000000,0.000000,0.000000],[0.000000,0.000000,1.000000,1.600000],[0.000000,0.000000,0.000000,1.000000]]"
# 90 degrees around Z, cut at 1.9 m: the plane of the real plan.
ROTATED_PLANE = "[[0.000000,1.000000,0.000000,0.000000],[-1.000000,0.000000,0.000000,0.000000],[0.000000,0.000000,1.000000,1.900000],[0.000000,0.000000,0.000000,1.000000]]"
MATRIX3 = (10.0, 250.0, 250.0)

SVG = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:ifc="http://www.ifcopenshell.org/ns" \
baseProfile="full" version="1.1" width="500.0mm" height="500.0mm" viewBox="0 0 500.0 500.0" data-scale="{scale}">\
<defs><style type="text/css"><![CDATA[.cut {{ fill: black; fill-rule: evenodd; }}]]></style></defs>\
<g class="section target-view-PLANVIEW scale-100" ifc:plane="{plane}" ifc:matrix3="{matrix3}">{groups}</g></svg>"""


def paper(x, y, matrix3=MATRIX3):
    """Model metres to paper millimetres: scale, offset and the Y that Bonsai
    negates when it flattens the section plane."""
    scale, cx, cy = matrix3
    return scale * x + cx, -scale * y + cy


def ring(points, matrix3=MATRIX3, close=True):
    d = "M" + " L".join(f"{x},{y}" for x, y in (paper(*p, matrix3) for p in points))
    return d + " Z" if close else d


def box(x0, y0, x1, y1, matrix3=MATRIX3):
    return ring([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], matrix3)


def polyline(points, matrix3=MATRIX3):
    """The open paths of the projection groups: `M` plus `L`, no `Z`."""
    return ring(points, matrix3, close=False)


def group(classes, *paths, guid=None):
    body = "".join("<path/>" if d is None else f'<path d="{d}"/>' for d in paths)
    attributes = f' ifc:guid="{guid}"' if guid else ""
    return f'<g class="{classes}"{attributes}>{body}</g>'


def write_svg(path, groups, plane=PLANE, matrix3=MATRIX3, scale="1:100"):
    matrix3_text = f"[[{matrix3[0]},0.0,{matrix3[1]}],[0.0,{matrix3[0]},{matrix3[2]}],[0.0,0.0,1.0]]"
    path.write_text(SVG.format(plane=plane, matrix3=matrix3_text, groups="".join(groups), scale=scale))
    return str(path)
