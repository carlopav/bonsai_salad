import pytest
from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.teletype import extractText

from daylight_ventilation.core import ods, ratios


def row(identification, name, net, daylight, air, unmeasured_fillings=0, verified=True):
    return ratios.Row(
        space=None,
        identification=identification,
        name=name,
        net=net,
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
    "Superficie aerante (m²)",
    "Superficie illuminante (m²)",
    "Rapporto aerazione",
    "Requisito aerazione",
    "Rapporto illuminazione",
    "Requisito illuminazione",
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


def test_the_header_comes_first(written):
    lines = written([("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)])])
    assert lines[0] == HEADERS


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


def test_the_ratio_and_the_verdict_are_formulas(tmp_path):
    path = tmp_path / "table.ods"
    ods.write(str(path), HEADERS, [("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)])])
    (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
    cells = table.getElementsByType(TableRow)[2].getElementsByType(TableCell)
    formula = "urn:oasis:names:tc:opendocument:xmlns:table:1.0", "formula"
    assert cells[5].attributes[formula] == "of:=IF([.C3]=0;0;[.D3]/[.C3])"
    assert cells[7].attributes[formula] == "of:=IF([.C3]=0;0;[.E3]/[.C3])"
    assert cells[9].attributes[formula] == 'of:=IF(AND([.F3]>=[.G3];[.H3]>=[.I3]);"sì";"no")'


def test_a_formula_cell_still_carries_the_computed_value(tmp_path):
    """Bonsai's schedule renderer does not recalculate: it shows what is stored."""
    path = tmp_path / "table.ods"
    ods.write(str(path), HEADERS, [("Piano terra", [row("A1", "Soggiorno", 12.0, 1.8, 1.8)])])
    (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
    cells = table.getElementsByType(TableRow)[2].getElementsByType(TableCell)
    assert extractText(cells[5]) == "0.150"
    assert extractText(cells[9]) == "sì"


def test_an_exempt_room_shows_a_dash(written):
    exempt = row("A2", "Ripostiglio", 4.0, 0.0, 0.0)._replace(daylight_requirement=0.0, air_requirement=0.0)
    lines = written([("Piano terra", [exempt])])
    assert lines[2][5] == "—"
    assert lines[2][7] == "—"


def test_an_unmeasured_room_withholds_the_verdict(tmp_path):
    path = tmp_path / "table.ods"
    unmeasured = row("A3", "Cucina", 12.0, 1.8, 1.8, unmeasured_fillings=1)
    ods.write(str(path), HEADERS, [("Piano terra", [unmeasured])])
    (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
    cells = table.getElementsByType(TableRow)[2].getElementsByType(TableCell)
    formula = "urn:oasis:names:tc:opendocument:xmlns:table:1.0", "formula"
    assert extractText(cells[9]) == "da verificare"
    assert formula not in cells[9].attributes


def test_one_exempt_requirement_drops_its_term(tmp_path):
    path = tmp_path / "table.ods"
    exempt_air = row("A2", "Ripostiglio", 4.0, 0.6, 0.0)._replace(air_requirement=0.0)
    ods.write(str(path), HEADERS, [("Piano terra", [exempt_air])])
    (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
    cells = table.getElementsByType(TableRow)[2].getElementsByType(TableCell)
    formula = "urn:oasis:names:tc:opendocument:xmlns:table:1.0", "formula"
    assert cells[9].attributes[formula] == 'of:=IF([.H3]>=[.I3];"sì";"no")'
    assert "[.F" not in cells[9].attributes[formula]
    assert "[.G" not in cells[9].attributes[formula]


def test_both_exempt_requirements_drop_the_formula(tmp_path):
    path = tmp_path / "table.ods"
    exempt = row("A2", "Ripostiglio", 4.0, 0.0, 0.0)._replace(daylight_requirement=0.0, air_requirement=0.0)
    ods.write(str(path), HEADERS, [("Piano terra", [exempt])])
    (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
    cells = table.getElementsByType(TableRow)[2].getElementsByType(TableCell)
    formula = "urn:oasis:names:tc:opendocument:xmlns:table:1.0", "formula"
    assert formula not in cells[9].attributes
    assert extractText(cells[9]) == "sì"


def test_both_requirements_present_keep_the_and_formula(tmp_path):
    path = tmp_path / "table.ods"
    both = row("A1", "Soggiorno", 12.0, 1.8, 1.8)
    ods.write(str(path), HEADERS, [("Piano terra", [both])])
    (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
    cells = table.getElementsByType(TableRow)[2].getElementsByType(TableCell)
    formula = "urn:oasis:names:tc:opendocument:xmlns:table:1.0", "formula"
    assert cells[9].attributes[formula] == 'of:=IF(AND([.F3]>=[.G3];[.H3]>=[.I3]);"sì";"no")'


def test_a_measured_room_still_gets_the_formula(tmp_path):
    path = tmp_path / "table.ods"
    measured = row("A1", "Soggiorno", 12.0, 1.8, 1.8, unmeasured_fillings=0)
    ods.write(str(path), HEADERS, [("Piano terra", [measured])])
    (table,) = load(str(path)).spreadsheet.getElementsByType(Table)
    cells = table.getElementsByType(TableRow)[2].getElementsByType(TableCell)
    formula = "urn:oasis:names:tc:opendocument:xmlns:table:1.0", "formula"
    assert extractText(cells[9]) == "sì"
    assert cells[9].attributes[formula] == 'of:=IF(AND([.F3]>=[.G3];[.H3]>=[.I3]);"sì";"no")'
