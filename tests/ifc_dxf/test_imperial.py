"""Imperial output: Bonsai's dimension text, the drawing's own annotation
scale, and the imperial template with its sheet picked by drawing size."""

import os
import re

import ezdxf
import ifcopenshell
import ifcopenshell.util.unit
import pytest

from ifc_dxf.core.ifc_query import find_drawings
from ifc_dxf.core.annotations import _format_imperial_length
from ifc_dxf.core.dxf_template import _select_sheet, default_template
from ifc_dxf.core.approximate import export_drawing

_FILES_DIR = os.path.join(os.path.dirname(__file__), "files")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_IMPERIAL_TEMPLATE = os.path.join(_REPO_ROOT, "ifc_dxf", "templates",
                                  "ifc_dxf_template_imperial.dxf")
FOOT = 0.3048


# Dimension lengths of a real 3/8"=1'-0" plan (Restaurant_E_Washington,
# ImperialPrecision 1/2) and the text Bonsai's SVG shows for each.
@pytest.mark.parametrize("feet, text", [
    (3.0, "3' - 0"),
    (2.75, "2' - 9\""),
    (5.579, "5' - 7\""),
    (7.667, "7' - 8\""),
    (13.937, "13' - 11\""),
    (3.606, "3' - 7 1/2\""),
    (3.308, "3' - 3 1/2\""),
    (8.021, "8' - 0 1/2\""),
    (3.11, "3' - 1 1/2\""),
    (1.5, "1' - 6\""),
])
def test_dimension_text_matches_bonsai(feet, text):
    assert _format_imperial_length(feet * FOOT, "FEET", True, "1/2") == text


def test_zero_inches_can_be_suppressed():
    assert _format_imperial_length(3.0 * FOOT, "FEET", True, "1/2",
                                   suppress_zero_inches=True) == "3'"


@pytest.mark.parametrize("unit_scale, name", [
    (FOOT, "imperial"), (0.0254, "imperial"), (1.0, "metric"), (0.001, "metric"),
])
def test_default_template_follows_the_unit_system(unit_scale, name):
    assert default_template(unit_scale).endswith(f"ifc_dxf_template_{name}.dxf")


@pytest.mark.parametrize("width_in, height_in, sheet, fits", [
    (15.0, 9.0, "ANSI B 11x17", True),
    (27.4, 22.5, "ARCH D 24x36", True),   # the real plan: 696.7 x 572.2 mm
    (40.0, 28.0, "ARCH E1 30x42", True),
    (60.0, 40.0, "ARCH E 36x48", False),  # nothing holds it: the largest
])
def test_smallest_sheet_that_holds_the_drawing(width_in, height_in, sheet, fits):
    doc = ezdxf.readfile(_IMPERIAL_TEMPLATE)
    kept, did_fit = _select_sheet(doc, width_in * 0.0254, height_in * 0.0254, FOOT)
    assert (kept, did_fit) == (sheet, fits)
    assert [n for n in doc.layouts.names() if n != "Model"] == [sheet]


class TestFeetProject:
    """test_ifc_01.ifc converted to feet, exported with no template given."""

    def _export(self, tmp_path, **pset_overrides):
        ifc = ifcopenshell.util.unit.convert_file_length_units(
            ifcopenshell.open(os.path.join(_FILES_DIR, "test_ifc_01.ifc")), "FOOT")
        drawing, pset = find_drawings(ifc)[0]
        out = str(tmp_path / "plan_ft.dxf")
        export_drawing(ifc, drawing, dict(pset, **pset_overrides), out, wall_mode="shapely")
        return drawing, ezdxf.readfile(out)

    def test_imperial_template_and_one_filled_sheet(self, tmp_path):
        drawing, doc = self._export(tmp_path)
        assert doc.header["$INSUNITS"] == 2
        assert "dimensions_imperial" in doc.dimstyles
        sheets = [n for n in doc.layouts.names() if n != "Model"]
        assert len(sheets) == 1 and sheets[0].startswith(("ANSI", "ARCH"))
        layout = doc.layouts.get(sheets[0])
        texts = [e.dxf.text if e.dxftype() == "TEXT" else e.text
                 for e in layout if e.dxftype() in ("TEXT", "MTEXT")]
        assert not [t for t in texts if "{{" in t or "\\{\\{" in t], texts
        assert drawing.Name in texts

    def test_dimension_text_is_written_in_feet_and_inches(self, tmp_path):
        _drawing, doc = self._export(tmp_path)
        dims = list(doc.modelspace().query("DIMENSION"))
        assert dims
        for dim in dims:
            assert re.match(r"^\d+' - \d", dim.dxf.text), dim.dxf.text

    def test_drawing_scale_is_the_current_annotation_scale(self, tmp_path):
        """3/8"=1'-0" (1:32) is in no metric scale list: CANNOSCALE named a
        scale the list lacked, and no text got its annotative data."""
        name = '3/8"=1\'-0"'
        _drawing, doc = self._export(tmp_path, Scale="1/32", HumanScale=name)
        assert name in doc.rootdict["ACAD_SCALELIST"]
        variables = doc.rootdict["AcDbVariableDictionary"]
        assert variables.get("CANNOSCALE").dxf.value == name
        texts = list(doc.modelspace().query("TEXT"))
        assert texts
        for text in texts:
            assert text.has_extension_dict
            assert "AcDbContextDataManager" in text.get_extension_dict().dictionary
