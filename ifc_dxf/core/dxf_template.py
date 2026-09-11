"""DXF utilities: scale list, layer setup, font resolution, cartiglio fill,
dimstyle, drawing units.

Depends only on core/units.py.
"""

import os
import sys

import numpy as np

from .units import dxf_insunits, is_imperial


# Valid DXF lineweight values (hundredths of mm)
_DXF_LW = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50,
            53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)

_DIM_STYLE_NAME = "dimensions_metric_m"
_DIM_STYLE_IMPERIAL = "dimensions_imperial"  # preferred when a template has it
_DIM_STYLE_FALLBACK = "BONSAI_DIM"

# Same priority order as Bonsai's SVG CSS font-family stack.
_FONT_FALLBACKS = [
    "OpenGost Type B TT.ttf",
    "DejaVuSansCondensed.ttf",
    "LiberationSansNarrow-Regular.ttf",
    "arialn.ttf",
    "arial.ttf",
]

# BricsCAD/AutoCAD SCALE entity format (verified from BricsCAD-saved DXF):
#   entity type: SCALE  (not ACDBSCALE)
#   subclass AcDbScale group codes: 70=flags, 300=name, 140=paper, 141=drawing, 290=is_1:1
_ARCH_SCALES = [
    ("1:1",    1,   1,   1),   # 290=1 marks the paper-space 1:1 base scale
    ("1:2",    1,   2,   0),
    ("1:4",    1,   4,   0),
    ("1:5",    1,   5,   0),
    ("1:8",    1,   8,   0),
    ("1:10",   1,   10,  0),
    ("1:20",   1,   20,  0),
    ("1:25",   1,   25,  0),
    ("1:50",   1,   50,  0),
    ("1:100",  1,   100, 0),
    ("1:200",  1,   200, 0),
    ("1:500",  1,   500, 0),
    ("1:1000", 1,   1000,0),
    ("2:1",    2,   1,   0),
    ("5:1",    5,   1,   0),
    ("10:1",   10,  1,   0),
    ("100:1",  100, 1,   0),
]


def _project_local_to_lines(verts, edges, cam_R):
    """Project element-local verts/edges to block-local 2D line segments."""
    n = len(verts) // 3
    if n == 0 or len(edges) < 2:
        return []
    verts_arr = np.array(verts[:n * 3], dtype=float).reshape(n, 3)
    pts = (cam_R @ verts_arr.T).T[:, :2]
    lines = []
    for k in range(0, len(edges) - 1, 2):
        i, j = int(edges[k]), int(edges[k + 1])
        p0 = (float(pts[i, 0]), float(pts[i, 1]))
        p1 = (float(pts[j, 0]), float(pts[j, 1]))
        if (p0[0] - p1[0]) ** 2 + (p0[1] - p1[1]) ** 2 > 1e-18:
            lines.append((p0, p1))
    return lines


def _compute_insert(wm_flat, cam_inv_np):
    """Return (pos_2d, rotation_deg) for a DXF INSERT from a world matrix."""
    wm = np.array(wm_flat, dtype=float).reshape(4, 4, order='F')
    orig_h = np.array([0.0, 0.0, 0.0, 1.0])
    pos_h  = cam_inv_np @ (wm @ orig_h)
    local_x_world = wm[:3, :3] @ np.array([1.0, 0.0, 0.0])
    rot_deg = float(np.degrees(np.arctan2(float(local_x_world[1]),
                                          float(local_x_world[0]))))
    return (float(pos_h[0]), float(pos_h[1])), rot_deg


def _snap_lw(mm):
    """Snap a lineweight in mm to the nearest valid DXF lineweight (hundredths of mm)."""
    hundredths = round(float(mm) * 100)
    return min(_DXF_LW, key=lambda v: abs(v - hundredths))


def _parse_scale_factor(scale_str):
    """Parse EPset_Drawing 'Scale' value (e.g. '1/100') -> scale factor 0.01.

    Uses fractions.Fraction so '1/100', '1/50', '2/1000' all parse correctly.
    Returns None if absent, zero, or unparseable.
    """
    if not scale_str:
        return None
    from fractions import Fraction
    try:
        f = Fraction(str(scale_str))
        if f > 0:
            return float(f)   # '1/100' -> 0.01
    except (ValueError, ZeroDivisionError):
        pass
    return None


def _setup_dxf_layers(doc, layer_styles):
    """Add/configure layers from layer_styles list of (name, color, lw, linetype)."""
    for name, color, lw_hundredths, linetype in layer_styles:
        try:
            layer = doc.layers.get(name)
        except Exception:
            layer = doc.layers.add(name)
        layer.color = color
        layer.lineweight = lw_hundredths
        try:
            if linetype and linetype.upper() != "CONTINUOUS":
                if linetype not in doc.linetypes:
                    doc.linetypes.add(linetype)
            layer.dxf.linetype = linetype
        except Exception:
            pass


def _make_text_styles_annotative(doc):
    """Mark every fixed-height text style as annotative.

    The template's styles carry the *paper* height (`small` = 0.0018 m = 1.8 mm),
    which is only meaningful if the CAD multiplies it by the annotation scale --
    and it does that only for an annotative style. A plain style's fixed height
    is taken literally instead, overriding the TEXT entity's height and making
    DIMTXT ignored, so every text and dimension collapsed to paper size (1/100 of
    the model size at 1:100) the moment it was edited in BricsCAD.

    Annotative is not a STYLE group code: it is an XRECORD named AcadAnnotative
    in the style's extension dictionary, carrying the same AnnotativeData payload
    as the per-entity XDATA (see _make_text_annotative).
    """
    from ezdxf.lldxf.types import DXFTag

    for style in doc.styles:
        if not style.dxf.get("height", 0.0):
            continue
        ext_dict = (style.get_extension_dict() if style.has_extension_dict
                    else style.new_extension_dict())
        if "AcadAnnotative" in ext_dict:
            continue
        xrec = ext_dict.add_xrecord("AcadAnnotative")
        xrec.dxf.cloning = 1
        xrec.reset([
            DXFTag(1000, "AnnotativeData"), DXFTag(1002, "{"),
            DXFTag(1070, 1), DXFTag(1070, 1), DXFTag(1002, "}"),
        ])


# US architectural and engineering scales, named the way Bonsai writes
# EPset_Drawing.HumanScale (e.g. 3/8"=1'-0"), so an imperial drawing's own
# scale is found by name. Same tuple layout as _ARCH_SCALES.
_IMPERIAL_SCALES = [
    ('1/32"=1\'-0"',    1, 384, 0),
    ('1/16"=1\'-0"',    1, 192, 0),
    ('3/32"=1\'-0"',    1, 128, 0),
    ('1/8"=1\'-0"',     1, 96,  0),
    ('3/16"=1\'-0"',    1, 64,  0),
    ('1/4"=1\'-0"',     1, 48,  0),
    ('3/8"=1\'-0"',     1, 32,  0),
    ('1/2"=1\'-0"',     1, 24,  0),
    ('3/4"=1\'-0"',     1, 16,  0),
    ('1"=1\'-0"',       1, 12,  0),
    ('1-1/2"=1\'-0"',   1, 8,   0),
    ('3"=1\'-0"',       1, 4,   0),
    ('6"=1\'-0"',       1, 2,   0),
    ('1"=10\'',         1, 120, 0),
    ('1"=20\'',         1, 240, 0),
    ('1"=30\'',         1, 360, 0),
    ('1"=40\'',         1, 480, 0),
    ('1"=50\'',         1, 600, 0),
    ('1"=100\'',        1, 1200, 0),
]


def _add_scale(doc, scale_dict, name, paper, drawing, flag290=0):
    """Add one SCALE object (paper:drawing) to ACAD_SCALELIST."""
    from ezdxf.lldxf.types import DXFTag
    from ezdxf.lldxf.tags import Tags

    obj = doc.objects.new_entity("SCALE", dxfattribs={})
    obj.__class__ = type("SCALE", (obj.__class__,), {"DXFTYPE": "SCALE"})
    obj.xtags.subclasses = [Tags(), Tags([
        DXFTag(100, "AcDbScale"),
        DXFTag(70, 0),
        DXFTag(300, name),
        DXFTag(140, float(paper)),
        DXFTag(141, float(drawing)),
        DXFTag(290, flag290),
    ])]
    obj.dxf.owner = scale_dict.dxf.handle
    scale_dict.add(key=name, entity=obj)


def _populate_scale_list(doc, scale_factor, scale_name=None, imperial=False):
    """Fill ACAD_SCALELIST with SCALE objects and set the current annotation scale.

    BricsCAD/AutoCAD store the current scale in AcDbVariableDictionary ->
    DictionaryVariables entry 'CANNOSCALE', NOT in the $CANNOSCALE header
    variable (which ezdxf doesn't support anyway).

    The current scale is the drawing's own: `scale_name` (its HumanScale) or
    "1:N". A list entry of that name is added when missing -- CANNOSCALE
    naming a scale the list lacks leaves every annotative entity without its
    scale representation (a 3/8"=1'-0" drawing, 1:32, wrote none at all).
    Imperial drawings also get the US architectural/engineering scales.

    Returns ({scale_name: handle}, current scale name).
    """
    # 1. populate ACAD_SCALELIST
    scale_dict = doc.rootdict["ACAD_SCALELIST"]
    existing = set(scale_dict.keys())

    for name, paper, drawing, flag290 in _ARCH_SCALES + (_IMPERIAL_SCALES if imperial else []):
        if name not in existing:
            _add_scale(doc, scale_dict, name, paper, drawing, flag290)
            existing.add(name)

    # 2. the drawing's own scale, added when the list lacks it
    if not scale_name or scale_name.upper() == "NTS":
        scale_name = f"1:{int(round(1.0 / scale_factor))}"
    if scale_name not in existing:
        _add_scale(doc, scale_dict, scale_name, 1, 1.0 / scale_factor)

    # 3. set current annotation scale via AcDbVariableDictionary
    if "AcDbVariableDictionary" not in doc.rootdict:
        var_dict = doc.rootdict.add_new_dict("AcDbVariableDictionary")
    else:
        var_dict = doc.rootdict["AcDbVariableDictionary"]

    var_dict.discard("CANNOSCALE")
    var_dict.add_dict_var("CANNOSCALE", scale_name)

    # 4. return {scale_name: handle} map for annotative entity creation
    scale_dict = doc.rootdict["ACAD_SCALELIST"]
    return {k: scale_dict.get(k).dxf.handle for k in scale_dict.keys()}, scale_name


def _system_font_dirs():
    """Return OS-specific font directory list."""
    dirs = []
    if sys.platform == "win32":
        dirs.append(r"C:\Windows\Fonts")
        local = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts")
        if os.path.isdir(local):
            dirs.append(local)
    elif sys.platform == "darwin":
        dirs += ["/Library/Fonts", "/System/Library/Fonts",
                 os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts",
                 os.path.expanduser("~/.fonts"),
                 os.path.expanduser("~/.local/share/fonts")]
    return dirs


def _font_available(filename):
    """Return True if the given font filename exists anywhere in the system font dirs."""
    name_lower = filename.lower()
    for d in _system_font_dirs():
        for root, _, files in os.walk(d):
            if any(f.lower() == name_lower for f in files):
                return True
    return False


def _resolve_text_font(doc):
    """Pick the best available font for DXF text styles.

    Reads the preferred font from the template's text styles, then walks the
    Bonsai CSS fallback list until a font present on this system is found.
    Updates all named text styles in doc to use that font.
    """
    # Detect the font currently set in the template (any named style).
    preferred = next(
        (s.dxf.font for s in doc.styles if s.dxf.name not in ("Standard", "") and s.dxf.font),
        None,
    )

    # Build candidate list: template preference first, then standard fallbacks.
    candidates = []
    if preferred and preferred not in _FONT_FALLBACKS:
        candidates.append(preferred)
    candidates += _FONT_FALLBACKS

    resolved = candidates[-1]  # last-resort fallback (arial.ttf)
    for font in candidates:
        if _font_available(font):
            resolved = font
            break

    for style in doc.styles:
        if style.dxf.name not in ("Standard", ""):
            style.dxf.font = resolved

    return resolved


# Dimension-style variables holding a length in drawing units; everything else
# in a DIMSTYLE is a flag, a count or a pure ratio.
_DIMSTYLE_LENGTHS = ("dimasz", "dimcen", "dimdle", "dimdli", "dimexe", "dimexo",
                     "dimfxl", "dimgap", "dimrnd", "dimtm", "dimtp", "dimtsz",
                     "dimtxt")


def _apply_drawing_units(doc, unit_scale):
    """Put the document in the project's length unit (`unit_scale` metres per
    drawing unit), converting what the template states in its own unit.

    A template's model-space sizes -- the text styles' paper heights, the
    dimension styles' lengths -- are written in its $INSUNITS (metres for
    ours; unitless counts as metres, the convention it was authored in).
    Paper space is left alone: it plots by its own scale, and its linetypes
    are sized for paper (PSLTSCALE), so $LTSCALE needs no conversion either.

    Returns the factor from template units to drawing units, which the model
    viewport's view height needs too (see _fill_cartiglio).
    """
    from ezdxf import units

    target = dxf_insunits(unit_scale)
    source = doc.header.get("$INSUNITS", units.M) or units.M
    factor = units.conversion_factor(source, units.M) / unit_scale
    if abs(factor - 1.0) > 1e-9:
        for style in doc.styles:
            height = style.dxf.get("height", 0.0)
            if height:
                style.dxf.height = height * factor
        for dimstyle in doc.dimstyles:
            for key in _DIMSTYLE_LENGTHS:
                if dimstyle.dxf.hasattr(key):
                    dimstyle.dxf.set(key, dimstyle.dxf.get(key) * factor)
    doc.header["$INSUNITS"] = target
    doc.header["$MEASUREMENT"] = 0 if is_imperial(target) else 1
    return factor


def _fill_cartiglio(doc, scale_factor, scale_handle=None,
                    drawing_name=None, drawing_identification=None,
                    drawing_scale=None, unit_factor=1.0):
    """Fill cartiglio placeholders in all paper-space layouts.

    Replaces {{scale}}, {{date}}, {{Name}}, {{Identification}} in TEXT/MTEXT.
    Updates the drawing viewport: view_height, center, and annotation scale
    (ASDK_XREC_ANNOTATION_SCALE_INFO extension-dict XREC -> code 340 handle).

    The drawing viewport's view height comes from the drawing scale: its
    paper height / scale, times `unit_factor` (template units -> drawing
    units, from _apply_drawing_units) since paper space stays in template
    units. Any viewport size and template baseline scale work.
    """
    import datetime
    from ezdxf.lldxf.types import DXFTag

    date_str  = datetime.date.today().strftime("%d.%m.%Y")
    scale_str = drawing_scale or f"1:{int(round(1.0 / scale_factor))}"
    name_str  = drawing_name or ""
    ident_str = drawing_identification or ""

    for layout_name in doc.layouts.names():
        if layout_name == "Model":
            continue
        layout = doc.layouts.get(layout_name)

        # viewport: size the model-space drawing viewport(s) from the scale
        for vp in layout.viewports():
            if vp.dxf.id == 1:
                continue  # the layout's own paper-space viewport
            new_h = vp.dxf.height / scale_factor * unit_factor
            vp.dxf.view_height = new_h
            try:
                vp.dxf.view_center_point = (0.0, 0.0)
            except Exception:
                pass
            # Update the annotation scale XREC in the viewport extension dict
            if scale_handle and vp.has_extension_dict:
                try:
                    ext_dict = vp.get_extension_dict()
                    d = ext_dict.dictionary
                    xrec_name = "ASDK_XREC_ANNOTATION_SCALE_INFO"
                    if xrec_name in d:
                        xrec = d[xrec_name]
                        for i, tag in enumerate(xrec.tags):
                            if tag.code == 340:
                                xrec.tags[i] = DXFTag(340, scale_handle)
                                break
                except Exception:
                    pass

        # text placeholders -- any of the four, in TEXT or MTEXT
        values = {"scale": scale_str, "date": date_str,
                  "Name": name_str, "Identification": ident_str}
        for e in layout:
            t = e.dxftype()
            if t == "TEXT":
                e.dxf.text = _fill_placeholders(e.dxf.get("text", ""), values)
            elif t == "MTEXT":
                e.text = _fill_placeholders(e.text, values)


def _fill_placeholders(text, values):
    """Replace {{key}} placeholders; raw MTEXT stores the braces escaped."""
    for key, value in values.items():
        text = text.replace("{{%s}}" % key, value)
        text = text.replace("\\{\\{%s\\}\\}" % key, value)
    return text


_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
_METRIC_TEMPLATE = "ifc_dxf_template_metric.dxf"
_IMPERIAL_TEMPLATE = "ifc_dxf_template_imperial.dxf"


def default_template(unit_scale):
    """The bundled template for the project's unit system: imperial for
    feet/inches (and yards/miles), metric otherwise or when missing."""
    name = _IMPERIAL_TEMPLATE if is_imperial(dxf_insunits(unit_scale)) else _METRIC_TEMPLATE
    path = os.path.join(_TEMPLATES_DIR, name)
    return path if os.path.isfile(path) else os.path.join(_TEMPLATES_DIR, _METRIC_TEMPLATE)


def _select_sheet(doc, drawing_w, drawing_h, paper_unit):
    """Keep the smallest sheet whose drawing viewport holds the drawing.

    A sheet is a paper layout with a drawing viewport (any viewport but the
    layout's own, id 1); the others are deleted. drawing_w/h: the drawing's
    size on paper in metres -- Bonsai sizes a drawing by its camera box times
    the scale. paper_unit: metres per paper-space unit (paper space stays in
    the template's unit). With no sheet big enough the largest is kept.
    Returns (kept layout name, fits), or (None, True) without any sheet.
    """
    sheets = []
    for name in doc.layouts.names():
        if name == "Model":
            continue
        viewports = [vp for vp in doc.layouts.get(name).viewports() if vp.dxf.id != 1]
        if viewports:
            vp = max(viewports, key=lambda v: v.dxf.width * v.dxf.height)
            sheets.append((vp.dxf.width * paper_unit, vp.dxf.height * paper_unit, name))
    if not sheets:
        return None, True
    tol = 1e-3  # a millimetre
    fitting = [s for s in sheets if s[0] + tol >= drawing_w and s[1] + tol >= drawing_h]
    pool = fitting or sheets
    keep = (min if fitting else max)(pool, key=lambda s: s[0] * s[1])[2]
    for _w, _h, name in sheets:
        if name != keep:
            doc.layouts.delete(name)
    doc.layouts.set_active_layout(keep)
    return keep, bool(fitting)


def _ensure_dim_style(doc, scale_factor, unit_scale=1.0):
    """Return the dimension style name to use.

    Prefers the template's 'dimensions_metric_m' and uses it exactly as authored
    -- arrow block, sizes, and its annotative dimscale=0 are all left untouched,
    so the template alone controls dimension appearance. Only when the template
    is absent do we create the 'BONSAI_DIM' fallback with our own standard sizes.

    Dimension entities are individually marked as annotative (AcadAnnotative
    XDATA) so BricsCAD/AutoCAD display them at the correct paper size.
    """
    for name in (_DIM_STYLE_IMPERIAL, _DIM_STYLE_NAME):
        if name in doc.dimstyles:
            return name

    dim_scale = 1.0 / scale_factor   # e.g. 100 for 1:100

    # Paper-space sizes, stated in metres and converted to drawing units --
    # dimscale multiplies these to model space.
    text_h  = 0.0025 / unit_scale
    ext_ext = 0.0015 / unit_scale
    ext_off = 0.0005 / unit_scale
    gap     = text_h * 0.4
    arrow   = 0.0020 / unit_scale

    # fallback: create BONSAI_DIM with oblique ticks
    attrs = {
        "dimtxt": text_h, "dimtsz": arrow,
        "dimasz": arrow,
        "dimexe": ext_ext, "dimexo": ext_off, "dimgap": gap,
        "dimscale": dim_scale,
        "dimtih": 0, "dimtad": 1,
        "dimclrd": 256, "dimclrt": 256, "dimclre": 256,
    }
    if _DIM_STYLE_FALLBACK not in doc.dimstyles:
        doc.dimstyles.new(_DIM_STYLE_FALLBACK, dxfattribs=attrs)
    else:
        style = doc.dimstyles.get(_DIM_STYLE_FALLBACK)
        for k, v in attrs.items():
            style.dxf.set(k, v)
    return _DIM_STYLE_FALLBACK
