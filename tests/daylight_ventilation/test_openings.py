import numpy as np
import pytest

from daylight_ventilation.core import openings

from .conftest import placement


def test_thickness_axis_of_a_wall_is_its_local_y(add_box):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    assert openings.thickness_axis(wall) == pytest.approx([0.0, 1.0, 0.0], abs=1e-9)


def test_thickness_axis_follows_the_placement(add_box):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0, matrix=placement(angle=np.pi / 2))
    assert openings.thickness_axis(wall) == pytest.approx([-1.0, 0.0, 0.0], abs=1e-9)


def test_thickness_axis_of_a_slab_is_its_local_z(add_box):
    slab = add_box("IfcSlab", length=5.0, thickness=4.0, height=0.25)
    assert openings.thickness_axis(slab) == pytest.approx([0.0, 0.0, 1.0], abs=1e-9)


def test_clear_opening_of_a_prism_is_its_rectangle(add_box, add_tapered_opening):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=1.0))
    assert openings.clear_opening_area(opening, wall, openings.settings()) == pytest.approx(1.8, rel=1e-6)


def test_a_tapered_opening_reports_its_throat(add_box, add_tapered_opening):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    opening = add_tapered_opening(near=1.2, far=0.8, height=1.5, depth=0.3, matrix=placement(x=1.0))
    area = openings.clear_opening_area(opening, wall, openings.settings())
    assert area == pytest.approx(0.8 * 1.5, rel=0.05)


def test_the_part_outside_the_wall_does_not_count(add_box, add_tapered_opening):
    """The opening runs 1 m deep through a 0.3 m wall and widens outside it. Only
    the 0.3 m inside the wall may be sampled, so the throat inside wins."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    opening = add_tapered_opening(near=1.0, far=5.0, height=1.5, depth=1.0, matrix=placement(x=1.0))
    area = openings.clear_opening_area(opening, wall, openings.settings())
    assert area < 1.0 * 1.5 * 1.3
