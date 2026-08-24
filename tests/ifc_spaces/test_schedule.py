import ifcopenshell.util.selector
import pytest

from ifc_spaces.core import schedule


def codes(spaces):
    return [space.Name for space in spaces]


def value(space, query):
    return ifcopenshell.util.selector.get_element_value(space, query)


def column(header):
    """The column of that heading, whatever its place in the table."""
    (found,) = [query for query, text in schedule.COLUMNS if text == header]
    return found


@pytest.fixture
def room(ifc_file, add_space, write_quantities, write_daylight_results):
    """A room the whole toolchain has been over: taken off, and checked."""
    space = add_space("A01", 4.0, 5.0, long_name="zona giorno")
    write_quantities(space, area=20.0, volume=60.0)
    write_daylight_results(
        space,
        daylight=3.0,
        air=2.5,
        minimum=2.5,
        daylight_requirement=0.125,
        air_requirement=0.125,
        verified=True,
    )
    return space


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Codice", "A01"),
        ("Nome esteso", "zona giorno"),
        ("Superficie utile", 20.0),
        ("Volume", 60.0),
        ("Superficie minima ill.ne / aerazione", 2.5),
        ("Superficie illuminante", 3.0),
        ("Superficie aerante", 2.5),
        ("Verificato", True),
    ],
)
def test_every_column_reaches_the_value_it_names(ifc_file, room, header, expected):
    """The one thing that fails silently: a query that misses its property
    yields a null the reader takes for a room nobody measured."""
    assert value(room, column(header)) == expected


def test_a_room_nobody_checked_answers_nothing_on_the_daylight_columns(ifc_file, add_space, write_quantities):
    """Nothing is invented for it: the empty answers become the table's null
    marker, and the row still lists the room."""
    space = add_space("A01", 4.0, 5.0)
    write_quantities(space, area=20.0, volume=60.0)

    daylight = [column(header) for header in ("Superficie illuminante", "Superficie aerante", "Verificato")]

    assert [value(space, query) for query in daylight] == [None, None, None]


def test_the_headers_carry_the_units_of_the_project(ifc_file):
    headers = [header for _, header in schedule.columns(ifc_file)]

    assert headers == [
        "Codice",
        "Nome esteso",
        "Superficie utile (m²)",
        "Volume (m³)",
        "Superficie minima ill.ne / aerazione (m²)",
        "Superficie illuminante (m²)",
        "Superficie aerante (m²)",
        "Verificato",
    ]


@pytest.mark.parametrize(
    "value,printed",
    [("48.6234", "48,62"), ("6.2977", "6,30"), ("12.0", "12,00"), ("0.0", "0,00"), ("1234.5", "1234,50")],
)
def test_a_measured_column_prints_two_decimals_with_the_italian_comma(value, printed):
    """A column of 48,62 beside 12,0 reads as two different precisions. The
    export replaces every {{value}} in the format, so the rounding may be
    written twice."""
    rendered = ifcopenshell.util.selector.format(schedule.NUMBER_FORMAT.replace("{{value}}", f'"{value}"'))

    assert rendered == printed


def test_every_formatted_column_is_a_column_of_the_table(ifc_file):
    """The formatting names the column it applies to: a name no column carries
    raises inside the export, so the two lists have to be kept in step."""
    queries = [query for query, _ in schedule.COLUMNS]

    assert [rule["name"] for rule in schedule.FORMATTING] == [
        query for query in queries if query in {rule["name"] for rule in schedule.FORMATTING}
    ]
    assert set(rule["name"] for rule in schedule.FORMATTING) <= set(queries)


def test_the_table_is_sorted_on_a_column_it_carries(ifc_file):
    queries = [query for query, _ in schedule.COLUMNS]

    assert [rule["name"] for rule in schedule.SORT] == [queries[0]]


@pytest.mark.parametrize("kind", ["EXTERNAL", "PARKING", "GFA"])
def test_outdoor_space_a_parking_bay_and_a_floor_overlay_are_not_rooms(ifc_file, add_space, kind):
    room = add_space("A01", 4.0, 5.0)
    add_space("A02", 4.0, 5.0, predefined_type=kind)

    assert schedule.rooms(ifc_file) == [room]


def test_the_storeys_come_in_building_order_each_with_its_own_rooms(ifc_file, add_storey, add_space):
    ground = add_storey("Piano terra", 0.0)
    first = add_storey("Piano primo", 3.0)
    add_space("B01", 4.0, 5.0, storey=first)
    add_space("A02", 4.0, 5.0, storey=ground)
    add_space("A01", 4.0, 5.0, storey=ground)

    grouping = schedule.by_storey(ifc_file)

    assert [(storey.Name, codes(spaces)) for storey, spaces in grouping.storeys] == [
        ("Piano terra", ["A02", "A01"]),
        ("Piano primo", ["B01"]),
    ]
    assert grouping.orphans == []


def test_a_room_outside_a_storey_is_reported_apart(ifc_file, add_storey, add_space):
    """One file per storey has nowhere to write it: the export says so rather
    than dropping it in silence."""
    ground = add_storey("Piano terra", 0.0)
    add_space("A01", 4.0, 5.0, storey=ground)
    add_space("Z99", 4.0, 5.0)

    grouping = schedule.by_storey(ifc_file)

    assert [codes(spaces) for _, spaces in grouping.storeys] == [["A01"]]
    assert codes(grouping.orphans) == ["Z99"]


def test_a_room_related_to_its_storey_by_containment_is_still_of_that_storey(ifc_file, add_storey, add_space, contain):
    """WR31 forbids it, an imported file does it anyway."""
    ground = add_storey("Piano terra", 0.0)
    contain(add_space("A01", 4.0, 5.0), ground)

    ((storey, spaces),) = schedule.by_storey(ifc_file).storeys

    assert (storey.Name, codes(spaces)) == ("Piano terra", ["A01"])


def test_a_storey_is_named_after_itself(ifc_file, add_storey):
    assert schedule.labels([add_storey("Piano terra", 0.0)]) == ["Piano terra"]


def test_two_storeys_of_the_same_name_are_told_apart_by_their_building(ifc_file, add_storey, add_building):
    """One file per storey, and a multi-building project has a Piano terra in
    each: unqualified, the second would overwrite the first."""
    ground = add_storey("Piano terra", 0.0)
    other = add_storey("Piano terra", 0.0)
    add_building("Edificio A", [ground])
    add_building("Edificio B", [other])

    assert schedule.labels([ground, other]) == ["Edificio A - Piano terra", "Edificio B - Piano terra"]


def test_storeys_the_building_cannot_tell_apart_are_numbered(ifc_file, add_storey, add_building):
    storeys = [add_storey("Piano terra", 0.0), add_storey("Piano terra", 0.0)]
    add_building("Edificio A", storeys)

    assert schedule.labels(storeys) == ["Edificio A - Piano terra (1)", "Edificio A - Piano terra (2)"]


def test_a_storey_without_a_name_is_still_named(ifc_file, add_storey):
    """An empty label would make a file called nothing."""
    assert schedule.labels([add_storey(None, 0.0)]) == ["Piano"]
