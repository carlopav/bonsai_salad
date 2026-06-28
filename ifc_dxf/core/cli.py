"""Command-line interface for ifc_dxf: IFC -> DXF export (no Blender required)."""

import sys
import os
import argparse

import ifcopenshell

from .ifc_query import find_drawings
from .writer import export_drawing, render_preview, WALL_MODES


DEFAULT_IFC_PATH = os.path.join(
    r"C:\120grammi Dropbox\120grammi_lavori",
    r"26-TV-2T_Costruzioni-2026-Fontane\TV-2T-02a-PP",
    r"2T-Fontane-Modello-Ipotesi_12.ifc",
)


def main():
    """Entry point for the ifc_dxf command-line tool."""
    parser = argparse.ArgumentParser(
        description="ifc_dxf standalone -- IFC -> DXF (no Blender needed)"
    )
    parser.add_argument("--ifc",     default=DEFAULT_IFC_PATH,
                        help="Path to IFC file")
    parser.add_argument("--drawing", type=int, default=0,
                        help="Drawing index to export (default 0)")
    parser.add_argument("--out",     default=None,
                        help="Output .dxf path (default: alongside the IFC)")
    parser.add_argument("--list",    action="store_true",
                        help="List available drawings and exit")
    parser.add_argument("--no-preview", action="store_true",
                        help="Skip PNG preview rendering")
    parser.add_argument("--wall-mode", default="shapely",
                        choices=WALL_MODES,
                        help="Wall section mode: shapely (default) or flat")
    args = parser.parse_args()

    print(f"Loading IFC: {args.ifc}")
    ifc = ifcopenshell.open(args.ifc)

    drawings = find_drawings(ifc)
    if not drawings:
        print("ERROR: no drawings found (no EPset_Drawing pset in any IfcAnnotation).")
        sys.exit(1)

    print(f"\n{len(drawings)} drawing(s) found:")
    for i, (ann, pset) in enumerate(drawings):
        marker = ">>" if i == args.drawing else "  "
        tv = pset.get("TargetView", "?")
        hs = pset.get("HumanScale", "?")
        print(f"  {marker} [{i}]  {ann.Name or ann.GlobalId!r}  ({tv}  {hs})")

    if args.list:
        return

    if args.drawing >= len(drawings):
        print(f"\nERROR: drawing index {args.drawing} out of range (0-{len(drawings)-1}).")
        sys.exit(1)

    drawing, pset = drawings[args.drawing]
    name = (drawing.Name or drawing.GlobalId).replace("/", "_").replace(" ", "_")
    print(f"\nExporting [{args.drawing}]: {drawing.Name or drawing.GlobalId}")

    if args.out:
        out_path = args.out
    else:
        # Output alongside this script (repo root), in an output/ sub-directory
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out_dir = os.path.join(repo_root, "output")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{name}.dxf")

    export_drawing(ifc, drawing, pset, out_path, wall_mode=args.wall_mode)

    if not args.no_preview:
        render_preview(out_path)


if __name__ == "__main__":
    main()
