# Bonsai Salad — daylight_ventilation tool

"""The check as an OpenDocument spreadsheet, written with the odfpy Bonsai
already ships.

The table compares areas with areas: the requirement is printed as the square
metres the room has to reach, not as the ratio it is taken over, so a reader
checks a row by reading across it. Required area and verdict are formulas over
the net floor area beside them, so the sheet stays a live document: correct an
area by hand and both follow. Each formula cell also carries the value computed
here, which is what a reader that does not recalculate — Bonsai's own schedule
renderer among them — displays.

There are no subtotals. Adding areas across rooms says nothing when the subject
is a ratio.
"""

from odf.number import Number, NumberStyle
from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import Style, TableColumnProperties, TextProperties
from odf.table import Table, TableCell, TableColumn, TableRow
from odf.text import P

SHEET_NAME = "Rapporti aeroilluminanti"
AREA_DECIMALS = 2
DASH = "—"
YES, NO, UNVERIFIED = "sì", "no", "da verificare"

NET, REQUIREMENT, DAYLIGHT, AIR = "C", "D", "E", "F"


def _styles(doc):
    number_format = NumberStyle(name="SaladAreas")
    number_format.addElement(Number(decimalplaces=str(AREA_DECIMALS), minintegerdigits="1"))
    doc.styles.addElement(number_format)

    styles = {
        "text": Style(name="SaladText", family="table-cell"),
        "header": Style(name="SaladHeader", family="table-cell"),
        "area": Style(name="SaladArea", family="table-cell", datastylename="SaladAreas"),
        "label_column": Style(name="SaladLabelColumn", family="table-column"),
        "value_column": Style(name="SaladValueColumn", family="table-column"),
    }
    styles["header"].addElement(TextProperties(fontweight="bold"))
    styles["label_column"].addElement(TableColumnProperties(columnwidth="5cm"))
    styles["value_column"].addElement(TableColumnProperties(columnwidth="3cm"))
    for style in styles.values():
        doc.automaticstyles.addElement(style)
    return styles


def _text(value, style):
    cell = TableCell(valuetype="string", stylename=style)
    cell.addElement(P(text=value or ""))
    return cell


def _number(value, style, decimals, formula=None):
    attributes = {"valuetype": "float", "value": float(value), "stylename": style}
    if formula:
        attributes["formula"] = formula
    cell = TableCell(**attributes)
    cell.addElement(P(text=f"{value:.{decimals}f}"))
    return cell


def _verdict(row, number, styles):
    """An unmeasured filling withholds the verdict: a formula would recompute to
    sì/no the moment the sheet is opened, overwriting the honest unknown.

    Each comparison is built against the net floor area rather than against the
    requirement column, which holds two values when the two requirements differ
    and could not be read by one term. Only the comparisons a requirement
    actually imposes enter it.

    A room whose floor could not be read requires zero square metres by
    arithmetic and would pass on that alone, so the formula asks for the floor
    first — the same verdict the calculation reaches here."""
    if row.unmeasured_fillings > 0:
        return _text(UNVERIFIED, styles["text"])

    terms = []
    if row.daylight_requirement > 0:
        terms.append(f"[.{DAYLIGHT}{number}]>=[.{NET}{number}]*{row.daylight_requirement}")
    if row.air_requirement > 0:
        terms.append(f"[.{AIR}{number}]>=[.{NET}{number}]*{row.air_requirement}")

    if not terms:
        return _text(YES, styles["text"])

    condition = f"AND({';'.join([f'[.{NET}{number}]>0', *terms])})"
    cell = TableCell(
        valuetype="string",
        stylename=styles["text"],
        formula=f'of:=IF({condition};"{YES}";"{NO}")',
    )
    cell.addElement(P(text=YES if row.verified else NO))
    return cell


def _required(net, requirement):
    return f"{net * requirement:.{AREA_DECIMALS}f}" if requirement > 0 else DASH


def _requirement_cell(row, number, styles):
    """What the room has to reach, in the same unit as the areas beside it: one
    value where the two requirements agree, illuminazione and aerazione in that
    order where they do not.

    A room that requires nothing gets a dash — a bar it is not held to would only
    invite the reader to compare it with something. Two values cannot be a
    formula, and nothing reads this column: the verdict is built from the net
    floor area directly, so it never goes stale against it."""
    daylight, air = row.daylight_requirement, row.air_requirement
    if daylight <= 0 and air <= 0:
        return _text(DASH, styles["text"])
    if daylight == air:
        return _number(row.net * daylight, styles["area"], AREA_DECIMALS, f"of:=[.{NET}{number}]*{daylight}")
    return _text(" / ".join(_required(row.net, requirement) for requirement in (daylight, air)), styles["text"])


def _row(cells):
    line = TableRow()
    for cell in cells:
        line.addElement(cell)
    return line


def write(path, headers, sections):
    """headers, then one block per storey as (label, [Row]), the storey's name on
    a line of its own above its rooms."""
    doc = OpenDocumentSpreadsheet()
    styles = _styles(doc)

    table = Table(name=SHEET_NAME)
    table.addElement(TableColumn(numbercolumnsrepeated="2", stylename=styles["label_column"]))
    table.addElement(TableColumn(numbercolumnsrepeated=str(len(headers) - 2), stylename=styles["value_column"]))
    table.addElement(_row([_text(header, styles["header"]) for header in headers]))

    number = 1  # The headers took row 1.
    for label, rows in sections:
        number += 1
        table.addElement(_row([_text(label, styles["header"])]))
        for row in rows:
            number += 1
            table.addElement(
                _row(
                    [
                        _text(row.identification, styles["text"]),
                        _text(row.name, styles["text"]),
                        _number(row.net, styles["area"], AREA_DECIMALS),
                        _requirement_cell(row, number, styles),
                        _number(row.daylight, styles["area"], AREA_DECIMALS),
                        _number(row.air, styles["area"], AREA_DECIMALS),
                        _verdict(row, number, styles),
                    ]
                )
            )

    doc.spreadsheet.addElement(table)
    doc.save(path)
