import pytest
from odf import teletype
from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.style import Style, TableColumnProperties, TableRowProperties
from odf.table import Table, TableCell, TableColumn, TableRow
from odf.text import P

from ifc_spaces.core import schedule, sheet_style

HEADINGS = ("Codice", "Nome esteso", "Superficie utile (m²)", "Verificato")
ROOMS = (("A01", "zona giorno", "48,62", "sì"), ("A02", "vano scale", "7,95", "—"))

# (width in mm, horizontal alignment): a centred code, a description left where
# it starts, a measure read against the ones above it, a centred verdict.
LAYOUT = ((15.0, "center"), (52.0, None), (19.0, "end"), (16.0, "center"))


@pytest.fixture
def sheet(tmp_path):
    """An ODS as the exporter leaves it: values and nothing else, no column
    styles, no print range."""
    path = str(tmp_path / "abaco.ods")
    document = OpenDocumentSpreadsheet()
    table = Table(name="Sheet1")
    for values in (HEADINGS, *ROOMS):
        row = TableRow()
        for value in values:
            cell = TableCell(valuetype="string")
            cell.addElement(P(text=value))
            row.addElement(cell)
        table.addElement(row)
    document.spreadsheet.addElement(table)
    document.save(path)
    return path


def reopen(path):
    """The styled sheet, and every style in it flattened to a dict of
    properties the way Bonsai's renderer reads them."""
    document = load(path)
    styles = {}
    for style in document.getElementsByType(Style):
        properties = {}
        for child in style.childNodes:
            properties.update({key[1]: value for key, value in child.attributes.items()})
        styles[style.getAttribute("name")] = properties
    return document.spreadsheet.getElementsByType(Table)[0], styles


def cell_styles(table, styles):
    """The resolved style of every cell, row by row."""
    resolved = []
    for row in table.getElementsByType(TableRow):
        resolved.append([styles[cell.getAttribute("stylename")] for cell in row.getElementsByType(TableCell)])
    return resolved


def test_columns_carry_their_width(sheet):
    sheet_style.apply(sheet, LAYOUT)
    table, styles = reopen(sheet)
    columns = table.getElementsByType(TableColumn)
    widths = [styles[column.getAttribute("stylename")]["column-width"] for column in columns]
    assert widths == ["15.0mm", "52.0mm", "19.0mm", "16.0mm"]


def test_the_heading_is_twice_a_room_and_the_rooms_are_alike(sheet):
    sheet_style.apply(sheet, LAYOUT)
    table, styles = reopen(sheet)
    heights = [styles[row.getAttribute("stylename")]["row-height"] for row in table.getElementsByType(TableRow)]
    assert heights == ["16.0mm", "8.0mm", "8.0mm"]


def test_rows_keep_the_height_they_are_given(sheet):
    """An optimal height would let the renderer shrink the heading back."""
    sheet_style.apply(sheet, LAYOUT)
    table, styles = reopen(sheet)
    for row in table.getElementsByType(TableRow):
        assert styles[row.getAttribute("stylename")]["use-optimal-row-height"] == "false"


def test_the_print_range_is_the_content(sheet):
    sheet_style.apply(sheet, LAYOUT)
    table, _ = reopen(sheet)
    assert table.getAttribute("printranges") == "Sheet1.A1:Sheet1.D3"


def test_the_heading_wraps_and_the_rooms_do_not(sheet):
    sheet_style.apply(sheet, LAYOUT)
    heading, room, _ = cell_styles(*reopen(sheet))
    assert [cell["wrap-option"] for cell in heading] == ["wrap"] * 4
    assert [cell.get("wrap-option") for cell in room] == [None] * 4


def test_everything_sits_in_the_middle_of_its_row(sheet):
    sheet_style.apply(sheet, LAYOUT)
    for row in cell_styles(*reopen(sheet)):
        for cell in row:
            assert cell["vertical-align"] == "middle"


def test_a_room_follows_the_alignment_of_its_column(sheet):
    sheet_style.apply(sheet, LAYOUT)
    _, room, _ = cell_styles(*reopen(sheet))
    assert [cell.get("text-align") for cell in room] == ["center", None, "end", "center"]


def test_the_heading_is_centred_except_over_a_column_that_starts_left(sheet):
    """A heading is a label, not a measure, so it does not follow a right
    alignment; it stays left only where its column does."""
    sheet_style.apply(sheet, LAYOUT)
    heading, _, _ = cell_styles(*reopen(sheet))
    assert [cell.get("text-align") for cell in heading] == ["center", None, "center", "center"]


def test_the_heading_is_set_smaller_than_the_rooms(sheet):
    """It carries the longest text of the table with two lines to fit it in."""
    sheet_style.apply(sheet, LAYOUT)
    heading, room, _ = cell_styles(*reopen(sheet))
    assert [cell["font-size"] for cell in heading] == ["9pt"] * 4
    assert [cell.get("font-size") for cell in room] == [None] * 4


def test_the_heading_is_framed_on_every_side(sheet):
    sheet_style.apply(sheet, LAYOUT)
    heading, _, _ = cell_styles(*reopen(sheet))
    for cell in heading:
        assert cell["border"] == sheet_style.FRAME
        assert "border-top" not in cell


def test_a_room_is_ruled_above_and_below_only(sheet):
    sheet_style.apply(sheet, LAYOUT)
    _, room, _ = cell_styles(*reopen(sheet))
    for cell in room:
        assert cell["border-top"] == cell["border-bottom"] == sheet_style.RULE
        assert "border" not in cell
        assert "border-left" not in cell and "border-right" not in cell


def test_the_rule_is_nine_hundredths_of_a_millimetre():
    assert sheet_style.RULE.startswith("0.09mm ")


def test_the_table_is_nineteen_centimetres_wide():
    assert sum(width for width, _ in schedule.LAYOUT) == pytest.approx(190.0)


def test_every_column_of_the_schedule_has_a_width():
    assert len(schedule.LAYOUT) == len(schedule.COLUMNS)


def test_every_measured_column_is_read_against_the_ones_above_it():
    for (_, alignment), (_, _, unit, _, _) in zip(schedule.LAYOUT, schedule.COLUMNS_WITH_UNITS):
        if unit:
            assert alignment == "end"


def test_the_code_and_the_verdict_sit_in_the_middle():
    assert schedule.LAYOUT[0][1] == schedule.LAYOUT[-1][1] == "center"
