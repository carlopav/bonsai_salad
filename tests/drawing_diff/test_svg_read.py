"""SVG parsing: degenerate paths, GUIDs, even-odd and the model transform."""

import os

import pytest
import shapely
from svg_builder import ROTATED_PLANE, box, group, paper, polyline, ring, write_svg

from drawing_diff.core import svg_read

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "MY STOREY PLAN.svg")


def test_reads_canvas_and_section(tmp_path):
    path = write_svg(tmp_path / "d.svg", [group("IfcWall cut", box(0, 0, 1, 1))])
    canvas, (section,) = svg_read.read_sections(path)
    assert (canvas.width, canvas.height, canvas.data_scale) == ("500.0mm", "500.0mm", "1:100")
    assert canvas.view_box == "0 0 500.0 500.0"
    assert section.scale == 100.0
    assert section.plane[2][3] == 1.6
    assert len(section.polygons) == 1


def test_transform_to_model_coordinates(tmp_path):
    """A 10 mm paper square at 1:100 is one square metre in the model, and the
    Y of the paper grows downwards."""
    _, (section,) = svg_read.read_sections(write_svg(tmp_path / "d.svg", [group("IfcWall cut", box(2, 3, 3, 4))]))
    polygon = section.polygons[0].geometry
    assert polygon.area == pytest.approx(1.0)
    assert polygon.bounds == pytest.approx((2.0, 3.0, 3.0, 4.0))


def test_transform_follows_the_section_plane(tmp_path):
    """ifc:matrix3 alone only lands in the 2D frame of the section plane: with
    the plane rotated by 90 degrees the model coordinates rotate with it."""
    path = write_svg(tmp_path / "d.svg", [group("IfcWall cut", box(2, 3, 3, 4))], plane=ROTATED_PLANE)
    _, (section,) = svg_read.read_sections(path)
    assert section.polygons[0].geometry.bounds == pytest.approx((3.0, -3.0, 4.0, -2.0))


def test_real_drawing_lands_on_the_model_coordinates_of_its_ifc():
    """The walls of the sample drawing, read out of the SVG, cover exactly the
    XY extent that ifcopenshell.geom gives for the walls of SF.ifc, the model
    the drawing was cut from."""
    _, (section,) = svg_read.read_sections(SAMPLE)
    union = shapely.union_all([p.geometry for p in section.polygons])
    # A tenth of a millimetre on the building: paper coordinates are read to a
    # thousandth of a millimetre, which is 0.1 mm of model at 1:100.
    assert union.bounds == pytest.approx((-1.915, -1.607, 8.085, 3.393), abs=2e-4)
    assert union.area == pytest.approx(4.11, abs=0.005)


def test_degenerate_paths_are_counted_not_ignored(tmp_path):
    """The shapes the real plans contain: no `d` at all, `d="M Z"`, two point
    subpaths, and rings closed by repeating the first point instead of Z."""
    unclosed = ring([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)], close=False)
    path = write_svg(
        tmp_path / "d.svg",
        [
            group("IfcWall cut", None),
            group("IfcWall cut", "M Z"),
            group("IfcColumn cut", f"M{paper(0, 0)[0]},{paper(0, 0)[1]} L{paper(0.1, 0)[0]},{paper(0.1, 0)[1]} Z"),
            group("IfcWall cut", unclosed),
        ],
    )
    _, (section,) = svg_read.read_sections(path)
    assert len(section.polygons) == 1
    assert section.polygons[0].geometry.area == pytest.approx(1.0)
    assert section.discarded_subpaths == 2
    assert section.empty_groups == 3


def test_lowercase_close_command(tmp_path):
    path = write_svg(tmp_path / "d.svg", [group("IfcWall cut", box(0, 0, 1, 1).replace(" Z", " z"))])
    _, (section,) = svg_read.read_sections(path)
    assert section.polygons[0].geometry.area == pytest.approx(1.0)


def test_guids_come_from_the_class_tokens(tmp_path):
    """ifc:guid is missing on 250 of the 268 cut groups of the real plan: the
    merge writes up to three GUIDs as class tokens instead."""
    guids = "13361MWDv4yBQtXP$XMiOb 1442s4wbH86wSTrF0qoOtp 3DlZiNDu1EB9OWaYnXpfoq"
    classes = f"IfcWall material-null {guids} cut IfcMaterialLayer layer-material-Unknown"
    _, (section,) = svg_read.read_sections(write_svg(tmp_path / "d.svg", [group(classes, box(0, 0, 1, 1))]))
    assert section.polygons[0].guids == tuple(guids.split())
    assert section.polygons[0].ifc_class == "IfcWall"


def test_guids_come_from_the_attribute_too(tmp_path):
    """All 322 projection groups of the real plan carry ifc:guid instead."""
    path = write_svg(
        tmp_path / "d.svg",
        [group("IfcWall material-null projection", polyline([(0, 0), (5, 0)]), guid="0aUjmLwDn30w5g2iMShGx6")],
    )
    _, (section,) = svg_read.read_sections(path, linework=True)
    assert section.lines[0].guids == ("0aUjmLwDn30w5g2iMShGx6",)
    assert section.lines[0].ifc_class == "IfcWall"


def test_excluded_classes_are_counted(tmp_path):
    path = write_svg(
        tmp_path / "d.svg",
        [
            group("IfcFurniture cut", box(0, 0, 1, 1)),
            group("IfcSanitaryTerminal material-null cut", box(4, 0, 5, 1)),
            group("IfcWall cut", box(2, 0, 3, 1)),
        ],
    )
    _, (section,) = svg_read.read_sections(path)
    assert len(section.polygons) == 1
    assert section.empty_groups == 0
    assert dict(section.excluded_classes) == {"IfcFurniture": 1, "IfcSanitaryTerminal": 1}


def test_an_unknown_class_takes_part(tmp_path):
    """A wall exported as a proxy on one side only would be a false demolition:
    everything the drawing shows is compared, except what the filter names."""
    path = write_svg(tmp_path / "d.svg", [group("IfcBuildingElementProxy material-null cut", box(0, 0, 1, 1))])
    _, (section,) = svg_read.read_sections(path)
    assert len(section.polygons) == 1
    assert section.polygons[0].ifc_class == "IfcBuildingElementProxy"
    assert section.excluded_classes == {}


def test_an_emptied_filter_lets_the_furniture_back_in(tmp_path):
    path = write_svg(tmp_path / "d.svg", [group("IfcFurniture cut", box(0, 0, 1, 1))])
    _, (section,) = svg_read.read_sections(path, exclude=svg_read.parse_exclude(""))
    assert len(section.polygons) == 1
    assert section.excluded_classes == {}


def test_the_filter_also_takes_material_tokens(tmp_path):
    """The drawings carry material-<name> among the class tokens, so the filter
    reaches what has no class of its own."""
    path = write_svg(
        tmp_path / "d.svg",
        [
            group("IfcWall layer-material-Insulation cut", box(0, 0, 1, 1)),
            group("IfcWall layer-material-Concrete cut", box(2, 0, 3, 1)),
        ],
    )
    _, (section,) = svg_read.read_sections(path, exclude=("layer-material-Insulation",))
    assert [p.geometry.bounds[0] for p in section.polygons] == [pytest.approx(2.0)]
    assert dict(section.excluded_classes) == {"layer-material-Insulation": 1}


def test_a_merged_group_survives_the_class_of_what_it_was_merged_with(tmp_path):
    """Bonsai's merge unions the classes of the elements it joins: a wall fused
    with a piece of furniture must not leave with it."""
    path = write_svg(
        tmp_path / "d.svg",
        [
            group("IfcWall IfcFurniture material-null cut", box(0, 0, 5, 0.3)),
            group("IfcFurniture material-null cut", box(0, 3, 1, 4)),
        ],
    )
    _, (section,) = svg_read.read_sections(path)
    assert [p.ifc_class for p in section.polygons] == ["IfcWall"]
    assert dict(section.excluded_classes) == {"IfcFurniture": 1}


def test_the_filter_is_normalised(tmp_path):
    assert svg_read.parse_exclude(" IfcFurniture ,, IfcWall,") == ("IfcFurniture", "IfcWall")
    assert svg_read.parse_exclude("") == ()


def test_projection_groups_are_read_only_with_linework(tmp_path):
    """Projection paths are open: the ring parser drops them, and rightly so
    for the areas, so the lines get a reader of their own."""
    path = write_svg(
        tmp_path / "d.svg",
        [
            group("IfcWall cut", box(0, 0, 5, 0.3)),
            group("IfcWall projection", polyline([(0, 0), (5, 0), (5, 0.3)])),
        ],
    )
    _, (off,) = svg_read.read_sections(path)
    assert off.lines == []
    assert off.discarded_subpaths == 0
    _, (on,) = svg_read.read_sections(path, linework=True)
    assert len(on.lines) == 1
    assert on.lines[0].geometry.length == pytest.approx(5.3)
    assert len(on.polygons) == 1


def test_cut_and_projection_do_not_mix(tmp_path):
    """A closed projection path stays a line, an open cut path stays discarded."""
    path = write_svg(
        tmp_path / "d.svg",
        [
            group("IfcWall projection", box(0, 0, 1, 1)),
            group("IfcWall cut", polyline([(2, 0), (3, 0)])),
        ],
    )
    _, (section,) = svg_read.read_sections(path, linework=True)
    assert section.polygons == []
    assert len(section.lines) == 1
    assert section.lines[0].geometry.is_closed
    assert (section.discarded_subpaths, section.empty_groups) == (1, 1)


def test_a_projection_path_of_one_point_is_discarded_and_counted(tmp_path):
    x, y = paper(1, 1)
    path = write_svg(
        tmp_path / "d.svg",
        [group("IfcWall projection", f"M{x},{y}", f"M{x},{y} Z", polyline([(0, 0), (5, 0)]))],
    )
    _, (section,) = svg_read.read_sections(path, linework=True)
    assert len(section.lines) == 1
    assert section.discarded_subpaths == 2


def test_even_odd_subpaths_are_holes_at_any_depth(tmp_path):
    path = write_svg(
        tmp_path / "d.svg",
        [group("IfcWall cut", " ".join([box(0, 0, 4, 4), box(1, 1, 3, 3), box(1.5, 1.5, 2.5, 2.5)]))],
    )
    _, (section,) = svg_read.read_sections(path)
    assert sum(p.geometry.area for p in section.polygons) == pytest.approx(16 - 4 + 1)


def test_sibling_paths_fill_independently(tmp_path):
    """fill-rule is a per element rule: a path drawn over another one inside the
    same cut group is not its hole, and Bonsai does emit several paths per group
    (bim/module/drawing/operator.py:1647-1665)."""
    path = write_svg(tmp_path / "d.svg", [group("IfcWall cut", box(0, 0, 10, 10), box(3, 3, 6, 6))])
    _, (section,) = svg_read.read_sections(path)
    assert len(section.polygons) == 1
    assert len(section.polygons[0].geometry.interiors) == 0
    assert section.polygons[0].geometry.area == pytest.approx(100.0)


def test_self_intersecting_ring_is_recovered(tmp_path):
    bowtie = ring([(0, 0), (2, 2), (2, 0), (0, 2)])
    _, (section,) = svg_read.read_sections(write_svg(tmp_path / "d.svg", [group("IfcWall cut", bowtie)]))
    assert sum(p.geometry.area for p in section.polygons) == pytest.approx(2.0)


def test_vertical_planes_are_rejected(tmp_path):
    elevation = "[[1.0,0.0,0.0,0.0],[0.0,0.0,-1.0,0.0],[0.0,1.0,0.0,0.0],[0.0,0.0,0.0,1.0]]"
    path = write_svg(tmp_path / "d.svg", [group("IfcWall cut", box(0, 0, 1, 1))], plane=elevation)
    with pytest.raises(ValueError, match="not horizontal"):
        svg_read.read_sections(path)


def test_drawing_without_a_section_group(tmp_path):
    path = tmp_path / "d.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" data-scale="1:100"><g class="annotation"/></svg>')
    canvas, sections = svg_read.read_sections(path)
    assert sections == []
    assert canvas.data_scale == "1:100"
