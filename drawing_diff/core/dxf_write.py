"""Writes the comparison geometry as a DXF, model coordinates in metres 1:1."""

import ezdxf
from ezdxf import units

from .svg_write import Colors, rgb

LAYERS = (
    ("unchanged", "lines_unchanged", "DIFF_ESISTENTE", 8),
    ("demolished", "lines_demolished", "DIFF_DEMOLIZIONI", 2),
    ("added", "lines_added", "DIFF_NUOVE_COSTRUZIONI", 1),
)


def write_dxf(path, result, colors=Colors()):
    doc = ezdxf.new("R2010")
    doc.units = units.M
    msp = doc.modelspace()
    for field, line_field, name, aci in LAYERS:
        layer = doc.layers.add(name)
        layer.color = aci
        layer.rgb = rgb(getattr(colors, field))
        # Projections first, cuts over them: same order as the SVG, so a CAD
        # that honours the entity order shows the same drawing.
        for line in getattr(result, line_field):
            msp.add_lwpolyline(line.coords, dxfattribs={"layer": name})
        for polygon in getattr(result, field):
            _add_polygon(msp, polygon, name)
    doc.saveas(str(path))


def _add_polygon(msp, polygon, layer):
    exterior = list(polygon.exterior.coords[:-1])
    holes = [list(ring.coords[:-1]) for ring in polygon.interiors]
    hatch = msp.add_hatch(dxfattribs={"layer": layer, "color": 256})
    hatch.set_solid_fill(color=256)
    hatch.paths.add_polyline_path(exterior, is_closed=True, flags=1)
    for hole in holes:
        hatch.paths.add_polyline_path(hole, is_closed=True, flags=16)
    for ring in [exterior, *holes]:
        msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": layer})
