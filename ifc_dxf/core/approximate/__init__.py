"""Approximate pipeline: IFC-native traversal + Shapely wall-section extraction.

No OCC / HLR. See ifc_dxf/README.md, Pipeline A.
"""

from .pipeline import export_drawing, classify_elements, ElementRecord, WALL_MODES

__all__ = ["export_drawing", "classify_elements", "ElementRecord", "WALL_MODES"]
