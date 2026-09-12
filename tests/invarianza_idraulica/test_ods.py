import shapely
from odf.opendocument import load
from odf.table import Table, TableRow
from odf.teletype import extractText

from invarianza_idraulica.core import ods, surfaces


def measurement():
    boundary = shapely.box(0, 0, 20, 10)
    rows = [
        surfaces.Row("Asfalto", 0.9, "materiale", 100.0, shapely.box(0, 0, 10, 10)),
        surfaces.Row("Prato", 0.2, "materiale", 100.0, shapely.box(10, 0, 20, 10)),
    ]
    impermeable = sum(row.area * row.coefficient for row in rows)
    return surfaces.Measurement(boundary, 200.0, rows, impermeable, impermeable / 200.0, [], [])


def _cells(path):
    document = load(str(path))
    table = document.spreadsheet.getElementsByType(Table)[0]
    return [[extractText(cell) for cell in row.childNodes] for row in table.getElementsByType(TableRow)]


def test_the_table_holds_a_row_per_group_and_the_totals(tmp_path):
    path = tmp_path / "superfici.ods"
    ods.write(measurement(), str(path), "Ambito di intervento")
    labels = [row[0] for row in _cells(path) if row]
    assert "Asfalto" in labels
    assert "Prato" in labels
    assert any("impermeabile" in label.lower() for label in labels)
    assert any("medio" in label.lower() for label in labels)


def test_the_warnings_are_carried_to_the_end(tmp_path):
    measured = measurement()._replace(unmeasurable=[object()])
    path = tmp_path / "superfici.ods"
    ods.write(measured, str(path), "Ambito")
    labels = [row[0] for row in _cells(path) if row]
    assert any("non misurabili" in label for label in labels)
