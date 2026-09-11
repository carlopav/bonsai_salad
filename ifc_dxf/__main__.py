# Bonsai Salad -- ifc_dxf tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0
"""Export Bonsai drawings (IfcAnnotation, ObjectType DRAWING) to DXF from the
command line, without Blender.

Run from the repository root:
    python -m ifc_dxf model.ifc --list
    python -m ifc_dxf model.ifc                              # every drawing
    python -m ifc_dxf model.ifc -d "MY STOREY PLAN" -o plan.dxf
    python -m ifc_dxf model.ifc --pipeline approximate --audit

Headless, element selection takes the pure-ifcopenshell fallback path (README,
"Element selection according to the view limits"), so the result can differ
slightly from the export made inside Blender.
"""

import argparse
import os
import sys
import traceback

import ifcopenshell

# Concrete modules, not the ifc_dxf.core package root: the test harness stubs
# ifc_dxf.core without running its __init__.py.
from .core.ifc_query import find_drawings
from .core.approximate import export_drawing as export_drawing_approximate
from .core.accurate import export_drawing as export_drawing_accurate
from .core.audit import audit_dxf_file


def _safe_name(name):
    """Drawing name -> file stem, same rule as the Blender operator."""
    return "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name)


def _default_dir(ifc_path):
    """<ifc dir>/drawings when it exists (Bonsai's layout), else the IFC's dir."""
    base = os.path.dirname(os.path.abspath(ifc_path))
    drawings_dir = os.path.join(base, "drawings")
    return drawings_dir if os.path.isdir(drawings_dir) else base


def main(argv=None):
    # Drawing names can hold characters the console can't encode (e.g. "≤" on
    # a cp1252 Windows console): print a placeholder rather than crash.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    parser = argparse.ArgumentParser(
        prog="python -m ifc_dxf",
        description="Export Bonsai drawings from an IFC file to DXF.",
    )
    parser.add_argument("ifc", help="input IFC file")
    parser.add_argument("-d", "--drawing", action="append", metavar="NAME_OR_GUID",
                        help="drawing to export, by Name or GlobalId "
                             "(repeatable; default: every drawing)")
    parser.add_argument("-o", "--output",
                        help="output .dxf file (one drawing) or directory "
                             "(default: <ifc dir>/drawings if present, else <ifc dir>)")
    parser.add_argument("--pipeline", choices=("accurate", "approximate"),
                        default="accurate",
                        help="extraction pipeline (default: accurate, as in Blender)")
    parser.add_argument("--template",
                        help="DXF template (default: the bundled metric or imperial "
                             "template, by the project's length unit)")
    parser.add_argument("--fuse-by-material", action="store_true",
                        help="fuse touching sections only when they share the same "
                             "material assignment")
    parser.add_argument("--material-layers", action="store_true",
                        help="decompose layered walls into per-material-layer polygons "
                             "(approximate pipeline only)")
    parser.add_argument("--crease-angle", type=float, default=15.0, metavar="DEG",
                        help="minimum dihedral angle to draw a shared mesh edge "
                             "(default: 15)")
    parser.add_argument("--audit", action="store_true",
                        help="run ezdxf's structural audit on each written file; "
                             "exit 1 if it finds any issue")
    parser.add_argument("--list", action="store_true",
                        help="list the drawings in the file and exit")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.ifc):
        parser.error(f"IFC file not found: {args.ifc}")
    if args.template and not os.path.isfile(args.template):
        parser.error(f"template not found: {args.template}")
    if args.material_layers and args.pipeline != "approximate":
        parser.error("--material-layers needs --pipeline approximate")

    ifc = ifcopenshell.open(args.ifc)
    drawings = find_drawings(ifc)
    if not drawings:
        print(f"No drawings (IfcAnnotation with EPset_Drawing) in {args.ifc}",
              file=sys.stderr)
        return 1

    if args.list:
        for drawing, pset in drawings:
            print(f"{drawing.GlobalId}  {pset.get('TargetView', '?'):<20}"
                  f"{pset.get('HumanScale', 'NTS'):<12}  {drawing.Name or ''}")
        return 0

    selected = drawings
    if args.drawing:
        selected = [(d, p) for d, p in drawings
                    if d.Name in args.drawing or d.GlobalId in args.drawing]
        found = {key for d, _ in selected for key in (d.Name, d.GlobalId)}
        missing = [w for w in args.drawing if w not in found]
        if missing:
            parser.error(f"no drawing {', '.join(map(repr, missing))} (see --list)")

    if args.output and args.output.lower().endswith(".dxf"):
        if len(selected) > 1:
            parser.error("-o names one .dxf file but several drawings are selected; "
                         "give a directory instead")
        jobs = [(*selected[0], args.output)]
    else:
        out_dir = args.output or _default_dir(args.ifc)
        jobs, used = [], set()
        for drawing, pset in selected:
            stem = _safe_name(drawing.Name or "drawing")
            if stem.lower() in used:  # duplicate names must not overwrite each other
                stem = _safe_name(f"{stem}_{drawing.GlobalId}")
            used.add(stem.lower())
            jobs.append((drawing, pset, os.path.join(out_dir, stem + ".dxf")))

    try:
        import shapely  # noqa: F401
        wall_mode = "shapely"
    except ImportError:
        wall_mode = "flat"

    failed = 0
    for drawing, pset, path in jobs:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        print(f"{drawing.Name} -> {path}")
        try:
            if args.pipeline == "accurate":
                export_drawing_accurate(
                    ifc, drawing, pset, path,
                    template_path=args.template,
                    crease_angle_deg=args.crease_angle,
                    fuse_by_material=args.fuse_by_material,
                )
            else:
                export_drawing_approximate(
                    ifc, drawing, pset, path,
                    wall_mode=wall_mode,
                    template_path=args.template,
                    crease_angle_deg=args.crease_angle,
                    export_material_layers=args.material_layers,
                    fuse_by_material=args.fuse_by_material,
                )
        except Exception:
            traceback.print_exc()
            print(f"FAILED: {drawing.Name}", file=sys.stderr)
            failed += 1
            continue
        if args.audit:
            is_clean, report = audit_dxf_file(path)
            if not is_clean:
                print(f"Audit found issues in {path}:\n{report}", file=sys.stderr)
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
