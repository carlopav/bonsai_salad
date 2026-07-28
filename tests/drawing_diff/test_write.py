"""SVG and DXF output of the comparison."""

import ezdxf
import pytest
import shapely
from lxml import etree
from svg_builder import box, group, polyline, write_svg

from drawing_diff.core import diff, dxf_write, svg_read, svg_write

SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture
def comparison(tmp_path):
    """An existing 5 m wall shortened to 3 m by the project, plus a new wall."""
    _, existing = svg_read.read_sections(write_svg(tmp_path / "e.svg", [group("IfcWall cut", box(0, 0, 5, 0.3))]))
    canvas, project = svg_read.read_sections(
        write_svg(
            tmp_path / "p.svg",
            [
                group("IfcWall cut", " ".join([box(0, 0, 3, 0.3), box(1, 0.05, 2, 0.25)])),
                group("IfcWall cut", box(0, 3, 5, 3.3)),
            ],
        )
    )
    return diff.compare(existing, project), project[0], canvas


@pytest.fixture
def linework_comparison(tmp_path):
    """The same wall in both states, plus a projection line only the project
    has: one unchanged run and one new."""
    groups = [group("IfcWall cut", box(0, 0, 5, 0.3)), group("IfcWall projection", polyline([(0, 0), (5, 0)]))]
    _, existing = svg_read.read_sections(write_svg(tmp_path / "le.svg", groups), linework=True)
    canvas, project = svg_read.read_sections(
        write_svg(tmp_path / "lp.svg", [*groups, group("IfcWall projection", polyline([(0, 2), (5, 2)]))]),
        linework=True,
    )
    return diff.compare(existing, project, linework=True), project[0], canvas


def test_svg_keeps_the_project_canvas_and_paints_the_three_states(tmp_path, comparison):
    result, section, canvas = comparison
    path = tmp_path / "raffronto.svg"
    svg_write.write(path, result, section, canvas)

    root = etree.parse(str(path)).getroot()
    assert (root.get("width"), root.get("viewBox"), root.get("data-scale")) == (
        canvas.width,
        canvas.view_box,
        canvas.data_scale,
    )
    style = root.find(f"{SVG_NS}defs/{SVG_NS}style").text
    assert "#F5C518" in style and "#E30613" in style and "#D2D2D2" in style

    section_group = root.find(f"{SVG_NS}g")
    assert section_group.get("class") == "section"
    assert section_group.get("{http://www.ifcopenshell.org/ns}matrix3").startswith("[[10.000000,0.000000,250")
    assert [g.get("id") for g in section_group] == ["diff-invariato", "diff-demolizione", "diff-nuovo"]

    counts = {g.get("id"): len(g.findall(f"{SVG_NS}path")) for g in section_group}
    assert counts == {
        "diff-invariato": len(result.unchanged),
        "diff-demolizione": len(result.demolished),
        "diff-nuovo": len(result.added),
    }


def test_svg_geometry_goes_back_to_paper_millimetres(tmp_path, comparison):
    """The demolished stretch runs from x 3 m to 5 m: at 1:100 with the sample
    matrix3 that is 280 to 300 mm on paper, with the paper Y flipped."""
    result, section, canvas = comparison
    path = tmp_path / "raffronto.svg"
    svg_write.write(path, result, section, canvas)

    root = etree.parse(str(path)).getroot()
    demolished = root.find(f".//{SVG_NS}g[@id='diff-demolizione']")
    subpaths = [s for p in demolished.findall(f"{SVG_NS}path") for s in p.get("d").split("M")[1:]]
    boxes = []
    for subpath in subpaths:
        coords = [tuple(float(v) for v in t.split(",")) for t in subpath.strip(" Z").replace("L", "").split()]
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    assert (280.0, 247.0, 300.0, 250.0) in boxes


def test_svg_writes_holes_as_extra_subpaths(tmp_path, comparison):
    result, section, canvas = comparison
    path = tmp_path / "raffronto.svg"
    svg_write.write(path, result, section, canvas)

    root = etree.parse(str(path)).getroot()
    unchanged = root.find(f".//{SVG_NS}g[@id='diff-invariato']")
    d = unchanged.find(f"{SVG_NS}path").get("d")
    assert d.count("M") == 2 and d.count("Z") == 2


def test_svg_has_no_line_groups_unless_asked_for(tmp_path, comparison):
    result, section, canvas = comparison
    path = tmp_path / "raffronto.svg"
    svg_write.write(path, result, section, canvas)

    root = etree.parse(str(path)).getroot()
    assert [g.get("id") for g in root.find(f"{SVG_NS}g")] == ["diff-invariato", "diff-demolizione", "diff-nuovo"]
    assert root.get("data-diff-linework") == "false"


def test_svg_draws_the_cuts_over_the_linework(tmp_path, linework_comparison):
    """The section cut is the subject of the drawing: it covers what is
    projected behind it, the way a poché does."""
    result, section, canvas = linework_comparison
    path = tmp_path / "raffronto.svg"
    settings = svg_write.Settings(linework=True, exclude="IfcFurniture")
    svg_write.write(path, result, section, canvas, settings)

    root = etree.parse(str(path)).getroot()
    # In SVG the later element wins: the fills come last, so they are on top.
    assert [g.get("id") for g in root.find(f"{SVG_NS}g")] == [
        "diff-proiezione-invariata",
        "diff-proiezione-demolita",
        "diff-proiezione-nuova",
        "diff-invariato",
        "diff-demolizione",
        "diff-nuovo",
    ]
    new = root.find(f".//{SVG_NS}g[@id='diff-proiezione-nuova']")
    d = new.find(f"{SVG_NS}path").get("d")
    assert "Z" not in d
    assert d == "M250.0000,230.0000 L300.0000,230.0000"
    assert "stroke-width: 0.25" in root.find(f"{SVG_NS}defs/{SVG_NS}style").text


def test_the_settings_are_written_on_the_root_and_read_back(tmp_path, linework_comparison):
    result, section, canvas = linework_comparison
    path = tmp_path / "raffronto.svg"
    settings = svg_write.Settings(linework=True, exclude="IfcFurniture, material-Glass")
    svg_write.write(path, result, section, canvas, settings)

    root = etree.parse(str(path)).getroot()
    assert root.get("data-diff-exclude") == "IfcFurniture, material-Glass"
    assert svg_write.read_settings(path, svg_write.Settings()) == settings


def test_a_missing_comparison_falls_back_on_the_panel(tmp_path):
    panel = svg_write.Settings(linework=True, exclude="IfcWall")
    assert svg_write.read_settings(tmp_path / "gone.svg", panel) == panel


def test_an_older_comparison_is_recognised_by_its_groups(tmp_path):
    """Comparisons written before the attributes: the linework is visible in
    the file, the filter is not, so the panel answers for it."""
    path = tmp_path / "old.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" data-scale="1:100">'
        '<g class="section"><g id="diff-invariato"/><g id="diff-proiezione-nuova"/></g></svg>'
    )
    settings = svg_write.read_settings(path, svg_write.Settings(linework=False, exclude="IfcFurniture"))
    assert settings == svg_write.Settings(linework=True, exclude="IfcFurniture")

    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" data-scale="1:100"><g id="diff-invariato"/></svg>')
    assert svg_write.read_settings(path, svg_write.Settings(exclude="IfcFurniture")).linework is False


def test_dxf_has_the_three_layers_in_model_metres(tmp_path, comparison):
    result, _, _ = comparison
    path = tmp_path / "raffronto.dxf"
    dxf_write.write_dxf(path, result, svg_write.Colors())

    doc = ezdxf.readfile(str(path))
    assert doc.units == ezdxf.units.M
    assert {layer.dxf.name: layer.color for layer in doc.layers if layer.dxf.name.startswith("DIFF_")} == {
        "DIFF_ESISTENTE": 8,
        "DIFF_DEMOLIZIONI": 2,
        "DIFF_NUOVE_COSTRUZIONI": 1,
    }

    msp = doc.modelspace()
    hatches = {
        name: [h for h in msp.query("HATCH") if h.dxf.layer == name]
        for name in ("DIFF_ESISTENTE", "DIFF_DEMOLIZIONI", "DIFF_NUOVE_COSTRUZIONI")
    }
    assert [len(hatches[name]) for name in hatches] == [
        len(result.unchanged),
        len(result.demolished),
        len(result.added),
    ]
    assert _hatch_area(hatches["DIFF_NUOVE_COSTRUZIONI"][0]) == pytest.approx(result.area_new_m2, abs=1e-6)
    assert sum(_hatch_area(h) for h in hatches["DIFF_ESISTENTE"]) == pytest.approx(result.area_unchanged_m2, abs=1e-6)
    assert len(hatches["DIFF_ESISTENTE"][0].paths) == 2  # the opening comes through as a hole


def test_dxf_writes_the_linework_as_open_polylines(tmp_path, linework_comparison):
    result, _, _ = linework_comparison
    path = tmp_path / "raffronto.dxf"
    dxf_write.write_dxf(path, result, svg_write.Colors())

    msp = ezdxf.readfile(str(path)).modelspace()
    # Same order as the SVG, on the one layer that carries both: the projection
    # first, the cut hatch over it.
    kinds = [e.dxftype() for e in msp if e.dxf.layer == "DIFF_ESISTENTE"]
    assert kinds.index("LWPOLYLINE") < kinds.index("HATCH")
    new = [p for p in msp.query("LWPOLYLINE") if p.dxf.layer == "DIFF_NUOVE_COSTRUZIONI"]
    assert len(new) == 1
    assert not new[0].closed
    assert [(round(x, 3), round(y, 3)) for x, y, *_ in new[0]] == [(0.0, 2.0), (5.0, 2.0)]
    unchanged = [p for p in msp.query("LWPOLYLINE") if p.dxf.layer == "DIFF_ESISTENTE"]
    # The cut polygon's own boundary polyline, plus the unchanged projection.
    assert len(unchanged) == len(result.unchanged) + len(result.lines_unchanged)


def test_the_colours_travel_with_the_comparison(tmp_path, comparison):
    """On a comparison drawing the colours are the legend: a regeneration must
    repaint it as it was, not as the panel happens to be set now."""
    result, section, canvas = comparison
    path = tmp_path / "raffronto.svg"
    settings = svg_write.Settings(colors=svg_write.Colors(unchanged="#111111", demolished="#222222", added="#333333"))
    svg_write.write(path, result, section, canvas, settings)

    root = etree.parse(str(path)).getroot()
    assert root.get("data-diff-colors") == "#111111,#222222,#333333"
    assert "#222222" in root.find(f"{SVG_NS}defs/{SVG_NS}style").text
    assert svg_write.read_settings(path, svg_write.Settings()).colors == settings.colors


def test_a_comparison_without_colours_written_keeps_the_fallback(tmp_path):
    path = tmp_path / "old.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" data-scale="1:100"><g id="diff-invariato"/></svg>')
    fallback = svg_write.Settings(colors=svg_write.Colors(unchanged="#ABCDEF"))
    assert svg_write.read_settings(path, fallback).colors == fallback.colors


def _hatch_area(hatch):
    exterior, *holes = ([(v[0], v[1]) for v in path.vertices] for path in hatch.paths)
    return shapely.Polygon(exterior, holes).area
