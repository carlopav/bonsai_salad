#!/usr/bin/env python3
"""Generate ifc_dxf_template.dxf — base template for ifc_dxf DXF exports.

Metric (metres), R2010 DXF, all standard ifc_dxf layers, BONSAI_DIM dimstyle,
and an A1 landscape paper-space layout.

Run to regenerate the template after changing layer definitions:
    python create_dxf_template.py
"""

import os
import sys

try:
    import ezdxf
    from ezdxf import units
except ImportError:
    sys.exit("ezdxf is required: pip install ezdxf")

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_OUT = os.path.join(SCRIPT_DIR, "ifc_dxf_template.dxf")

# ── valid DXF lineweights (hundredths of mm) ──────────────────────────────────
_DXF_LW = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50,
            53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)

def _snap_lw(mm):
    hw = int(round(mm * 100))
    return min(_DXF_LW, key=lambda x: abs(x - hw))


# ── layer definitions ─────────────────────────────────────────────────────────
# (name, ACI_color, lineweight_mm, linetype)
LAYERS = [
    # Walls
    ("IfcWall_Section",                  7,  0.30, "Continuous"),
    ("IfcWall_View",                     8,  0.13, "Continuous"),
    ("IfcWall_Hatches",                254,  0.09, "Continuous"),
    ("IfcWallStandardCase_Section",      7,  0.50, "Continuous"),
    ("IfcWallStandardCase_View",         8,  0.13, "Dashed"),
    ("IfcWallStandardCase_Hatches",    254,  0.09, "Continuous"),
    # Slabs
    ("IfcSlab_Section",                  7,  0.35, "Continuous"),
    ("IfcSlab_View",                     8,  0.13, "Dashed"),
    ("IfcSlab_Hatches",                254,  0.09, "Continuous"),
    ("IfcSlab",                          8,  0.18, "Continuous"),
    # Columns
    ("IfcColumn_Section",                7,  0.50, "Continuous"),
    ("IfcColumn_Hatches",              254,  0.09, "Continuous"),
    ("IfcColumn",                        7,  0.18, "Continuous"),
    # Beams
    ("IfcBeam_Section",                  7,  0.35, "Continuous"),
    ("IfcBeam_Hatches",                254,  0.09, "Continuous"),
    ("IfcBeam",                          7,  0.18, "Continuous"),
    # Openings
    ("IfcDoor",                          2,  0.18, "Continuous"),
    ("IfcDoor_Overhead",                 2,  0.13, "Dashed"),
    ("IfcWindow",                        4,  0.18, "Continuous"),
    ("IfcWindow_Overhead",               4,  0.13, "Dashed"),
    # Furnishings
    ("IfcFurniture",                     8,  0.13, "Continuous"),
    ("IfcFurnishingElement",             8,  0.13, "Continuous"),
    ("IfcSanitaryTerminal",              8,  0.13, "Continuous"),
    # Circulation
    ("IfcStair",                         3,  0.18, "Continuous"),
    ("IfcStairFlight",                   3,  0.18, "Continuous"),
    ("IfcRailing",                       3,  0.13, "Continuous"),
    ("IfcRamp",                          3,  0.18, "Continuous"),
    # Coverings
    ("IfcCovering",                      8,  0.13, "Continuous"),
    # Annotations
    ("IfcAnnotation_Dimension",          1,  0.13, "Continuous"),
    ("IfcAnnotation_Text",               1,  0.13, "Continuous"),
]


def _setup_linetypes(doc):
    """Define Dashed and DashDot linetypes at model-unit scale (metres).

    Pattern values are in metres; $LTSCALE multiplies them at export time.
    At $LTSCALE=0.01 (1:100), the visible dash is 0.5*0.01 = 5mm on paper.
    """
    lt = doc.linetypes
    if "Dashed" not in lt:
        lt.add("Dashed", [0.75, 0.5, -0.25], description="Dashed  __  __  __")
    if "DashDot" not in lt:
        lt.add("DashDot", [0.65, 0.5, -0.1, 0.0, -0.05], description="DashDot  __.__.__ ")


def _setup_layers(doc):
    _setup_linetypes(doc)
    for name, color, lw_mm, linetype in LAYERS:
        if name in doc.layers:
            layer = doc.layers.get(name)
        else:
            layer = doc.layers.add(name)
        layer.color = color
        layer.lineweight = _snap_lw(lw_mm)
        try:
            layer.dxf.linetype = linetype
        except Exception:
            pass


def _setup_text_styles(doc):
    """DXF TEXTSTYLE entries matching Bonsai's CSS text classes.

    Font priority mirrors the SVG CSS: OpenGost Type B TT (if installed),
    else the CAD application substitutes with its nearest match.
    Height = 0 means the height is set per-entity (standard practice).

    Sizes (paper mm → model-space metres at 1:100):
        title=7mm, header=5mm, large=3.5mm, regular=2.5mm, small/DIMENSION=1.8mm
    """
    # DejaVu Sans Condensed: open source, pre-installed on most Linux distros,
    # included in many Windows/macOS setups (e.g. via LibreOffice/Blender).
    # Condensed and clean — second fallback in Bonsai's own SVG CSS.
    font = "DejaVuSansCondensed.ttf"

    # Paper-space heights in metres (2.5mm → 0.0025m), same as Bonsai CSS.
    # Non-zero height = annotative paper-space height; CAD scales automatically.
    _STYLES = [
        ("title",     0.0070),
        ("header",    0.0050),
        ("large",     0.0035),
        ("regular",   0.0025),
        ("small",     0.0018),
        ("DIMENSION", 0.0018),
        ("GRID",      0.0035),
    ]
    for name, paper_h in _STYLES:
        if name not in doc.styles:
            doc.styles.new(name, dxfattribs={"font": font, "height": paper_h})
        else:
            style = doc.styles.get(name)
            style.dxf.font = font
            style.dxf.height = paper_h


def _setup_dimstyle(doc, scale_factor=0.01):
    """BONSAI_DIM: oblique tick markers, sizes fixed in paper space (mm).

    All dimension-entity sizes are stored in model-space metres.  The export
    calls _ensure_dim_style() again to recompute them for the actual scale;
    the values here are the 1:100 defaults stored in the template.

    dimtsz > 0  → oblique tick marks (standard European/Italian diagonal slash)
                  instead of arrowheads.
    dimtad = 1  → text above the dimension line.
    dimtih = 0  → text follows the line angle (not forced horizontal).
    """
    text_h  = 0.0025 / scale_factor   # 2.5 mm paper → 0.25 m model at 1:100
    tick_sz = 0.0020 / scale_factor   # 2.0 mm paper
    ext_ext = 0.0015 / scale_factor   # extension line overshoot past dim line
    ext_off = 0.0005 / scale_factor   # gap from defpoint to ext line start
    gap     = text_h * 0.4

    attrs = {
        "dimtxt": text_h,
        "dimtsz": tick_sz,
        "dimexe": ext_ext,
        "dimexo": ext_off,
        "dimgap": gap,
        "dimtih": 0,
        "dimtad": 1,
        "dimclrd": 256,   # BYLAYER
        "dimclrt": 256,
        "dimclre": 256,
    }
    if "BONSAI_DIM" not in doc.dimstyles:
        doc.dimstyles.new("BONSAI_DIM", dxfattribs=attrs)
    else:
        style = doc.dimstyles.get("BONSAI_DIM")
        for k, v in attrs.items():
            style.dxf.set(k, v)


def _setup_a1_layout(doc, scale_factor=0.01):
    """Add an A1 landscape paper-space layout with a full-page viewport.

    Paper space uses mm (DXF convention independent of $INSUNITS).
    Model space uses metres ($INSUNITS = 6).

    Scale relationship:
        view_height [m] = paper_usable_height [mm] / (scale_factor × 1000)
    At 1:100: 574 mm / (0.01 × 1000) = 57.4 m.
    """
    A1_W_MM  = 841.0
    A1_H_MM  = 594.0
    MARGIN   = 10.0   # mm

    if "A1 Planimetria" in doc.layouts:
        layout = doc.layouts.get("A1 Planimetria")
    else:
        layout = doc.layouts.new("A1 Planimetria")

    layout.page_setup(
        size=(A1_W_MM, A1_H_MM),
        margins=(MARGIN, MARGIN, MARGIN, MARGIN),
        units="mm",
        name="A1",
        device="DWG to PDF.pc3",
    )

    # page_setup() creates one main viewport; update its view_height so that
    # the 1:100 scale fills the usable A1 area in model-space metres.
    usable_h_mm = A1_H_MM - 2 * MARGIN
    view_height_m = usable_h_mm / (scale_factor * 1000.0)

    vps = list(layout.viewports())
    if vps:
        vp = vps[0]
        vp.dxf.view_height = view_height_m
        vp.dxf.layer = "0"
        # Center on model-space origin (the camera-projected drawing is at 0,0).
        try:
            vp.dxf.view_center_point = (0.0, 0.0)
        except Exception:
            pass
        vp.dxf.status = 1   # active

    # Border rectangle in paper space (mm coordinates).
    pts = [
        (MARGIN, MARGIN),
        (A1_W_MM - MARGIN, MARGIN),
        (A1_W_MM - MARGIN, A1_H_MM - MARGIN),
        (MARGIN, A1_H_MM - MARGIN),
    ]
    layout.add_lwpolyline(pts, close=True, dxfattribs={"color": 7, "lineweight": 50})

    return layout


def main():
    doc = ezdxf.new("R2010")
    doc.units = units.M
    doc.header["$MEASUREMENT"] = 1    # metric
    doc.header["$LTSCALE"]     = 0.01  # default 1:100

    _setup_layers(doc)
    _setup_text_styles(doc)
    _setup_dimstyle(doc, scale_factor=0.01)
    _setup_a1_layout(doc, scale_factor=0.01)

    doc.saveas(TEMPLATE_OUT)

    print(f"Template: {TEMPLATE_OUT}")
    print(f"  Layers     : {len(LAYERS)}  (+default '0')")
    print(f"  TextStyles : title, header, large, regular, small, DIMENSION, GRID  (DejaVu Sans Condensed)")
    print(f"  Dimstyle   : BONSAI_DIM  (1:100 defaults, recalculated at export)")
    print(f"  Layout     : A1 Planimetria  (841 x 594 mm, 1:100 viewport)")


if __name__ == "__main__":
    main()
