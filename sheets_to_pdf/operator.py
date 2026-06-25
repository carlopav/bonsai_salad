# Bonsai - OpenBIM 5D Blender Add-on based on Bonsai
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
#
# This file is part of Bonsai5D+.  GNU GPL v3 or later.

"""Self-contained "convert sheet SVGs to PDF" operator.

This feature is intentionally isolated in its own module: it does not belong to
the cost-management domain and is slated to be removed from this add-on and
moved elsewhere. Keep all of its dependencies local to this package so it can be
dropped without touching the rest of the code base.
"""

import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import bpy

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"


def _open_file(path):
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


def _get_ifc():
    try:
        from bonsai import tool
        return tool.Ifc.get()
    except Exception:
        return None


def _get_ifc_path():
    try:
        from bonsai import tool
        return tool.Ifc.get_path()
    except Exception:
        return None


def _ensure_typst():
    try:
        import typst  # noqa: F401
        return True
    except ImportError:
        pass
    appdata = os.environ.get("APPDATA", "")
    site = os.path.join(
        appdata,
        r"Blender Foundation\Blender\5.1\extensions\.local\lib\python3.13\site-packages",
    )
    if os.path.isdir(site) and site not in sys.path:
        sys.path.insert(0, site)
    try:
        import typst  # noqa: F401
        return True
    except ImportError:
        return False


def _find_sheet_svgs():
    ifc = _get_ifc()
    if ifc is None:
        return []
    ifc_path = _get_ifc_path()
    if not ifc_path:
        return []
    ifc_dir = os.path.dirname(os.path.abspath(ifc_path))
    sheets_dir = os.path.join(ifc_dir, "sheets")

    try:
        from bonsai.tool import Drawing as _Drawing
        _get_uri = _Drawing.get_document_uri
    except Exception:
        _get_uri = None

    svgs = []
    for doc in ifc.by_type("IfcDocumentInformation"):
        if getattr(doc, "Scope", None) != "SHEET":
            continue

        path = None

        if _get_uri is not None:
            try:
                path = _get_uri(doc)
            except Exception:
                path = None
        if path and not os.path.isfile(path):
            path = None

        if not path:
            loc = getattr(doc, "Location", None) or ""
            if loc:
                p = loc if os.path.isabs(loc) else os.path.join(ifc_dir, loc)
                p = os.path.normpath(p)
                if os.path.isfile(p):
                    path = p

        if not path:
            ident = getattr(doc, "Identification", None) or ""
            name = getattr(doc, "Name", None) or ""
            for candidate in [
                os.path.join(sheets_dir, f"{ident} - {name}.svg") if ident and name else None,
                os.path.join(sheets_dir, f"{name}.svg") if name else None,
                os.path.join(sheets_dir, f"{ident}.svg") if ident else None,
            ]:
                if candidate and os.path.isfile(candidate):
                    path = candidate
                    break

        if path:
            svgs.append(os.path.normpath(path))

    return svgs


def _inline_svg_images(svg_path, _depth=0):
    from urllib.parse import unquote

    if _depth > 8:
        return None

    for prefix, uri in [
        ("", _SVG_NS),
        ("xlink", _XLINK_NS),
        ("dc", "http://purl.org/dc/elements/1.1/"),
        ("cc", "http://creativecommons.org/ns#"),
        ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#"),
        ("inkscape", "http://www.inkscape.org/namespaces/inkscape"),
        ("sodipodi", "http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd"),
    ]:
        try:
            ET.register_namespace(prefix, uri)
        except Exception:
            pass

    tree = ET.parse(svg_path)
    root = tree.getroot()
    svg_dir = os.path.dirname(svg_path)

    parent_map = {child: parent for parent in root.iter() for child in parent}

    to_replace = []
    for el in root.iter(f"{{{_SVG_NS}}}image"):
        href_raw = el.get(f"{{{_XLINK_NS}}}href") or el.get("href") or ""
        href = unquote(href_raw)
        if href.lower().endswith(".svg"):
            p = href if os.path.isabs(href) else os.path.join(svg_dir, href.replace("/", os.sep))
            p = os.path.normpath(p)
            if os.path.isfile(p):
                to_replace.append((el, p))

    if not to_replace:
        return None

    for image_el, img_path in to_replace:
        parent = parent_map.get(image_el)
        if parent is None:
            continue
        sub_inlined = _inline_svg_images(img_path, _depth + 1)
        if sub_inlined is not None:
            sub_root = ET.fromstring(sub_inlined)
        else:
            sub_root = ET.parse(img_path).getroot()
        for attr in ("x", "y", "width", "height"):
            val = image_el.get(attr)
            if val is not None:
                sub_root.set(attr, val)
        idx = list(parent).index(image_el)
        parent.remove(image_el)
        parent.insert(idx, sub_root)

    xml_str = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str).encode("utf-8")


def _svg_to_pdf(svg_path):
    import typst
    import tempfile

    pdf_path = os.path.splitext(svg_path)[0] + ".pdf"
    svg_dir = os.path.dirname(svg_path)
    project_dir = os.path.dirname(svg_dir)

    inlined = _inline_svg_images(svg_path)

    if inlined is not None:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".svg", dir=svg_dir)
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(inlined)
            rel = os.path.relpath(tmp_path, project_dir).replace("\\", "/")
            typ = f'#set page(width: auto, height: auto, margin: 0pt)\n#image("{rel}")\n'
            typst.compile(typ.encode(), output=pdf_path, root=project_dir, format="pdf")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    else:
        rel = os.path.relpath(svg_path, project_dir).replace("\\", "/")
        typ = f'#set page(width: auto, height: auto, margin: 0pt)\n#image("{rel}")\n'
        typst.compile(typ.encode(), output=pdf_path, root=project_dir, format="pdf")

    return pdf_path


class ExportSheetsToPdfOperator(bpy.types.Operator):
    """Convert all Bonsai sheet SVGs to PDF via typst, saved alongside the SVGs."""

    bl_idname = "bim.export_sheets_to_pdf"
    bl_label = "Convert All Sheets to PDF"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _get_ifc() is not None

    def execute(self, context):
        if not _ensure_typst():
            self.report(
                {"ERROR"},
                "typst Python package not found. "
                "Install it in Blender's Python environment: pip install typst",
            )
            return {"CANCELLED"}

        svgs = _find_sheet_svgs()
        if not svgs:
            ifc = _get_ifc()
            if ifc:
                sheets = [
                    d for d in ifc.by_type("IfcDocumentInformation")
                    if getattr(d, "Scope", None) == "SHEET"
                ]
                if sheets:
                    self.report(
                        {"WARNING"},
                        f"Found {len(sheets)} sheet(s) in IFC but SVG files not found on disk. "
                        "Build/export the sheets from Bonsai first.",
                    )
                else:
                    self.report({"WARNING"}, "No sheet IfcDocumentInformation (Scope='SHEET') found.")
            return {"CANCELLED"}

        ok = 0
        generated = []
        for svg in svgs:
            try:
                pdf = _svg_to_pdf(svg)
                generated.append(pdf)
                ok += 1
            except Exception as exc:
                self.report({"WARNING"}, f"{os.path.basename(svg)}: {exc}")

        self.report({"INFO"}, f"Converted {ok}/{len(svgs)} sheet(s) to PDF.")
        if generated:
            if len(generated) <= 3:
                for pdf in generated:
                    _open_file(pdf)
            else:
                _open_file(os.path.dirname(generated[0]))
        return {"FINISHED"}


classes = [ExportSheetsToPdfOperator]
