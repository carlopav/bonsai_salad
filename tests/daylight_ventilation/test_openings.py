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


def _space_bodies(spaces):
    geom_settings = openings.settings()
    return {space: openings.triangles(space, geom_settings) for space in spaces}


def test_a_point_inside_a_room_is_contained(add_space):
    space = add_space("A", width=4.0, depth=3.0)
    body = openings.triangles(space, openings.settings())
    assert openings.contains(body, np.array([2.0, 1.5, 1.0]))
    assert not openings.contains(body, np.array([2.0, -1.5, 1.0]))


def test_a_point_that_ties_an_equal_component_ray_is_still_excluded(add_space):
    """A ray with equal components keeps y - z constant along its length; the x
    = 4 face's triangulated diagonal is the line y + z = 3. y0 + z0 = -1 solves
    both at the same parameter where the ray also reaches x = 4, so a ray of
    (1, 1, 1) lands exactly on the seam between that face's two triangles."""
    space = add_space("A", width=4.0, depth=3.0)
    body = openings.triangles(space, openings.settings())
    assert not openings.contains(body, np.array([2.0, -1.5, 0.5]))


# Reproduces the equal-component tie from the round-1 regression: same seam as
# test_a_point_that_ties_an_equal_component_ray_is_still_excluded.
_EQUAL_RAY = np.array([1.0, 1.0, 1.0]) / np.sqrt(3.0)
_TIE_POINT = np.array([2.0, -1.5, 0.5])


def test_cast_flags_a_ray_that_grazes_a_triangle_edge(add_space):
    space = add_space("A", width=4.0, depth=3.0)
    body = openings.triangles(space, openings.settings())
    _, grazed = openings._cast(body, _TIE_POINT, _EQUAL_RAY)
    assert grazed is True


def test_contains_falls_through_a_grazing_ray_to_the_next(add_space, monkeypatch):
    space = add_space("A", width=4.0, depth=3.0)
    body = openings.triangles(space, openings.settings())
    monkeypatch.setattr(openings, "RAYS", (_EQUAL_RAY, openings.RAYS[0]))
    assert not openings.contains(body, _TIE_POINT)


def test_contains_raises_when_every_ray_grazes(add_space, monkeypatch):
    space = add_space("A", width=4.0, depth=3.0)
    body = openings.triangles(space, openings.settings())
    monkeypatch.setattr(openings, "RAYS", (_EQUAL_RAY, _EQUAL_RAY))
    with pytest.raises(ValueError):
        openings.contains(body, _TIE_POINT)


def test_an_external_window_finds_one_room(add_box, add_tapered_opening, add_space):
    # The wall runs along X at y = 0 with its thickness in +Y; the room sits
    # behind it, from y = 0.3 to y = 3.3.
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    space = add_space("A", width=4.0, depth=3.0, matrix=placement(y=0.3))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    found, external = openings.probe(opening, wall, _space_bodies([space]), openings.settings())
    assert found == [space]
    assert external is True


def test_a_window_between_two_rooms_finds_both(add_box, add_tapered_opening, add_space):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    behind = add_space("A", width=4.0, depth=3.0, matrix=placement(y=0.3))
    front = add_space("B", width=4.0, depth=3.0, matrix=placement(y=-3.0))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    found, external = openings.probe(opening, wall, _space_bodies([behind, front]), openings.settings())
    assert set(found) == {behind, front}
    assert external is False


def test_an_orphan_window_finds_nothing(add_box, add_tapered_opening, add_space):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    far_away = add_space("A", width=4.0, depth=3.0, matrix=placement(x=100.0))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    found, external = openings.probe(opening, wall, _space_bodies([far_away]), openings.settings())
    assert found == []
    assert external is False


def test_the_host_of_a_filling_is_the_wall_its_opening_voids(add_box, add_tapered_opening, fill):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=2.0))
    fill(wall, opening, window)
    assert openings.host_of(window) == (opening, wall)


def test_an_unhosted_filling_has_neither(add_box):
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5)
    assert openings.host_of(window) == (None, None)


def test_a_proposal_carries_the_room_and_the_clear_opening(ifc_file, add_box, add_tapered_opening, add_space, fill):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    add_space("A", width=4.0, depth=3.0, matrix=placement(y=0.3))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)
    (proposal,) = openings.proposals(ifc_file)
    assert proposal.filling == window
    assert proposal.external is True
    assert proposal.area == pytest.approx(1.8, rel=1e-6)


def test_two_fillings_in_one_opening_split_it(ifc_file, add_box, add_tapered_opening, add_space, fill):
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    add_space("A", width=4.0, depth=3.0, matrix=placement(y=0.3))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    left = add_box("IfcWindow", length=0.6, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    right = add_box("IfcWindow", length=0.6, thickness=0.1, height=1.5, matrix=placement(x=2.0, z=0.9))
    fill(wall, opening, left)
    fill(wall, opening, right)
    areas = [proposal.area for proposal in openings.proposals(ifc_file)]
    assert areas == pytest.approx([0.9, 0.9], rel=1e-6)


def test_a_filling_whose_probe_cannot_be_answered_is_an_orphan_proposal(
    ifc_file, add_box, add_tapered_opening, add_space, fill, monkeypatch
):
    """Every ray grazing is a real outcome of contains(), not a special case
    invented for this test: it is what happens when the probed point lands
    exactly on a seam between two triangles, whichever ray is tried."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    add_space("A", width=4.0, depth=3.0, matrix=placement(y=0.3))
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, z=0.9))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=1.4, z=0.9))
    fill(wall, opening, window)

    def always_grazes(body, point, ray):
        return np.zeros(len(body), dtype=bool), True

    monkeypatch.setattr(openings, "_cast", always_grazes)
    (proposal,) = openings.proposals(ifc_file)
    assert proposal.filling == window
    assert proposal.rooms == []
    assert proposal.external is False


def test_a_void_that_does_not_overlap_its_host_leaves_the_area_unmeasured(ifc_file, add_box, add_tapered_opening, fill):
    """The opening sits clear of the wall's 0 to 0.3 span along the thickness
    axis, so clear_opening_area finds no shared span and returns None — the
    filling's area must stay None too, not collapse into a false zero."""
    wall = add_box("IfcWall", length=4.0, thickness=0.3, height=3.0)
    opening = add_tapered_opening(near=1.2, far=1.2, height=1.5, depth=0.3, matrix=placement(x=2.0, y=5.0))
    window = add_box("IfcWindow", length=1.2, thickness=0.1, height=1.5, matrix=placement(x=2.0, y=5.0))
    fill(wall, opening, window)
    (proposal,) = openings.proposals(ifc_file)
    assert proposal.filling == window
    assert proposal.area is None
