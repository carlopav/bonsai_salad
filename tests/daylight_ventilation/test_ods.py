import pytest
from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.teletype import extractText

from daylight_ventilation.core import ods, ratios

FORMULA = "urn:oasis:names:tc:opendocument:xmlns:table:1.0", "formula"


def row(identification, name, net, daylight, air, clear=None, unmeasured_fillings=0, verified=True):
    return ratios.Row(
        space=None,
        identification=identification,
        name=name,
        net=net,
        clear=daylight if clear is None else clear,
        daylight=daylight,
        air=air,
        unmeasured_fillings=unmeasured_fillings,
        daylight_ratio=daylight / net if net else 0.0,
        air_ratio=air / net if net else 0.0,
        daylight_requirement=0.125,
        air_requirement=0.125,
        verified=verified,
    )


HEADERS = [
    "Identificativo",
    "Nome",
    "Superficie netta (m²)",
    "Requisito illuminazione / aerazione (m²)",
    "Superficie illuminante (m²)",
    "Superficie aerante (m²)",
    "Verificato",
]


@pytest.fixture
def written(tmp_path):
    def write(sections):
        path = tmp_path / "table.ods"
        ods.write(str(path), HEADERS, sections)
        (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
        return [
            [extractText(cell) for cell in line.getElementsByType(TableCell)]
            for line in table.getElementsByType(TableRow)
        ]

    return write


def cells_of(path, line=2):
    (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
    return table.getElementsByType(TableRow)[line].getElementsByType(TableCell)


def test_the_header_comes_first(written):
    lines = written([("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)])])
    assert lines[0] == HEADERS


def test_a_row_reads_across_as_areas(written):
    """Net floor, what it has to reach, and what it has: three areas in the same
    unit, so the check is one look along the row."""
    lines = written([("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.6)])])
    assert lines[2][2:7] == ["12.00", "1.50", "1.80", "1.60", "sì"]


def test_a_storey_heads_its_block(written):
    lines = written([("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)])])
    assert lines[1][0] == "Piano terra"
    assert lines[2][0] == "A1"
    assert lines[2][1] == "Soggiorno"


def test_two_storeys_make_two_blocks(written):
    lines = written(
        [
            ("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)]),
            ("Piano primo", [row("B1", "Camera", 10.0, 1.2, 1.2)]),
        ]
    )
    assert [line[0] for line in lines if line and line[0]] == [
        "Identificativo",
        "Piano terra",
        "A1",
        "Piano primo",
        "B1",
    ]


def test_two_equal_requirements_make_one_value(tmp_path):
    path = tmp_path / "table.ods"
    ods.write(str(path), HEADERS, [("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)])])
    cells = cells_of(path)
    assert extractText(cells[3]) == "1.50"
    assert cells[3].attributes[FORMULA] == "of:=[.C3]*0.125"


def test_two_different_requirements_make_two_values(tmp_path):
    """Illuminazione first, aerazione second, in the order of the columns that
    follow. No formula: one cell cannot hold two."""
    path = tmp_path / "table.ods"
    differing = row("A1", "Soggiorno", 12.0, 1.8, 1.8)._replace(air_requirement=0.0625)
    ods.write(str(path), HEADERS, [("Piano terra", [differing])])
    cells = cells_of(path)
    assert extractText(cells[3]) == "1.50 / 0.75"
    assert FORMULA not in cells[3].attributes


def test_one_exempt_requirement_still_shows_the_other(tmp_path):
    path = tmp_path / "table.ods"
    exempt_air = row("A2", "Ripostiglio", 4.0, 0.6, 0.0)._replace(air_requirement=0.0)
    ods.write(str(path), HEADERS, [("Piano terra", [exempt_air])])
    assert extractText(cells_of(path)[3]) == "0.50 / —"


def test_an_exempt_room_shows_a_dash(written):
    exempt = row("A2", "Ripostiglio", 4.0, 0.0, 0.0)._replace(daylight_requirement=0.0, air_requirement=0.0)
    lines = written([("Piano terra", [exempt])])
    assert lines[2][3] == "—"


def test_the_requirement_and_the_verdict_are_formulas(tmp_path):
    path = tmp_path / "table.ods"
    ods.write(str(path), HEADERS, [("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)])])
    cells = cells_of(path)
    assert cells[3].attributes[FORMULA] == "of:=[.C3]*0.125"
    assert cells[6].attributes[FORMULA] == (
        'of:=IF(AND([.C3]>0;[.E3]>=[.C3]*0.125;[.F3]>=[.C3]*0.125);"sì";"no")'
    )


def test_the_verdict_is_built_over_the_areas_not_over_the_requirement_column(tmp_path):
    """That column holds text whenever the two requirements differ; a verdict
    reading it would depend on how the application ranks text against numbers."""
    path = tmp_path / "table.ods"
    differing = row("A1", "Soggiorno", 12.0, 1.8, 1.8)._replace(air_requirement=0.0625)
    ods.write(str(path), HEADERS, [("Piano terra", [differing])])
    formula = cells_of(path)[6].attributes[FORMULA]
    assert "[.D3]" not in formula
    assert formula == 'of:=IF(AND([.C3]>0;[.E3]>=[.C3]*0.125;[.F3]>=[.C3]*0.0625);"sì";"no")'


def test_a_room_without_a_floor_does_not_pass_on_the_arithmetic(tmp_path):
    """Zero net floor makes every requirement zero square metres, which every
    area meets. The calculation calls that a failure; so must the formula."""
    path = tmp_path / "table.ods"
    floorless = row("A4", "Vano tecnico", 0.0, 0.0, 0.0, verified=False)
    ods.write(str(path), HEADERS, [("Piano terra", [floorless])])
    cells = cells_of(path)
    assert extractText(cells[6]) == "no"
    assert cells[6].attributes[FORMULA].startswith('of:=IF(AND([.C3]>0;')


def test_a_formula_cell_still_carries_the_computed_value(tmp_path):
    """Bonsai's schedule renderer does not recalculate: it shows what is stored."""
    path = tmp_path / "table.ods"
    ods.write(str(path), HEADERS, [("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)])])
    cells = cells_of(path)
    assert extractText(cells[3]) == "1.50"
    assert extractText(cells[6]) == "sì"


def test_an_unmeasured_room_withholds_the_verdict(tmp_path):
    path = tmp_path / "table.ods"
    unmeasured = row("A3", "Cucina", 12.0, 1.8, 1.8, unmeasured_fillings=1)
    ods.write(str(path), HEADERS, [("Piano terra", [unmeasured])])
    cells = cells_of(path)
    assert extractText(cells[6]) == "da verificare"
    assert FORMULA not in cells[6].attributes


def test_one_exempt_requirement_drops_its_term(tmp_path):
    path = tmp_path / "table.ods"
    exempt_air = row("A2", "Ripostiglio", 4.0, 0.6, 0.0)._replace(air_requirement=0.0)
    ods.write(str(path), HEADERS, [("Piano terra", [exempt_air])])
    formula = cells_of(path)[6].attributes[FORMULA]
    assert formula == 'of:=IF(AND([.C3]>0;[.E3]>=[.C3]*0.125);"sì";"no")'
    assert "[.F" not in formula


def test_both_exempt_requirements_drop_the_formula(tmp_path):
    path = tmp_path / "table.ods"
    exempt = row("A2", "Ripostiglio", 4.0, 0.0, 0.0)._replace(daylight_requirement=0.0, air_requirement=0.0)
    ods.write(str(path), HEADERS, [("Piano terra", [exempt])])
    cells = cells_of(path)
    assert FORMULA not in cells[6].attributes
    assert extractText(cells[6]) == "sì"
