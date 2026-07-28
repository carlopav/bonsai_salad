"""Set operations between the cut geometry, and the linework, of two drawings."""

import collections
import math
from dataclasses import dataclass, field

import numpy as np
import shapely

from .svg_read import linear_parts, polygonal_parts

DEFAULT_TOLERANCE_CM = 0.5
DEFAULT_MIN_AREA_M2 = 0.005
# Lateral tolerance of the linework comparison and shortest fragment kept, both
# on the building: 5 mm swallows the remesh noise, 2 mm does not.
LINE_TOLERANCE_CM = 0.5
MIN_LENGTH_M = 0.02
# 1 mm on the real building, well under the noise of a remeshed wall
PRECISION_M = 0.001
PLANE_TOLERANCE = 1e-4
# Paper millimetres per model unit at 1:1: everything here (the precision grid,
# the tolerance, the areas) is stated in metres and squared metres.
MODEL_UNIT_MM = 1000.0


class DiffError(Exception):
    pass


@dataclass
class DiffResult:
    unchanged: list
    demolished: list
    added: list
    tolerance_cm: float
    min_area_m2: float
    existing_polygons: int
    project_polygons: int
    existing_discarded: int
    project_discarded: int
    existing_empty_groups: int
    project_empty_groups: int
    existing_excluded_classes: collections.Counter
    project_excluded_classes: collections.Counter
    lines_unchanged: list = field(default_factory=list)
    lines_demolished: list = field(default_factory=list)
    lines_added: list = field(default_factory=list)

    @property
    def area_unchanged_m2(self):
        return sum(p.area for p in self.unchanged)

    @property
    def area_demolished_m2(self):
        return sum(p.area for p in self.demolished)

    @property
    def area_new_m2(self):
        return sum(p.area for p in self.added)

    @property
    def length_unchanged_m(self):
        return sum(line.length for line in self.lines_unchanged)

    @property
    def length_demolished_m(self):
        return sum(line.length for line in self.lines_demolished)

    @property
    def length_new_m(self):
        return sum(line.length for line in self.lines_added)


def compare(existing, project, tol_cm=DEFAULT_TOLERANCE_CM, min_area_m2=DEFAULT_MIN_AREA_M2, linework=False):
    existing_section = _single_section(existing, "existing")
    project_section = _single_section(project, "project")
    _check_planes(existing_section, project_section)
    _check_unit(existing_section, "existing")
    _check_unit(project_section, "project")

    e = _state_geometry(existing_section)
    p = _state_geometry(project_section)
    tolerance_m = tol_cm / 100.0
    lines_unchanged, lines_demolished, lines_added = (
        _compare_lines(existing_section, project_section) if linework else ([], [], [])
    )
    return DiffResult(
        unchanged=_desliver(shapely.intersection(e, p), tolerance_m, min_area_m2),
        demolished=_desliver(shapely.difference(e, p), tolerance_m, min_area_m2),
        added=_desliver(shapely.difference(p, e), tolerance_m, min_area_m2),
        tolerance_cm=tol_cm,
        min_area_m2=min_area_m2,
        existing_polygons=len(existing_section.polygons),
        project_polygons=len(project_section.polygons),
        existing_discarded=existing_section.discarded_subpaths,
        project_discarded=project_section.discarded_subpaths,
        existing_empty_groups=existing_section.empty_groups,
        project_empty_groups=project_section.empty_groups,
        existing_excluded_classes=existing_section.excluded_classes,
        project_excluded_classes=project_section.excluded_classes,
        lines_unchanged=lines_unchanged,
        lines_demolished=lines_demolished,
        lines_added=lines_added,
    )


def _single_section(sections, side):
    if len(sections) != 1:
        raise DiffError(f"The {side} drawing has {len(sections)} section groups, one is required.")
    return sections[0]


def _check_planes(existing, project):
    """Two models with different project origins would give translated frames
    and a silent "everything demolished + everything new": rotation and origin
    are both compared."""
    if not np.allclose(existing.plane, project.plane, atol=PLANE_TOLERANCE):
        raise DiffError(
            "The two drawings are cut on different planes (ifc:plane differs in rotation or origin):\n"
            f"existing {np.array2string(existing.plane, precision=4)}\n"
            f"project {np.array2string(project.plane, precision=4)}"
        )


def _check_unit(section, side):
    """matrix3 times the drawing scale gives the paper millimetres per model
    unit: a model in millimetres would be off by a factor of a thousand, on
    every area and on every tolerance, without a word."""
    unit = section.matrix3[0][0] * section.scale
    if not math.isclose(unit, MODEL_UNIT_MM, rel_tol=1e-6):
        raise DiffError(
            f"The {side} drawing is not in metres: {unit:.6g} paper mm per model unit at 1:1 "
            f"instead of {MODEL_UNIT_MM:.0f}."
        )


def _state_geometry(section):
    """The union is also what turns walls drawn layer by layer back into the
    monolithic footprint they share with the other state."""
    union = shapely.union_all([p.geometry for p in section.polygons])
    return shapely.union_all(polygonal_parts(shapely.set_precision(union, PRECISION_M)))


def _compare_lines(existing, project, tol_cm=LINE_TOLERANCE_CM, min_length_m=MIN_LENGTH_M):
    """The linear counterpart of the boolean on the areas, and just as blind:
    what falls inside the other state's neighbourhood is unchanged, the rest is
    demolished or new. The unchanged lines are the project's, so a line drawn
    twice by the two states does not come out doubled.

    The three lengths do not add up to the drawing's total, exactly as the three
    areas do not: the runs shorter than min_length_m are dropped from all three,
    the way the de-sliver drops the thin slivers."""
    if bool(existing.lines) != bool(project.lines):
        # The same trap as two different section planes: one drawing without
        # projection groups would paint the other's whole linework red or
        # yellow, quietly, with nothing demolished or built.
        side = "existing" if not existing.lines else "project"
        raise DiffError(
            f"The {side} drawing has no projection linework: comparing it would report the other drawing's "
            "linework as entirely new or entirely demolished. Regenerate that drawing with its linework, "
            "or compare the cut geometry only."
        )
    e = _line_geometry(existing)
    p = _line_geometry(project)
    tolerance_m = tol_cm / 100.0
    # quad_segs=1 makes the neighbourhood octagonal rather than round: the
    # lateral tolerance, the one that matters, stays exact, and only the ends
    # and the corners are clipped to about 0.7·t. With set_precision it takes
    # the real plan from 4 s to 0.3 s.
    e_zone = e.buffer(tolerance_m, quad_segs=1)
    p_zone = p.buffer(tolerance_m, quad_segs=1)
    return (
        _line_parts(shapely.intersection(p, e_zone), min_length_m),
        _line_parts(shapely.difference(e, p_zone), min_length_m),
        _line_parts(shapely.difference(p, e_zone), min_length_m),
    )


def _line_geometry(section):
    union = shapely.union_all([line.geometry for line in section.lines])
    return shapely.union_all(linear_parts(shapely.set_precision(union, PRECISION_M)))


def _line_parts(geometry, min_length_m):
    """line_merge welds the pieces a difference leaves behind back into runs,
    so the minimum length is judged on the run and not on each piece."""
    merged = shapely.line_merge(shapely.MultiLineString(linear_parts(geometry)))
    return [part for part in linear_parts(merged) if part.length >= min_length_m]


def _desliver(geometry, tolerance_m, min_area_m2):
    """Morphological opening kills the remesh noise along the shared faces; the
    re-clip on the original brings the exact edges back, mitre keeps corners."""
    opened = geometry.buffer(-tolerance_m, join_style="mitre").buffer(tolerance_m, join_style="mitre")
    parts = polygonal_parts(shapely.intersection(opened, geometry))
    return [p for p in parts if p.area >= min_area_m2]
