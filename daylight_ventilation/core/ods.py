# Bonsai Salad — daylight_ventilation tool

"""The check as an OpenDocument spreadsheet, written with the odfpy Bonsai
already ships.

Ratios and verdicts are formulas over the areas beside them, so the sheet stays
a live document: correct an area by hand and the verdict follows. Each formula
cell also carries the value computed here, which is what a reader that does not
recalculate — Bonsai's own schedule renderer among them — displays.

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
RATIO_DECIMALS = 3
DASH = "—"
YES, NO, UNVERIFIED = "sì", "no", "da verificare"

NET, AIR, DAYLIGHT = "C", "D", "E"
AIR_RATIO, AIR_REQUIREMENT, DAYLIGHT_RATIO, DAYLIGHT_REQUIREMENT = "F", "G", "H", "I"


def _styles(doc):
    for name, decimals in (("SaladAreas", AREA_DECIMALS), ("SaladRatios", RATIO_DECIMALS)):
        number_format = NumberStyle(name=name)
        number_format.addElement(Number(decimalplaces=str(decimals), minintegerdigits="1"))
        doc.styles.addElement(number_format)

    styles = {
        "text": Style(name="SaladText", family="table-cell"),
        "header": Style(name="SaladHeader", family="table-cell"),
        "area": Style(name="SaladArea", family="table-cell", datastylename="SaladAreas"),
        "ratio": Style(name="SaladRatio", family="table-cell", datastylename="SaladRatios"),
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

    Only the comparisons an exempt requirement actually imposes enter the
    formula: a term built over a dashed ratio cell would make the verdict
    depend on how the reading application ranks text against numbers."""
    if row.unmeasured > 0:
        cell = TableCell(valuetype="string", stylename=styles["text"])
        cell.addElement(P(text=UNVERIFIED))
        return cell

    terms = []
    if row.air_requirement > 0:
        terms.append(f"[.{AIR_RATIO}{number}]>=[.{AIR_REQUIREMENT}{number}]")
    if row.daylight_requirement > 0:
        terms.append(f"[.{DAYLIGHT_RATIO}{number}]>=[.{DAYLIGHT_REQUIREMENT}{number}]")

    if not terms:
        cell = TableCell(valuetype="string", stylename=styles["text"])
        cell.addElement(P(text=YES))
        return cell

    condition = terms[0] if len(terms) == 1 else f"AND({terms[0]};{terms[1]})"
    cell = TableCell(
        valuetype="string",
        stylename=styles["text"],
        formula=f'of:=IF({condition};"{YES}";"{NO}")',
    )
    cell.addElement(P(text=YES if row.verified else NO))
    return cell


def _ratio_cell(row, ratio, requirement, area_column, number, styles):
    """A room that requires nothing gets a dash: a ratio it is not measured
    against would only invite the reader to compare it with something."""
    if requirement <= 0:
        return _text(DASH, styles["text"])
    return _number(
        ratio,
        styles["ratio"],
        RATIO_DECIMALS,
        f"of:=IF([.{NET}{number}]=0;0;[.{area_column}{number}]/[.{NET}{number}])",
    )


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
                        _number(row.air, styles["area"], AREA_DECIMALS),
                        _number(row.daylight, styles["area"], AREA_DECIMALS),
                        _ratio_cell(row, row.air_ratio, row.air_requirement, AIR, number, styles),
                        _number(row.air_requirement, styles["ratio"], RATIO_DECIMALS),
                        _ratio_cell(row, row.daylight_ratio, row.daylight_requirement, DAYLIGHT, number, styles),
                        _number(row.daylight_requirement, styles["ratio"], RATIO_DECIMALS),
                        _verdict(row, number, styles),
                    ]
                )
            )

    doc.spreadsheet.addElement(table)
    doc.save(path)
