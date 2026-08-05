import zipfile

from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.teletype import extractText

from urban_parameters.core import ods

HEADERS = ["Name", "Coefficiente", "Superficie lorda (m²)", "Altezza media (m)", "Volume urbanistico (m³)"]
ADDITIONS = [("Lotto A", 1.0, 500.0, 9.0, 4500.0), ("Lotto B", 1.0, 300.0, 10.0, 3000.0)]
DETRACTIONS = [("Autorimessa", -1.0, 120.0, 3.0, 360.0)]


def _sheet(path):
    """(texts, values, formulas) of the written sheet, one list per row."""
    table = load(str(path)).getElementsByType(Table)[0]
    texts, values, formulas = [], [], []
    for row in table.getElementsByType(TableRow):
        cells = row.getElementsByType(TableCell)
        texts.append([extractText(cell) for cell in cells])
        values.append([cell.getAttribute("value") for cell in cells])
        formulas.append([cell.getAttribute("formula") for cell in cells])
    return texts, values, formulas


def _write(path, sections):
    ods.write(path, HEADERS, sections, "Totale netto")
    return _sheet(path)


def test_a_single_block_ends_on_its_total(tmp_path):
    texts, _, _ = _write(tmp_path / "t.ods", [("Totale", ADDITIONS)])

    assert texts[0] == HEADERS
    assert texts[1] == ["Lotto A", "1.00", "500.00", "9.00", "4500.00"]
    # A total is not weighed: the rows it adds already are.
    assert texts[-1] == ["Totale", "", "800.00", "9.38", "7500.00"]
    assert len(texts) == 4


def test_detractions_get_their_own_block_and_a_net_total(tmp_path):
    texts, _, _ = _write(tmp_path / "t.ods", [("Totale lordo", ADDITIONS), ("Totale detrazioni", DETRACTIONS)])

    labels = [row[0] if row else "" for row in texts]
    assert labels == ["Name", "Lotto A", "Lotto B", "Totale lordo", "", "Autorimessa", "Totale detrazioni", "", "Totale netto"]
    # The detracted rows stay positive, the coefficient says why they are here.
    assert texts[5] == ["Autorimessa", "-1.00", "120.00", "3.00", "360.00"]
    assert texts[-1] == ["Totale netto", "", "680.00", "10.50", "7140.00"]


def test_totals_are_formulas_over_the_rows_they_add(tmp_path):
    _, _, formulas = _write(tmp_path / "t.ods", [("Totale lordo", ADDITIONS), ("Totale detrazioni", DETRACTIONS)])

    assert formulas[1] == [None] * 5  # the data rows hold values, not formulas
    assert formulas[3][2] == "of:=SUM([.C2:.C3])"
    assert formulas[3][4] == "of:=SUM([.E2:.E3])"
    assert formulas[6][2] == "of:=SUM([.C6:.C6])"
    # The net row subtracts the detractions block from the gross one.
    assert formulas[8][2] == "of:=[.C4]-[.C7]"
    assert formulas[8][4] == "of:=[.E4]-[.E7]"


def test_a_total_row_multiplies_back_to_its_own_volume(tmp_path):
    _, _, formulas = _write(tmp_path / "t.ods", [("Totale lordo", ADDITIONS), ("Totale detrazioni", DETRACTIONS)])

    # Every total's height is volume over area, read off its own row.
    assert formulas[3][3] == "of:=IF([.C4]=0;0;[.E4]/[.C4])"
    assert formulas[8][3] == "of:=IF([.C9]=0;0;[.E9]/[.C9])"


def test_formula_cells_carry_the_computed_value_too(tmp_path):
    """Bonsai's schedule renderer reads the cells, it does not recalculate."""
    _, values, _ = _write(tmp_path / "t.ods", [("Totale lordo", ADDITIONS), ("Totale detrazioni", DETRACTIONS)])

    assert float(values[3][2]) == 800.0
    assert float(values[8][2]) == 680.0
    assert float(values[8][4]) == 7140.0


def test_numbers_are_numbers_not_text(tmp_path):
    _, values, _ = _write(tmp_path / "t.ods", [("Totale", ADDITIONS)])

    assert values[0] == [None] * 5  # the headers
    assert [float(value) for value in values[1][1:]] == [1.0, 500.0, 9.0, 4500.0]


def test_the_file_is_a_readable_spreadsheet(tmp_path):
    path = tmp_path / "t.ods"
    ods.write(path, HEADERS, [("Totale", ADDITIONS)], "Totale netto")

    with zipfile.ZipFile(path) as archive:
        assert archive.read("mimetype") == b"application/vnd.oasis.opendocument.spreadsheet"
        assert "content.xml" in archive.namelist()
