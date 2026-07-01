"""DXF utilities: scale list, layer setup, font resolution, cartiglio fill, dimstyle.

This module is a leaf — it has no dependencies on other core modules.
"""

import os
import sys

import numpy as np


# Valid DXF lineweight values (hundredths of mm)
_DXF_LW = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50,
            53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)

_DIM_STYLE_NAME = "dimensions_metric_m"
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


def _populate_scale_list(doc, scale_factor):
    """Fill ACAD_SCALELIST with SCALE objects and set the current annotation scale.

    BricsCAD/AutoCAD store the current scale in AcDbVariableDictionary ->
    DictionaryVariables entry 'CANNOSCALE', NOT in the $CANNOSCALE header
    variable (which ezdxf doesn't support anyway).

    Returns {scale_name: handle} map for annotative entity creation.
    """
    from ezdxf.lldxf.types import DXFTag
    from ezdxf.lldxf.tags import Tags

    # 1. populate ACAD_SCALELIST
    scale_dict = doc.rootdict["ACAD_SCALELIST"]
    existing = set(scale_dict.keys())

    for name, paper, drawing, flag290 in _ARCH_SCALES:
        if name in existing:
            continue
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

    # 2. set current annotation scale via AcDbVariableDictionary
    denom = int(round(1.0 / scale_factor))
    scale_name = f"1:{denom}"

    if "AcDbVariableDictionary" not in doc.rootdict:
        var_dict = doc.rootdict.add_new_dict("AcDbVariableDictionary")
    else:
        var_dict = doc.rootdict["AcDbVariableDictionary"]

    var_dict.discard("CANNOSCALE")
    var_dict.add_dict_var("CANNOSCALE", scale_name)

    # 3. return {scale_name: handle} map for annotative entity creation
    scale_dict = doc.rootdict["ACAD_SCALELIST"]
    return {k: scale_dict.get(k).dxf.handle for k in scale_dict.keys()}


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


def _fill_cartiglio(doc, scale_factor, scale_handle=None,
                    drawing_name=None, drawing_identification=None,
                    drawing_scale=None):
    """Fill cartiglio placeholders in all paper-space layouts.

    Replaces {{scale}}, {{date}}, {{Name}}, {{Identification}} in TEXT/MTEXT.
    Updates the drawing viewport: view_height, center, and annotation scale
    (ASDK_XREC_ANNOTATION_SCALE_INFO extension-dict XREC -> code 340 handle).

    The template is assumed to have been created at 1:100 (scale_factor=0.01).
    view_height scales proportionally for other ratios.
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

        # viewport: update the model-space drawing viewport (view_height > 1)
        for vp in layout.viewports():
            if vp.dxf.view_height <= 1.0:
                continue  # paper-layout sentinel viewport -- leave untouched
            # Scale proportionally from the template's 1:100 baseline
            new_h = vp.dxf.view_height * (0.01 / scale_factor)
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

        # text placeholders
        for e in layout:
            t = e.dxftype()
            if t == "TEXT":
                txt = e.dxf.get("text", "")
                txt = txt.replace("{{scale}}", scale_str)
                txt = txt.replace("{{date}}", date_str)
                e.dxf.text = txt
            elif t == "MTEXT":
                # raw MTEXT stores literal braces as \{ \}
                raw = e.text
                raw = raw.replace(r"\{\{Name\}\}", name_str)
                raw = raw.replace(r"\{\{Identification\}\}", ident_str)
                e.text = raw


def _ensure_dim_style(doc, scale_factor):
    """Create or update the dimension style, returning its name.

    Uses 'dimensions_metric_m' from the template when available (preserving
    its arrow block, font and other appearance attrs); falls back to creating
    'BONSAI_DIM' with oblique ticks when the template is absent.

    Size attributes are kept in paper-space metres (e.g. 0.0025 = 2.5 mm).
    dimscale is set to 1/scale_factor (e.g. 100 for 1:100) so that ezdxf
    multiplies them to the correct model-space sizes when rendering.
    Dimension entities are individually marked as annotative (AcadAnnotative
    XDATA) so BricsCAD/AutoCAD display them at the correct paper size.
    """
    dim_scale = 1.0 / scale_factor   # e.g. 100 for 1:100

    # Paper-space sizes (metres) -- dimscale multiplies these to model space.
    text_h  = 0.0025
    ext_ext = 0.0015
    ext_off = 0.0005
    gap     = text_h * 0.4
    arrow   = 0.0020

    if _DIM_STYLE_NAME in doc.dimstyles:
        style = doc.dimstyles.get(_DIM_STYLE_NAME)
        style.dxf.set("dimtxt",   text_h)
        style.dxf.set("dimasz",   arrow)
        style.dxf.set("dimexe",   ext_ext)
        style.dxf.set("dimexo",   ext_off)
        style.dxf.set("dimgap",   gap)
        style.dxf.set("dimscale", dim_scale)
        style.dxf.set("dimtih",   0)
        style.dxf.set("dimtad",   1)
        return _DIM_STYLE_NAME

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
