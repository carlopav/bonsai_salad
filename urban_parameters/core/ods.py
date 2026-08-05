# Bonsai Salad — urban_parameters tool

"""The urban parameters table as an OpenDocument spreadsheet, written with the
odfpy Bonsai already ships.

Subtotals and the net total are written as formulas over the rows they add up,
so the sheet stays a live document: edit a row and the totals follow. Each
formula cell also carries the value we computed, which is what a reader that
does not recalculate — Bonsai's own schedule renderer among them — displays.
"""

from odf.number import Number, NumberStyle
from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import Style, TableColumnProperties, TextProperties
from odf.table import Table, TableCell, TableColumn, TableRow
from odf.text import P

SHEET_NAME = "Parametri urbanistici"
DECIMALS = 2

# Where each value sits in a row: name, the coefficient that weighs the row,
# then the three measures.
COEFFICIENT_COLUMN, AREA_COLUMN, HEIGHT_COLUMN, VOLUME_COLUMN = 1, 2, 3, 4


def _column(index):
    return chr(ord("A") + index)


def _cell_ref(index, row):
    return f"[.{_column(index)}{row}]"


def _styles(doc):
    """Bold headers and two decimal numbers. The number format lives in the
    document styles, the cell styles that point at it in the automatic ones:
    that is where a reader looks for them."""
    number_format = NumberStyle(name="SaladDecimals")
    number_format.addElement(Number(decimalplaces=str(DECIMALS), minintegerdigits="1", grouping="true"))
    doc.styles.addElement(number_format)

    styles = {
        "text": Style(name="SaladText", family="table-cell"),
        "header": Style(name="SaladHeader", family="table-cell"),
        "number": Style(name="SaladNumber", family="table-cell", datastylename="SaladDecimals"),
        "total": Style(name="SaladTotal", family="table-cell", datastylename="SaladDecimals"),
        "name_column": Style(name="SaladNameColumn", family="table-column"),
        "coefficient_column": Style(name="SaladCoefficientColumn", family="table-column"),
        "value_column": Style(name="SaladValueColumn", family="table-column"),
    }
    styles["header"].addElement(TextProperties(fontweight="bold"))
    styles["total"].addElement(TextProperties(fontweight="bold"))
    styles["name_column"].addElement(TableColumnProperties(columnwidth="7cm"))
    # A coefficient is two digits wide: the schedule is placed on a sheet, and
    # every millimetre the table does not need is one the drawing keeps.
    styles["coefficient_column"].addElement(TableColumnProperties(columnwidth="2.5cm"))
    styles["value_column"].addElement(TableColumnProperties(columnwidth="4.5cm"))
    for style in styles.values():
        doc.automaticstyles.addElement(style)
    return styles


def _text(value, style):
    cell = TableCell(valuetype="string", stylename=style)
    cell.addElement(P(text=value or ""))
    return cell


def _number(value, style, formula=None):
    attributes = {"valuetype": "float", "value": float(value), "stylename": style}
    if formula:
        attributes["formula"] = formula
    cell = TableCell(**attributes)
    cell.addElement(P(text=f"{value:.{DECIMALS}f}"))
    return cell


def _row(cells):
    row = TableRow()
    for cell in cells:
        row.addElement(cell)
    return row


def _mean_height(area, volume):
    return volume / area if area else 0.0


def _sum_formula(index, first_row, rows):
    if not rows:
        return None
    column = _column(index)
    return f"of:=SUM([.{column}{first_row}:.{column}{first_row + len(rows) - 1}])"


def _total_row(label, area, volume, own_row, styles, area_formula=None, volume_formula=None):
    """A closing row: area and volume, and between them no coefficient — a
    total is not weighed, the rows it adds already are. The height is derived
    from the two so the row multiplies back like every other."""
    return _row(
        [
            _text(label, styles["header"]),
            _text("", styles["header"]),
            _number(area, styles["total"], area_formula),
            _number(_mean_height(area, volume), styles["total"], _ratio_formula(own_row)),
            _number(volume, styles["total"], volume_formula),
        ]
    )


def _subtotal(label, rows, first_row, own_row, styles):
    """The closing row of a block, summed over the block's own rows."""
    return _total_row(
        label,
        sum(row[AREA_COLUMN] for row in rows),
        sum(row[VOLUME_COLUMN] for row in rows),
        own_row,
        styles,
        _sum_formula(AREA_COLUMN, first_row, rows),
        _sum_formula(VOLUME_COLUMN, first_row, rows),
    )


def _ratio_formula(row):
    """The mean height of a total row, guarded: an empty block would divide by
    zero and put an error where a number belongs."""
    area, volume = _cell_ref(AREA_COLUMN, row), _cell_ref(VOLUME_COLUMN, row)
    return f"of:=IF({area}=0;0;{volume}/{area})"


def _difference_formula(index, rows):
    """The first subtotal less all the others, one term per block."""
    first, rest = rows[0], rows[1:]
    return "of:=" + _cell_ref(index, first) + "".join(f"-{_cell_ref(index, row)}" for row in rest)


def write(path, headers, sections, net_label):
    """headers, then one block per section as (label, [(name, coefficient,
    area, height, volume)]), each closed by its subtotal. With more than one
    block a final row subtracts the later subtotals from the first: the blocks
    after the first are what gets detracted."""
    doc = OpenDocumentSpreadsheet()
    styles = _styles(doc)

    table = Table(name=SHEET_NAME)
    table.addElement(TableColumn(stylename=styles["name_column"]))
    table.addElement(TableColumn(stylename=styles["coefficient_column"]))
    table.addElement(TableColumn(numbercolumnsrepeated=str(len(headers) - 2), stylename=styles["value_column"]))
    table.addElement(_row([_text(header, styles["header"]) for header in headers]))

    subtotal_rows = []
    number = 2  # The headers took row 1.
    for index, (label, rows) in enumerate(sections):
        if index:
            table.addElement(TableRow())  # A blank row between the blocks.
            number += 1
        for row in rows:
            table.addElement(
                _row([_text(row[0], styles["text"])] + [_number(value, styles["number"]) for value in row[1:]])
            )
        first_row, number = number, number + len(rows)
        table.addElement(_subtotal(label, rows, first_row, number, styles))
        subtotal_rows.append(number)
        number += 1

    if len(subtotal_rows) > 1:
        table.addElement(TableRow())
        number += 1
        # The first block adds up, every block after it is detracted.
        signs = [1] + [-1] * (len(sections) - 1)
        area = sum(sign * sum(row[AREA_COLUMN] for row in rows) for sign, (_, rows) in zip(signs, sections))
        volume = sum(sign * sum(row[VOLUME_COLUMN] for row in rows) for sign, (_, rows) in zip(signs, sections))
        table.addElement(
            _total_row(
                net_label,
                area,
                volume,
                number,
                styles,
                _difference_formula(AREA_COLUMN, subtotal_rows),
                _difference_formula(VOLUME_COLUMN, subtotal_rows),
            )
        )

    doc.spreadsheet.addElement(table)
    doc.save(path)
