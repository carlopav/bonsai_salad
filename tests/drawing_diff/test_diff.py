"""Booleans between two drawings: the cases a comparison table has to survive."""

import os

import pytest
from svg_builder import PLANE, box, group, polyline, write_svg

from drawing_diff.core import diff, svg_read

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "MY STOREY PLAN.svg")


def sections(path, linework=False):
    return svg_read.read_sections(path, linework=linework)[1]


def drawing(tmp_path, name, *groups, **kwargs):
    linework = kwargs.pop("linework", False)
    return sections(write_svg(tmp_path / f"{name}.svg", groups, **kwargs), linework)


def wall(x0, y0, x1, y1, guid="0aUjmLwDn30w5g2iMShGx6", ifc_class="IfcWall"):
    return group(f"{ifc_class} material-null {guid} cut", box(x0, y0, x1, y1))


def wall_projection(x0, y0, x1, y1):
    return group("IfcWall material-null projection", polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]))


def test_identical_drawings_are_all_unchanged(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    project = drawing(tmp_path, "p", wall(0, 0, 5, 0.3))
    result = diff.compare(existing, project)
    assert result.area_unchanged_m2 == pytest.approx(1.5)
    assert result.demolished == []
    assert result.added == []


def test_wall_only_in_the_existing_state_is_demolished(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3), wall(0, 3, 5, 3.3))
    project = drawing(tmp_path, "p", wall(0, 0, 5, 0.3))
    result = diff.compare(existing, project)
    assert result.area_demolished_m2 == pytest.approx(1.5)
    assert len(result.demolished) == 1
    assert result.added == []


def test_wall_only_in_the_project_is_new(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    project = drawing(tmp_path, "p", wall(0, 0, 5, 0.3), wall(0, 3, 5, 3.3))
    result = diff.compare(existing, project)
    assert result.area_new_m2 == pytest.approx(1.5)
    assert result.demolished == []


def test_shortened_wall_only_yields_the_removed_stretch(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    project = drawing(tmp_path, "p", wall(0, 0, 3, 0.3))
    result = diff.compare(existing, project)
    assert result.area_demolished_m2 == pytest.approx(0.6)
    assert result.demolished[0].bounds == pytest.approx((3.0, 0.0, 5.0, 0.3))
    assert result.area_unchanged_m2 == pytest.approx(0.9)


def test_new_opening_in_an_existing_wall_is_demolished(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    holed = group("IfcWall 0aUjmLwDn30w5g2iMShGx6 cut", " ".join([box(0, 0, 5, 0.3), box(2, 0.05, 3, 0.25)]))
    project = drawing(tmp_path, "p", holed)
    result = diff.compare(existing, project)
    assert result.area_demolished_m2 == pytest.approx(0.2, abs=1e-6)
    assert result.added == []


def test_filled_opening_is_new(tmp_path):
    holed = group("IfcWall 0aUjmLwDn30w5g2iMShGx6 cut", " ".join([box(0, 0, 5, 0.3), box(2, 0.05, 3, 0.25)]))
    existing = drawing(tmp_path, "e", holed)
    project = drawing(tmp_path, "p", wall(0, 0, 5, 0.3))
    result = diff.compare(existing, project)
    assert result.area_new_m2 == pytest.approx(0.2, abs=1e-6)
    assert result.demolished == []


def test_same_wall_rebuilt_with_another_guid_is_unchanged(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3, guid="0aUjmLwDn30w5g2iMShGx6"))
    project = drawing(tmp_path, "p", wall(0, 0, 5, 0.3, guid="3ZckJYA$jDexq3I3v01lLI"))
    result = diff.compare(existing, project)
    assert result.area_unchanged_m2 == pytest.approx(1.5)
    assert (result.demolished, result.added) == ([], [])


def test_material_layers_dissolve_into_the_monolithic_wall(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    project = drawing(
        tmp_path,
        "p",
        group("IfcWall IfcMaterialLayer layer-material-A cut", box(0, 0, 5, 0.1)),
        group("IfcWall IfcMaterialLayer layer-material-B cut", box(0, 0.1, 5, 0.2)),
        group("IfcWall IfcMaterialLayer layer-material-C cut", box(0, 0.2, 5, 0.3)),
    )
    result = diff.compare(existing, project)
    assert len(result.unchanged) == 1
    assert len(result.unchanged[0].interiors) == 0
    assert result.area_unchanged_m2 == pytest.approx(1.5)
    assert (result.demolished, result.added) == ([], [])


def test_different_matrix3_same_plane_still_compares(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    project = sections(
        write_svg(
            tmp_path / "p.svg",
            [group("IfcWall cut", box(0, 0, 3, 0.3, matrix3=(10.0, 100.0, 300.0)))],
            matrix3=(10.0, 100.0, 300.0),
        )
    )
    result = diff.compare(existing, project)
    assert result.area_demolished_m2 == pytest.approx(0.6)
    assert result.area_unchanged_m2 == pytest.approx(0.9)


def test_same_rotation_but_shifted_origin_is_an_error(tmp_path):
    shifted = PLANE.replace("[1.000000,0.000000,0.000000,0.000000]", "[1.000000,0.000000,0.000000,0.500000]", 1)
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    project = drawing(tmp_path, "p", wall(0, 0, 5, 0.3), plane=shifted)
    with pytest.raises(diff.DiffError, match="different planes"):
        diff.compare(existing, project)


def test_a_model_in_millimetres_is_an_error(tmp_path):
    """Everything here is stated in metres: a model in millimetres would be off
    by a factor of a thousand on every area and every tolerance, in silence."""
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    project = drawing(
        tmp_path,
        "p",
        group("IfcWall cut", box(0, 0, 5, 0.3, matrix3=(0.01, 250.0, 250.0))),
        matrix3=(0.01, 250.0, 250.0),
    )
    with pytest.raises(diff.DiffError, match="not in metres"):
        diff.compare(existing, project)


def test_three_millimetre_sliver_is_dropped_and_three_centimetres_kept(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3))
    noise = drawing(tmp_path, "n", wall(0, 0, 5, 0.303))
    thicker = drawing(tmp_path, "t", wall(0, 0, 5, 0.33))
    assert diff.compare(existing, noise).added == []
    kept = diff.compare(existing, thicker)
    assert kept.area_new_m2 == pytest.approx(0.15, abs=1e-6)


def test_real_drawing_against_itself_is_all_unchanged():
    result = diff.compare(sections(SAMPLE), sections(SAMPLE))
    assert (result.demolished, result.added) == ([], [])
    assert result.area_unchanged_m2 == pytest.approx(4.11, abs=0.01)
    assert result.existing_discarded == 0
    assert result.existing_empty_groups == 0


def test_linework_stays_out_unless_asked_for(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3), wall_projection(0, 0, 5, 0.3))
    project = drawing(tmp_path, "p", wall(0, 0, 5, 0.3), wall_projection(0, 3, 5, 3.3))
    result = diff.compare(existing, project)
    assert (result.lines_unchanged, result.lines_demolished, result.lines_added) == ([], [], [])
    assert result.length_unchanged_m == 0.0


def test_identical_projection_is_unchanged_linework(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3), wall_projection(0, 0, 5, 0.3), linework=True)
    project = drawing(tmp_path, "p", wall(0, 0, 5, 0.3), wall_projection(0, 0, 5, 0.3), linework=True)
    result = diff.compare(existing, project, linework=True)
    assert result.length_unchanged_m == pytest.approx(10.6)
    assert (result.lines_demolished, result.lines_added) == ([], [])


def test_a_moved_wall_is_demolished_linework_where_it_was(tmp_path):
    existing = drawing(tmp_path, "e", wall(0, 0, 5, 0.3), wall_projection(0, 0, 5, 0.3), linework=True)
    project = drawing(tmp_path, "p", wall(0, 3, 5, 3.3), wall_projection(0, 3, 5, 3.3), linework=True)
    result = diff.compare(existing, project, linework=True)
    assert result.length_demolished_m == pytest.approx(10.6)
    assert result.length_new_m == pytest.approx(10.6)
    assert result.lines_unchanged == []
    assert max(y for _, y in result.lines_demolished[0].coords) == pytest.approx(0.3)
    assert min(y for _, y in result.lines_added[0].coords) == pytest.approx(3.0)


def test_a_projection_shifted_under_the_tolerance_is_unchanged(tmp_path):
    """Three millimetres of remesh noise on the building, half the tolerance."""
    existing = drawing(tmp_path, "e", wall_projection(0, 0, 5, 0.3), linework=True)
    project = drawing(tmp_path, "p", wall_projection(0, 0.003, 5, 0.303), linework=True)
    result = diff.compare(existing, project, linework=True)
    assert result.length_unchanged_m == pytest.approx(10.6, abs=0.02)
    assert (result.lines_demolished, result.lines_added) == ([], [])


def test_a_fragment_shorter_than_the_minimum_is_dropped(tmp_path):
    """A line one centimetre longer than the other state's leaves a stub half
    of which the tolerance already covers: under MIN_LENGTH_M, and only noise
    on the table."""
    edge = group("IfcWall projection", polyline([(0, 0), (5, 0)]))
    existing = drawing(tmp_path, "e", edge, linework=True)
    project = drawing(tmp_path, "p", group("IfcWall projection", polyline([(0, 0), (5.01, 0)])), linework=True)
    assert diff.compare(existing, project, linework=True).lines_added == []
    longer = drawing(tmp_path, "l", group("IfcWall projection", polyline([(0, 0), (5.5, 0)])), linework=True)
    assert diff.compare(existing, longer, linework=True).length_new_m == pytest.approx(0.495)


def test_a_drawing_without_projections_is_an_error(tmp_path):
    """One drawing generated without its linework would have the other's whole
    projection reported as new, or as demolished, without a word."""
    with_lines = drawing(tmp_path, "e", wall(0, 0, 5, 0.3), wall_projection(0, 0, 5, 0.3), linework=True)
    without = drawing(tmp_path, "p", wall(0, 0, 5, 0.3), linework=True)

    with pytest.raises(diff.DiffError, match="no projection linework"):
        diff.compare(with_lines, without, linework=True)
    with pytest.raises(diff.DiffError, match="no projection linework"):
        diff.compare(without, with_lines, linework=True)
    # Without the linework the same pair compares just fine.
    assert diff.compare(without, with_lines).area_unchanged_m2 == pytest.approx(1.5)


def test_real_drawing_linework_against_itself_has_nothing_to_report():
    result = diff.compare(sections(SAMPLE, linework=True), sections(SAMPLE, linework=True), linework=True)
    assert (result.lines_demolished, result.lines_added) == ([], [])
    assert result.length_unchanged_m == pytest.approx(83.57, abs=0.01)
