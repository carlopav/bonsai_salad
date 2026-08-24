# Bonsai Salad — sheets_to_pdf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import os
import sys

import bpy
from bonsai import tool

from .core.svg import prepare


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


def _sheet_identification(doc):
    return getattr(doc, "Identification", None) or getattr(doc, "DocumentId", None) or ""


def _find_sheet_svgs():
    """Built sheets, not layouts: only the build embeds drawings, schedules and the
    titleblock into a single SVG."""
    ifc = tool.Ifc.get()
    if ifc is None:
        return [], []

    found = []
    unbuilt = []
    for doc in ifc.by_type("IfcDocumentInformation"):
        if getattr(doc, "Scope", None) != "SHEET":
            continue

        ident = _sheet_identification(doc)
        name = getattr(doc, "Name", None) or ""

        path = None
        try:
            path = tool.Drawing.get_document_uri(doc, "SHEET")
        except Exception:
            path = None

        if not path:
            try:
                path = tool.Ifc.resolve_uri(tool.Drawing.get_default_sheet_path(ident, name))
            except Exception:
                path = None

        if path and os.path.isfile(path):
            found.append(os.path.normpath(path))
        else:
            unbuilt.append(f"{ident} - {name}".strip(" -"))

    return found, unbuilt


def _svg_to_pdf(svg_path):
    import tempfile

    import typst

    pdf_path = os.path.splitext(svg_path)[0] + ".pdf"
    svg_dir = os.path.dirname(svg_path)
    project_dir = os.path.dirname(svg_dir)

    def compile_from(path):
        rel = os.path.relpath(path, project_dir).replace("\\", "/")
        typ = f'#set page(width: auto, height: auto, margin: 0pt)\n#image("{rel}")\n'
        typst.compile(typ.encode(), output=pdf_path, root=project_dir, format="pdf")

    prepared = prepare(svg_path)
    if prepared is None:
        compile_from(svg_path)
        return pdf_path

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".svg", dir=svg_dir)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(prepared)
        compile_from(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return pdf_path


class ExportSheetsToPdfOperator(bpy.types.Operator):
    """Convert all Bonsai sheet SVGs to PDF via typst, saved alongside the SVGs."""

    bl_idname = "bim.export_sheets_to_pdf"
    bl_label = "Convert All Sheets to PDF"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return tool.Ifc.get() is not None

    def execute(self, context):
        if not _ensure_typst():
            self.report(
                {"ERROR"},
                "typst Python package not found. "
                "Install it in Blender's Python environment: pip install typst",
            )
            return {"CANCELLED"}

        svgs, unbuilt = _find_sheet_svgs()
        if not svgs:
            if unbuilt:
                self.report(
                    {"WARNING"},
                    f"{len(unbuilt)} sheet(s) not built yet: {', '.join(unbuilt)}. "
                    "Run Create Sheets in Bonsai first.",
                )
            else:
                self.report({"WARNING"}, "No sheet IfcDocumentInformation (Scope='SHEET') found.")
            return {"CANCELLED"}

        if unbuilt:
            self.report(
                {"WARNING"},
                f"Skipping {len(unbuilt)} sheet(s) not built yet: {', '.join(unbuilt)}.",
            )

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
                    bpy.ops.wm.path_open(filepath=pdf)
            else:
                bpy.ops.wm.path_open(filepath=os.path.dirname(generated[0]))
        return {"FINISHED"}


classes = [ExportSheetsToPdfOperator]
