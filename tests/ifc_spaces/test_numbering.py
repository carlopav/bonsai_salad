import pytest

from ifc_spaces.core import numbering


def names(plan):
    return [name for _, name in plan.assignments]


def rooms(plan):
    return [space.Name for space, _ in plan.assignments]


def test_the_chain_starts_at_the_largest_room_and_walks_the_plan(ifc_file, add_storey, add_space):
    storey = add_storey("Piano terra", 0.0)
    # Centroids: A (3.0, 2.5), B (8.0, 2.5), C (1.0, 7.0), D (4.0, 7.0), E (8.0, 7.0).
    spaces = [
        add_space("A", 6.0, 5.0, 0.0, 0.0, storey),
        add_space("B", 4.0, 5.0, 6.0, 0.0, storey),
        add_space("C", 2.0, 4.0, 0.0, 5.0, storey),
        add_space("D", 4.0, 4.0, 2.0, 5.0, storey),
        add_space("E", 4.0, 4.0, 6.0, 5.0, storey),
    ]

    plan = numbering.plan(ifc_file, spaces, "L")

    assert rooms(plan) == ["A", "D", "C", "E", "B"]
    assert names(plan) == ["L01", "L02", "L03", "L04", "L05"]
    assert plan.storeys == 1
    assert not plan.unplaceable


def test_the_prefix_may_be_empty(ifc_file, add_storey, add_space):
    storey = add_storey("Piano terra", 0.0)
    spaces = [add_space("A", 4.0, 4.0, 0.0, 0.0, storey), add_space("B", 2.0, 2.0, 6.0, 0.0, storey)]

    assert names(numbering.plan(ifc_file, spaces, "")) == ["01", "02"]


@pytest.mark.parametrize("count,first,last", [(99, "01", "99"), (100, "001", "100")])
def test_the_padding_follows_how_many_rooms_are_renamed(ifc_file, add_storey, add_space, count, first, last):
    storey = add_storey("Piano terra", 0.0)
    # Each room a little smaller than the one before, in a row: the chain runs
    # from the first to the last.
    spaces = [add_space(f"R{i}", 4.0 - i * 0.01, 4.0, i * 5.0, 0.0, storey) for i in range(count)]

    numbered = names(numbering.plan(ifc_file, spaces, ""))

    assert numbered[0] == first
    assert numbered[-1] == last
    assert numbered == [f"{number:0{len(first)}d}" for number in range(1, count + 1)]


def test_one_counter_crosses_the_storeys_in_order_of_elevation(ifc_file, add_storey, add_space):
    ground, first = add_storey("Piano terra", 0.0), add_storey("Piano primo", 3.0)
    upper = [add_space("U1", 4.0, 4.0, 0.0, 0.0, first), add_space("U2", 2.0, 2.0, 6.0, 0.0, first)]
    lower = [add_space("G1", 6.0, 6.0, 0.0, 0.0, ground), add_space("G2", 3.0, 3.0, 8.0, 0.0, ground)]

    plan = numbering.plan(ifc_file, upper + lower, "")

    assert rooms(plan) == ["G1", "G2", "U1", "U2"]
    assert names(plan) == ["01", "02", "03", "04"]
    assert plan.storeys == 2


def test_the_storeys_stand_on_their_placement_where_the_elevation_is_null(ifc_file, add_storey, add_space):
    """A real file: every storey leaves the optional Elevation empty and says its
    height through the placement alone."""
    roof = add_storey("A-03 Copertura", z=5.0)
    first = add_storey("A-02 Piano primo", z=3.72)
    ground = add_storey("A-01 Piano terra", z=0.5)
    spaces = [
        add_space("R", 4.0, 4.0, 0.0, 0.0, roof),
        add_space("U", 4.0, 4.0, 0.0, 0.0, first),
        add_space("G", 4.0, 4.0, 0.0, 0.0, ground),
    ]

    assert rooms(numbering.plan(ifc_file, spaces, "")) == ["G", "U", "R"]


def test_two_storeys_at_the_same_height_are_ordered_by_name(ifc_file, add_storey, add_space):
    """Nothing in the model separates them, so the numbering falls back on the
    name rather than on the order the selection arrived in."""
    second, first = add_storey("Corpo B", z=3.0), add_storey("Corpo A", z=3.0)
    spaces = [add_space("B1", 4.0, 4.0, 0.0, 0.0, second), add_space("A1", 4.0, 4.0, 0.0, 0.0, first)]

    assert rooms(numbering.plan(ifc_file, spaces, "")) == ["A1", "B1"]
    assert rooms(numbering.plan(ifc_file, list(reversed(spaces)), "")) == ["A1", "B1"]


@pytest.fixture
def corridor_plan(add_storey, add_space):
    """Four rooms where the corridor changes what comes next: reached from the
    largest the chain goes on to the far room, skipped it would go to the near
    one. Only the corridor carries a name."""
    storey = add_storey("Piano terra", 0.0)
    return (
        add_space(None, 6.0, 5.0, 0.0, 0.0, storey),  # largest, (3.0, 2.5)
        add_space("Corridoio", 4.0, 2.0, 2.0, 5.0, storey),  # (4.0, 6.0)
        add_space(None, 4.0, 4.0, 6.0, 0.5, storey),  # near, (8.0, 2.5)
        add_space(None, 3.0, 3.0, 3.5, 7.0, storey),  # far, (5.0, 8.5)
    )


def test_a_named_room_is_a_stop_that_consumes_no_number(ifc_file, corridor_plan):
    largest, corridor, near, far = corridor_plan

    plan = numbering.plan(ifc_file, list(corridor_plan), "", rename_named=False)

    assert plan.assignments == [(largest, "01"), (far, "02"), (near, "03")]
    assert plan.kept == [corridor]


def test_the_same_plan_renumbers_the_named_room_too_by_default(ifc_file, corridor_plan):
    largest, corridor, near, far = corridor_plan

    plan = numbering.plan(ifc_file, list(corridor_plan), "")

    assert plan.assignments == [(largest, "01"), (corridor, "02"), (far, "03"), (near, "04")]
    assert not plan.kept


@pytest.mark.parametrize("name", [None, "", "   "])
def test_a_blank_name_is_not_a_name(ifc_file, add_storey, add_space, name):
    storey = add_storey("Piano terra", 0.0)
    spaces = [add_space(name, 4.0, 4.0, 0.0, 0.0, storey), add_space("B", 2.0, 2.0, 6.0, 0.0, storey)]

    plan = numbering.plan(ifc_file, spaces, "", rename_named=False)

    assert names(plan) == ["01"]
    assert plan.assignments[0][0] is spaces[0]


def test_a_room_outside_any_storey_blocks_the_whole_run(ifc_file, add_storey, add_space):
    storey = add_storey("Piano terra", 0.0)
    placed = add_space("A", 6.0, 5.0, 0.0, 0.0, storey)
    homeless = add_space("B", 4.0, 4.0, 6.0, 0.0)

    plan = numbering.plan(ifc_file, [placed, homeless], "")

    assert plan.unplaceable == [homeless]
    assert plan.assignments == []


def test_a_room_with_no_readable_geometry_blocks_the_whole_run(ifc_file, add_storey, add_space, add_bodiless_space):
    storey = add_storey("Piano terra", 0.0)
    placed = add_space("A", 6.0, 5.0, 0.0, 0.0, storey)
    bodiless = add_bodiless_space("B", storey)

    plan = numbering.plan(ifc_file, [placed, bodiless], "")

    assert plan.unplaceable == [bodiless]
    assert plan.assignments == []


def test_a_room_the_option_would_skip_still_has_to_be_placeable(ifc_file, add_storey, add_bodiless_space, add_space):
    storey = add_storey("Piano terra", 0.0)
    placed = add_space("A", 6.0, 5.0, 0.0, 0.0, storey)
    bodiless = add_bodiless_space("Corridoio", storey)

    plan = numbering.plan(ifc_file, [placed, bodiless], "", rename_named=False)

    assert plan.unplaceable == [bodiless]


def test_a_space_the_storey_contains_instead_of_aggregating_still_has_one(ifc_file, add_storey, add_space, contain):
    storey = add_storey("Piano terra", 0.0)
    space = add_space("A", 6.0, 5.0, 0.0, 0.0)
    contain(space, storey)

    plan = numbering.plan(ifc_file, [space], "")

    assert names(plan) == ["01"]


@pytest.mark.parametrize("unit_prefix", ["MILLI"], indirect=True)
def test_a_millimetre_file_is_numbered_the_same_way(ifc_file, add_storey, add_space):
    storey = add_storey("Piano terra", 0.0)
    spaces = [
        add_space("A", 6000.0, 5000.0, 0.0, 0.0, storey),
        add_space("B", 4000.0, 5000.0, 6000.0, 0.0, storey),
        add_space("C", 2000.0, 4000.0, 0.0, 5000.0, storey),
        add_space("D", 4000.0, 4000.0, 2000.0, 5000.0, storey),
        add_space("E", 4000.0, 4000.0, 6000.0, 5000.0, storey),
    ]

    assert rooms(numbering.plan(ifc_file, spaces, "")) == ["A", "D", "C", "E", "B"]


@pytest.mark.parametrize("unit_prefix", ["MILLI"], indirect=True)
def test_a_stored_take_off_is_compared_against_a_measured_area_on_the_same_scale(
    ifc_file, add_storey, add_space, write_net_floor_area
):
    storey = add_storey("Piano terra", 0.0)
    smaller = add_space("A", 4000.0, 4000.0, 0.0, 0.0, storey)
    larger = add_space("B", 5000.0, 5000.0, 10000.0, 0.0, storey)
    write_net_floor_area(smaller, 16000000.0)

    assert rooms(numbering.plan(ifc_file, [smaller, larger], "")) == ["B", "A"]


def test_equidistant_rooms_are_ordered_the_same_way_every_run(ifc_file, add_storey, add_space):
    storey = add_storey("Piano terra", 0.0)
    # Centroids: A (0.0, 0.0), west (-5.0, 0.0), east (5.0, 0.0).
    spaces = [
        add_space("A", 4.0, 4.0, -2.0, -2.0, storey),
        add_space("east", 2.0, 2.0, 4.0, -1.0, storey),
        add_space("west", 2.0, 2.0, -6.0, -1.0, storey),
    ]

    assert rooms(numbering.plan(ifc_file, spaces, "")) == ["A", "west", "east"]
    assert rooms(numbering.plan(ifc_file, list(reversed(spaces)), "")) == ["A", "west", "east"]


def test_rooms_at_the_same_place_are_ordered_by_global_id(ifc_file, add_storey, add_space):
    storey = add_storey("Piano terra", 0.0)
    start = add_space("A", 6.0, 6.0, 0.0, 0.0, storey)
    twins = [add_space("T1", 2.0, 2.0, 10.0, 0.0, storey), add_space("T2", 2.0, 2.0, 10.0, 0.0, storey)]
    first, second = sorted(twins, key=lambda space: space.GlobalId)

    plan = numbering.plan(ifc_file, [start] + twins, "")

    assert [space for space, _ in plan.assignments] == [start, first, second]


def test_a_generated_name_that_a_room_left_alone_already_carries_is_reported(ifc_file, add_storey, add_space):
    storey = add_storey("Piano terra", 0.0)
    selected = add_space("A", 6.0, 5.0, 0.0, 0.0, storey)
    untouched = add_space("01", 2.0, 2.0, 10.0, 0.0, storey)

    plan = numbering.plan(ifc_file, [selected], "")

    assert names(plan) == ["01"]
    assert plan.clashes == [untouched]
