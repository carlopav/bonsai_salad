import numpy as np
import pytest

from daylight_ventilation.core import openings


def box(x0, y0, z0, x1, y1, z1):
    """A closed box as (12, 3, 3) triangles, wound outwards."""
    p = np.array(
        [
            (x0, y0, z0),
            (x1, y0, z0),
            (x1, y1, z0),
            (x0, y1, z0),
            (x0, y0, z1),
            (x1, y0, z1),
            (x1, y1, z1),
            (x0, y1, z1),
        ]
    )
    quads = [
        (0, 3, 2, 1),  # bottom, normal -Z
        (4, 5, 6, 7),  # top, normal +Z
        (0, 1, 5, 4),  # front, normal -Y
        (1, 2, 6, 5),  # right, normal +X
        (2, 3, 7, 6),  # back, normal +Y
        (3, 0, 4, 7),  # left, normal -X
    ]
    faces = []
    for a, b, c, d in quads:
        faces.append([p[a], p[b], p[c]])
        faces.append([p[a], p[c], p[d]])
    return np.array(faces)


def test_section_of_a_box_is_its_rectangle():
    triangles = box(0.0, 0.0, 0.0, 2.0, 3.0, 4.0)
    assert openings.section_area(triangles, 1.0) == pytest.approx(6.0)


def test_section_above_the_box_is_empty():
    triangles = box(0.0, 0.0, 0.0, 2.0, 3.0, 4.0)
    assert openings.section_area(triangles, 5.0) == pytest.approx(0.0)


def test_section_of_two_disjoint_boxes_adds_up():
    triangles = np.concatenate([box(0, 0, 0, 1, 1, 1), box(5, 5, 0, 6, 7, 1)])
    assert openings.section_area(triangles, 0.5) == pytest.approx(1.0 + 2.0)


def test_a_hollow_section_subtracts_the_hole():
    """A box with a smaller box carved out of it: the inner shell is wound
    inwards, so its shoelace contribution comes out negative and the section
    reports the material, not the envelope."""
    outer = box(0.0, 0.0, 0.0, 4.0, 4.0, 2.0)
    inner = box(1.0, 1.0, -1.0, 3.0, 3.0, 3.0)[:, ::-1]  # reversed winding
    triangles = np.concatenate([outer, inner])
    assert openings.section_area(triangles, 1.0) == pytest.approx(16.0 - 4.0)
