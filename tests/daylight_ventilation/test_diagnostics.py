import colorsys

import ifcopenshell.api.root

from daylight_ventilation.core import boundaries, diagnostics, ratios


def _hue(rgb):
    return colorsys.rgb_to_hsv(*rgb)[0]


def _circular_distance(a, b):
    gap = abs(a - b) % 1.0
    return min(gap, 1.0 - gap)


def test_consecutive_rooms_land_far_apart_on_the_hue_circle():
    # Merely "different" is not enough: two rooms side by side must be told
    # apart at a glance.
    hues = [_hue(diagnostics.colour_for(index)) for index in range(5)]
    for first, second in zip(hues, hues[1:]):
        assert _circular_distance(first, second) > 0.3


def test_every_channel_stays_inside_the_unit_range():
    for index in range(20):
        assert all(0.0 <= channel <= 1.0 for channel in diagnostics.colour_for(index))


def _window(ifc_file, name):
    return ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcWindow", name=name)


def test_a_room_and_its_measured_window_are_paired(ifc_file, add_space):
    room = add_space("Soggiorno", 4.0, 3.0)
    window = _window(ifc_file, "W1")
    boundaries.add(ifc_file, room, window, True)
    ratios.write_clear_opening(ifc_file, window, 1.8)

    paired, excluded = diagnostics.groups(ifc_file)

    assert paired == [(room, [window])]
    assert excluded == []


def test_a_window_that_serves_a_room_but_has_no_known_area_is_excluded(ifc_file, add_space):
    # The room's colour would hide exactly the window the user has to go and
    # look at, so it goes red instead — and the room has nothing left.
    room = add_space("Bagno", 2.0, 2.0)
    window = _window(ifc_file, "W2")
    boundaries.add(ifc_file, room, window, True)

    paired, excluded = diagnostics.groups(ifc_file)

    assert paired == []
    assert set(excluded) == {room, window}


def test_a_room_with_no_window_at_all_is_excluded(ifc_file, add_space):
    room = add_space("Ripostiglio", 1.0, 1.0)

    paired, excluded = diagnostics.groups(ifc_file)

    assert paired == []
    assert excluded == [room]


def test_a_window_belonging_to_no_room_is_excluded(ifc_file, add_space):
    room = add_space("Cucina", 3.0, 3.0)
    served = _window(ifc_file, "W3")
    boundaries.add(ifc_file, room, served, True)
    ratios.write_clear_opening(ifc_file, served, 1.5)
    orphan = _window(ifc_file, "W4")

    paired, excluded = diagnostics.groups(ifc_file)

    assert paired == [(room, [served])]
    assert excluded == [orphan]
