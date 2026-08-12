# Bonsai Salad — daylight_ventilation tool

"""Which rooms and openings the check actually paired up, as colours.

The pairing is derived from geometry and drives every ratio, but nothing on
screen tells a room that is short of daylight from a room whose window was
paired with the one next door. Colouring the pairs answers that in one look.
"""

import colorsys

from . import boundaries, openings, ratios, spaces

# Successive multiples of an irrational spread hues as far apart as a stateless
# scheme can: consecutive rooms cannot land on the same colour, which a digest
# of the GlobalId could not promise.
GOLDEN = 0.618033988749895
SATURATION = 0.65
VALUE = 0.95

EXCLUDED = (0.8, 0.1, 0.1)

# Excluded sits at hue 0.0; no room's hue may land within this of it on either
# side of the circle, or red would stop meaning one specific thing.
RED_GAP = 0.06


def colour_for(index):
    """The colour of the index-th room, RGB in 0..1. Hues are folded into the
    band that excludes RED_GAP on both sides of the exclusion colour's hue."""
    hue = RED_GAP + ((index * GOLDEN) % 1.0) * (1.0 - 2 * RED_GAP)
    return colorsys.hsv_to_rgb(hue, SATURATION, VALUE)


def groups(ifc_file):
    """([(space, [filling])], [element]): the pairs that count, then everything
    the check knows and does not count — the excluded openings and the rooms
    left without a valid one.

    A filling that serves a room but has no known area belongs to the second
    list. It is the one to go and look at, and the room's colour would hide it.

    Outdoor space is in neither: the check says nothing about a balcony, and red
    would claim it found something wrong with it.
    """
    paired, counted, excluded = [], set(), []
    for space in spaces.rooms(ifc_file):
        served = [f for f in boundaries.serves(space) if ratios.has_known_area(f)]
        if served:
            paired.append((space, served))
            counted.update(served)
        else:
            excluded.append(space)
    excluded.extend(f for f in openings.fillings(ifc_file) if f not in counted)
    return paired, excluded
