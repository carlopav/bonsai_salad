"""ifc_dxf.core -- public API for the IFC -> DXF export pipeline.

Pure Python (ifcopenshell, ezdxf, shapely, numpy) — no bpy dependency.

Two independent extraction pipelines share the same DXF writer (dxf_writer.py),
template handling, materials classification and annotation writer:
- approximate: IFC-native traversal + Shapely wall-section extraction (Pipeline A)
- accurate:    OCC/HLR-based, matches Bonsai's own SVG export (Pipeline B)
"""

from .ifc_query import find_drawings, find_plan_repr
from .approximate import export_drawing as export_drawing_approximate
from .accurate import export_drawing as export_drawing_accurate

# Backwards-compatible default: existing callers importing `export_drawing`
# get the approximate pipeline (unchanged behaviour).
export_drawing = export_drawing_approximate

__all__ = [
    "export_drawing",
    "export_drawing_approximate",
    "export_drawing_accurate",
    "find_drawings",
    "find_plan_repr",
]
