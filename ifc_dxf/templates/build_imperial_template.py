"""Build ifc_dxf_template_imperial.dxf from the metric template.

Run from the repository root:
    python ifc_dxf/templates/build_imperial_template.py

The imperial template is the metric one converted to feet -- same layers,
blocks and text styles, whose paper heights keep their printed size (Bonsai's
CSS sizes) -- plus:

- `dimensions_imperial`: the metric dimension style in architectural units
  (feet and inches, "." as decimal point) with architectural ticks; the export
  prefers it over `dimensions_metric_m` (dxf_template._ensure_dim_style);
- one layout per US sheet size in SHEETS, each with a drawing viewport and a
  title block carrying the {{Identification}}, {{Name}}, {{scale}} and
  {{date}} placeholders. The export keeps the smallest sheet that holds the
  drawing at its scale and drops the others (dxf_template._select_sheet).

Paper space is in feet (plotted at 12 in per unit), the way the metric
template's paper space is in metres (1000 mm per unit). Running this script
again overwrites any hand edits made to the template in a CAD program.
"""

import os
import sys

import ezdxf
from ezdxf.lldxf.types import DXFTag
from ezdxf.render.arrows import ARROWS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # repository root

from ifc_dxf.core.dxf_template import _apply_drawing_units  # noqa: E402

SOURCE = os.path.join(HERE, "ifc_dxf_template_metric.dxf")
TARGET = os.path.join(HERE, "ifc_dxf_template_imperial.dxf")

# (layout name, width, height) in inches, landscape; smallest first
SHEETS = [
    ("ANSI B 11x17", 17.0, 11.0),
    ("ARCH C 18x24", 24.0, 18.0),
    ("ARCH D 24x36", 36.0, 24.0),
    ("ARCH E1 30x42", 42.0, 30.0),
    ("ARCH E 36x48", 48.0, 36.0),
]
INCH = 1.0 / 12.0       # paper space unit is the foot
MARGIN = 0.5            # inches, all round
TITLE_W = 4.0           # title block, inches
TITLE_ROWS = (0.45, 1.05, 0.75)   # bottom (date/scale), Name, Identification
TEXT_INSET = 0.12       # inches, text from the title block's left/right edge


def _style_height(doc, name):
    """A text style's fixed (paper) height, already in feet."""
    return doc.styles.get(name).dxf.get("height", 0.0) or 0.1 * INCH


def _imperial_dimstyle(doc):
    style = doc.dimstyles.duplicate_entry("dimensions_metric_m", "dimensions_imperial")
    style.dxf.dimlunit = 4        # architectural: feet and inches
    style.dxf.dimlfac = 12.0      # drawing unit is the foot; the format counts inches
    style.dxf.dimdsep = ord(".")
    style.dxf.dimdec = 4          # 1/16"
    style.dxf.dimzin = 1          # keep zero feet and zero inches: 0' - 6", 3' - 0"
    style.set_arrows(blk=ARROWS.architectural_tick)
    doc.header["$DIMSTYLE"] = "dimensions_imperial"


def _draw_sheet(doc, layout, width, height, scale_handle):
    """Sheet outline, drawing viewport and title block, in feet."""
    w, h = width * INCH, height * INCH
    m = MARGIN * INCH
    layout.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True,
                          dxfattribs={"layer": "0", "lineweight": 5})

    # Drawing viewport over the whole sheet inside the margin. Its view
    # height is set by the export from the drawing scale.
    vp_w, vp_h = w - 2 * m, h - 2 * m
    vp = layout.add_viewport(center=(w / 2, h / 2), size=(vp_w, vp_h),
                             view_center_point=(0, 0), view_height=vp_h * 48,
                             dxfattribs={"layer": "Annotation_noplot"})
    xrec = vp.new_extension_dict().add_xrecord("ASDK_XREC_ANNOTATION_SCALE_INFO")
    xrec.reset([DXFTag(90, 1), DXFTag(340, scale_handle)])

    # Title block, bottom right inside the margin
    tw = TITLE_W * INCH
    x0, x1 = w - m - tw, w - m
    ys = [m]
    for row in TITLE_ROWS:
        ys.append(ys[-1] + row * INCH)
    frame = dict(layer="0", lineweight=5)
    layout.add_lwpolyline([(x0, ys[0]), (x1, ys[0]), (x1, ys[-1]), (x0, ys[-1])],
                          close=True, dxfattribs=frame)
    for y in ys[1:-1]:
        layout.add_line((x0, y), (x1, y), dxfattribs=frame)

    inset = TEXT_INSET * INCH
    regular = _style_height(doc, "regular")
    date_y = (ys[0] + ys[1]) / 2
    layout.add_text("{{date}}", dxfattribs={
        "style": "regular", "height": regular, "layer": "0"}).set_placement(
        (x0 + inset, date_y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_LEFT)
    layout.add_text("{{scale}}", dxfattribs={
        "style": "regular", "height": regular, "layer": "0"}).set_placement(
        (x1 - inset, date_y), align=ezdxf.enums.TextEntityAlignment.MIDDLE_RIGHT)

    # Name and Identification wrap, so MTEXT; literal braces are escaped.
    for key, style, (y_lo, y_hi) in (("Name", "header", (ys[1], ys[2])),
                                     ("Identification", "title", (ys[2], ys[3]))):
        mtext = layout.add_mtext("\\{\\{%s\\}\\}" % key, dxfattribs={
            "style": style, "char_height": _style_height(doc, style),
            "width": tw - 2 * inset, "layer": "0",
            "attachment_point": ezdxf.enums.MTextEntityAlignment.MIDDLE_LEFT})
        mtext.set_location((x0 + inset, (y_lo + y_hi) / 2))


def build(source=SOURCE, target=TARGET):
    doc = ezdxf.readfile(source)
    # Metres -> feet: text styles, dimension styles, $INSUNITS, $MEASUREMENT.
    _apply_drawing_units(doc, 0.3048)
    _imperial_dimstyle(doc)

    scales = doc.rootdict["ACAD_SCALELIST"]
    scale_handle = scales.get("1:1").dxf.handle if "1:1" in scales else "0"
    old_layouts = [name for name in doc.layouts.names() if name != "Model"]
    for name, width, height in SHEETS:
        layout = doc.layouts.new(name)
        layout.page_setup(size=(width, height), margins=(0, 0, 0, 0), units="inch",
                          scale=(12, 1), name=name.split()[0])
        _draw_sheet(doc, layout, width, height, scale_handle)
    for name in old_layouts:
        doc.layouts.delete(name)
    doc.layouts.set_active_layout(SHEETS[2][0])

    doc.audit()
    doc.saveas(target)
    return target


if __name__ == "__main__":
    print("written", build())
