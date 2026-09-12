# Bonsai Salad — invarianza_idraulica tool

"""La tabella delle superfici come foglio OpenDocument, con l'odfpy che Bonsai
già distribuisce.

Le segnalazioni scendono in coda al foglio: una tabella consegnata senza di
esse mentirebbe per omissione, e chi la legge non ha modo di sapere che il
calcolo ha lasciato qualcosa indietro.
"""

from odf.number import Number, NumberStyle
from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import Style, TableColumnProperties, TextProperties
from odf.table import Table, TableCell, TableColumn, TableRow
from odf.text import P

SHEET_NAME = "Superfici scolanti"
AREA_DECIMALS = 2
COEFFICIENT_DECIMALS = 3

HEADERS = ("Superficie", "Coefficiente di deflusso", "Area [m²]", "Area equivalente [m²]")
TOTAL = "Superficie complessiva"
IMPERMEABLE = "Superficie impermeabile equivalente"
MEAN = "Coefficiente di deflusso medio"

AMBIGUOUS = "{count} elementi con materiali discordi, contati come impermeabili"
UNMEASURABLE = "{count} corpi non misurabili, esclusi dal conteggio"


def _styles(document):
    number_format = NumberStyle(name="SaladAreas")
    number_format.addElement(Number(decimalplaces=str(AREA_DECIMALS), minintegerdigits="1"))
    document.styles.addElement(number_format)

    styles = {
        "text": Style(name="SaladText", family="table-cell"),
        "header": Style(name="SaladHeader", family="table-cell"),
        "area": Style(name="SaladArea", family="table-cell", datastylename="SaladAreas"),
        "label_column": Style(name="SaladLabelColumn", family="table-column"),
        "value_column": Style(name="SaladValueColumn", family="table-column"),
    }
    styles["header"].addElement(TextProperties(fontweight="bold"))
    styles["label_column"].addElement(TableColumnProperties(columnwidth="7cm"))
    styles["value_column"].addElement(TableColumnProperties(columnwidth="4cm"))
    for style in styles.values():
        document.automaticstyles.addElement(style)
    return styles


def _text(value, style):
    cell = TableCell(valuetype="string", stylename=style)
    cell.addElement(P(text=value or ""))
    return cell


def _number(value, style, decimals):
    cell = TableCell(valuetype="float", value=float(value), stylename=style)
    cell.addElement(P(text=f"{value:.{decimals}f}"))
    return cell


def _row(cells):
    row = TableRow()
    for cell in cells:
        row.addElement(cell)
    return row


def write(measurement, path, title):
    """Scrive il foglio: intestazione, una riga per gruppo, i totali, e in coda
    ciò che il calcolo non ha potuto risolvere."""
    document = OpenDocumentSpreadsheet()
    styles = _styles(document)
    table = Table(name=SHEET_NAME)
    table.addElement(TableColumn(stylename=styles["label_column"]))
    for _ in HEADERS[1:]:
        table.addElement(TableColumn(stylename=styles["value_column"]))

    table.addElement(_row([_text(title, styles["header"])]))
    table.addElement(_row([_text(header, styles["header"]) for header in HEADERS]))

    for row in measurement.rows:
        table.addElement(
            _row(
                [
                    _text(row.label, styles["text"]),
                    _number(row.coefficient, styles["text"], COEFFICIENT_DECIMALS),
                    _number(row.area, styles["area"], AREA_DECIMALS),
                    _number(row.area * row.coefficient, styles["area"], AREA_DECIMALS),
                ]
            )
        )

    table.addElement(_row([]))
    table.addElement(
        _row(
            [
                _text(TOTAL, styles["header"]),
                _text("", styles["text"]),
                _number(measurement.total, styles["area"], AREA_DECIMALS),
            ]
        )
    )
    table.addElement(
        _row(
            [
                _text(IMPERMEABLE, styles["header"]),
                _text("", styles["text"]),
                _text("", styles["text"]),
                _number(measurement.impermeable, styles["area"], AREA_DECIMALS),
            ]
        )
    )
    table.addElement(
        _row(
            [
                _text(MEAN, styles["header"]),
                _number(measurement.mean_coefficient, styles["text"], COEFFICIENT_DECIMALS),
            ]
        )
    )

    for warning, involved in ((AMBIGUOUS, measurement.ambiguous), (UNMEASURABLE, measurement.unmeasurable)):
        if involved:
            table.addElement(_row([]))
            table.addElement(_row([_text(warning.format(count=len(involved)), styles["text"])]))

    document.spreadsheet.addElement(table)
    document.save(path)
