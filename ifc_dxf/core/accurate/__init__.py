"""Accurate pipeline: OCC/HLR-based, matches Bonsai's own SVG export.

See ifc_dxf/README.md, Pipeline B.
"""

from .pipeline import export_drawing

__all__ = ["export_drawing"]
