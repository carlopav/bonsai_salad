"""
Integration tests for ifc_dxf export pipeline.

Each IFC file in tests/ifc_dxf/files/ is a self-contained test fixture
covering a specific scenario.  The parametrized test exports every drawing
found in the file and runs basic sanity checks.  Case-specific assertions
are added as needed for each fixture.

Run with:
    python -m pytest tests/ifc_dxf/test_export.py -v

Requires: pytest, ifcopenshell, ezdxf, shapely (optional but recommended).
"""

import os
import sys
import pytest
import ifcopenshell
import ezdxf

# conftest.py at repo root registered bpy/ifc_dxf stubs before any package
# __init__.py was imported, so normal ifc_dxf.core imports work here.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FILES_DIR = os.path.join(os.path.dirname(__file__), "files")
_OUT_DIR = os.path.join(_REPO_ROOT, "output", "test_runs")
_TEMPLATE_PATH = os.path.join(_REPO_ROOT, "ifc_dxf", "templates", "ifc_dxf_template_metric.dxf")

from ifc_dxf.core.ifc_query import find_drawings
# Import from the concrete subpackage, not the package root: the test harness
# (root conftest.py) stubs `ifc_dxf.core` as an empty namespace and never runs
# its real __init__.py, so the `export_drawing` alias defined there is absent.
from ifc_dxf.core.approximate import export_drawing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ifc_files():
    """Collect all .ifc fixtures in tests/ifc_dxf/files/."""
    if not os.path.isdir(_FILES_DIR):
        return []
    return sorted(
        f for f in os.listdir(_FILES_DIR) if f.lower().endswith(".ifc")
    )


def _output_path(ifc_name, drawing_name):
    os.makedirs(_OUT_DIR, exist_ok=True)
    safe = drawing_name.replace(" ", "_").replace("/", "-")
    stem = os.path.splitext(ifc_name)[0]
    return os.path.join(_OUT_DIR, f"{stem}__{safe}.dxf")


# ---------------------------------------------------------------------------
# Parametrized smoke test: export every drawing in every IFC fixture
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ifc_filename", _ifc_files())
def test_export_all_drawings(ifc_filename, tmp_path):
    """Export every drawing in the IFC fixture and verify the output DXF."""
    ifc_path = os.path.join(_FILES_DIR, ifc_filename)
    ifc = ifcopenshell.open(ifc_path)

    drawings = find_drawings(ifc)
    assert drawings, f"No drawings found in {ifc_filename}"

    for drawing, pset in drawings:
        out = _output_path(ifc_filename, drawing.Name or "unnamed")
        export_drawing(ifc, drawing, pset, out, wall_mode="shapely")

        assert os.path.isfile(out), f"Output DXF not created for {drawing.Name}"
        assert os.path.getsize(out) > 1000, f"Output DXF suspiciously small for {drawing.Name}"

        doc = ezdxf.readfile(out)
        msp = doc.modelspace()
        assert len(list(msp)) > 0, f"Model space is empty for {drawing.Name}"


# ---------------------------------------------------------------------------
# Case-specific assertions
# ---------------------------------------------------------------------------

class TestCase01BasicPlan:
    """test_ifc_01.ifc — basic 1:100 plan: walls, door, window, slab, furniture."""

    IFC = "test_ifc_01.ifc"

    @pytest.fixture(autouse=True)
    def export(self):
        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, self.IFC))
        drawings = find_drawings(ifc)
        drawing, pset = drawings[0]
        self.out = _output_path(self.IFC, drawing.Name)
        tpl = _TEMPLATE_PATH if os.path.isfile(_TEMPLATE_PATH) else None
        self.has_template = tpl is not None
        export_drawing(ifc, drawing, pset, self.out, wall_mode="shapely", template_path=tpl)
        self.doc = ezdxf.readfile(self.out)

    def test_dxf_has_lwpolylines(self):
        """Walls exported as LWPOLYLINE (shapely mode)."""
        msp = self.doc.modelspace()
        polys = [e for e in msp if e.dxftype() == "LWPOLYLINE"]
        assert len(polys) >= 1

    def test_dxf_has_inserts(self):
        """Door and windows exported as INSERT blocks."""
        msp = self.doc.modelspace()
        inserts = [e for e in msp if e.dxftype() == "INSERT"]
        assert len(inserts) >= 2

    def test_dxf_has_hatch(self):
        """Section walls produce HATCH fill."""
        msp = self.doc.modelspace()
        hatches = [e for e in msp if e.dxftype() == "HATCH"]
        assert len(hatches) >= 1

    def test_dxf_has_dimension(self):
        """Dimension annotation produces DIMENSION entity."""
        msp = self.doc.modelspace()
        dims = [e for e in msp if e.dxftype() == "DIMENSION"]
        assert len(dims) >= 1

    def test_dxf_passes_ezdxf_audit(self):
        """Exported DXF is structurally clean per ezdxf's Auditor."""
        from ifc_dxf.core.audit import audit_dxf_file
        is_clean, report = audit_dxf_file(self.out)
        assert is_clean, report

    def test_hatch_boundaries_have_no_duplicate_vertices(self):
        """No HATCH boundary polyline has consecutive duplicate vertices.

        ezdxf's Auditor does not check this, but BricsCAD's audit rejects it,
        so assert it explicitly (locks in _dedup_ring)."""
        msp = self.doc.modelspace()
        for hatch in (e for e in msp if e.dxftype() == "HATCH"):
            for path in hatch.paths:
                verts = getattr(path, "vertices", None)
                if not verts:
                    continue
                pts = [(round(v[0], 6), round(v[1], 6)) for v in verts]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    assert a != b, f"duplicate hatch vertex {a} on layer {hatch.dxf.layer}"

    def test_annotation_context_data_has_owner(self):
        """Every annotation context-data object has a non-null owner handle.

        Guards the Owner Id (0) defect BricsCAD's audit flagged and repaired."""
        ctx_types = (
            "ACDB_TEXTOBJECTCONTEXTDATA_CLASS",
            "ACDB_DIMENSIONOBJECTCONTEXTDATA_CLASS",
        )
        for obj in self.doc.objects:
            if obj.dxftype() in ctx_types:
                owner = obj.dxf.get("owner", None)
                assert owner not in (None, "0"), \
                    f"{obj.dxftype()} has invalid owner {owner!r}"

    def test_a1_layout_cartiglio_date(self):
        """A1 paper-space layout has a date TEXT entity (cartiglio filled)."""
        if not self.has_template:
            pytest.skip("Template not found — cartiglio test skipped")
        for layout_name in self.doc.layouts.names():
            if layout_name == "Model":
                continue
            layout = self.doc.layouts.get(layout_name)
            texts = [e for e in layout if e.dxftype() == "TEXT"]
            date_texts = [
                e for e in texts
                if e.dxf.get("text", "").count(".") == 2  # dd.mm.yyyy
            ]
            assert date_texts, "No date text found in paper-space layout"
            return
        pytest.skip("No paper-space layout in DXF")
