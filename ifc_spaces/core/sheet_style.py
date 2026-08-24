# Bonsai Salad — ifc_spaces tool

"""The look of the schedule, written into the spreadsheet itself.

Nothing here decides what the table says. It decides how wide each column is,
how tall each row, where the text sits in its cell and which lines are drawn —
and it writes all of that into the ODS, because that is where Bonsai's schedule
renderer reads it from. The drawing then follows the sheet.

Every export rewrites the file and restyles it, so the formatting is
regenerated rather than preserved: there is nothing to lose and nothing to
carry over.
"""

from odf.opendocument import load
from odf.style import (
    ParagraphProperties,
    Style,
    TableCellProperties,
    TableColumnProperties,
    TableRowProperties,
    TextProperties,
)
from odf.table import Table, TableCell, TableColumn, TableRow

HEADING_HEIGHT, ROOM_HEIGHT = 16.0, 8.0

# The heading is framed on every side and the rooms are ruled between them, so
# the table reads as a head over a body rather than as a uniform grid. Bonsai
# draws a cell's own borders only since the renderer learned to read them;
# before that both of these came out as the stylesheet's single hairline.
FRAME = "0.5pt solid #000000"
RULE = "0.09mm solid #000000"


def _length(value):
    return f"{value}mm"


def _column_letter(index):
    """A, B, ... Z, AA — the spreadsheet's own name for a column."""
    letter = ""
    while True:
        index, remainder = divmod(index, 26)
        letter = chr(ord("A") + remainder) + letter
        if not index:
            return letter
        index -= 1


def _add(document, name, family, *properties):
    style = Style(name=name, family=family)
    for element in properties:
        style.addElement(element)
    document.automaticstyles.addElement(style)
    return name


def _row(document, name, height):
    """A row of a height it keeps: an optimal height would let the application
    shrink the heading back to fit its text."""
    return _add(
        document,
        name,
        "table-row",
        TableRowProperties(rowheight=_length(height), useoptimalrowheight="false"),
    )


# A heading is a label, not a measure: it does not follow a column aligned right,
# and starts where its column starts only where the column itself does. It is set
# smaller than the rooms below it because it has the longest text of the table
# and two lines to fit it in.
HEADING_ALIGNMENT, HEADING_FONT_SIZE = "center", "9pt"


def _cells(document):
    """Names a cell style per combination of row and alignment, making each one
    once however many cells end up wearing it."""
    made = {}

    def style(heading, alignment):
        if (heading, alignment) not in made:
            name = f"ce-{'heading' if heading else 'room'}-{alignment or 'start'}"
            borders = {"border": FRAME, "wrapoption": "wrap"} if heading else {"bordertop": RULE, "borderbottom": RULE}
            properties = [TableCellProperties(verticalalign="middle", **borders)]
            if heading:
                properties.append(TextProperties(fontsize=HEADING_FONT_SIZE))
            if alignment:
                properties.append(ParagraphProperties(textalign=alignment))
            made[(heading, alignment)] = _add(document, name, "table-cell", *properties)
        return made[(heading, alignment)]

    return style


def _widths(document, table, layout, before):
    """One column element per column, each carrying its width. A sheet written
    from a dataframe declares no columns at all, and the renderer then falls
    back on a constant width for every one of them."""
    for index, (width, _) in enumerate(layout):
        name = _add(
            document,
            f"co{index + 1}",
            "table-column",
            TableColumnProperties(columnwidth=_length(width)),
        )
        table.insertBefore(TableColumn(stylename=name), before)


def apply(path, layout):
    """Writes the schedule's look into the ODS: column widths, row heights,
    alignment, borders and the print range.

    `layout` is (width in mm, alignment) per column, in table order, where the
    alignment is the one a spreadsheet writes: None where the text starts at the
    column, "center", or "end".
    """
    document = load(path)
    table = document.spreadsheet.getElementsByType(Table)[0]
    rows = table.getElementsByType(TableRow)
    if not rows:
        return

    heading_row = _row(document, "ro-heading", HEADING_HEIGHT)
    room_row = _row(document, "ro-room", ROOM_HEIGHT)
    cell_style = _cells(document)

    _widths(document, table, layout, rows[0])

    for index, row in enumerate(rows):
        heading = index == 0
        row.setAttribute("stylename", heading_row if heading else room_row)
        for position, cell in enumerate(row.getElementsByType(TableCell)):
            alignment = layout[position][1] if position < len(layout) else None
            if heading and alignment:
                alignment = HEADING_ALIGNMENT
            cell.setAttribute("stylename", cell_style(heading, alignment))

    name = table.getAttribute("name")
    last = f"{_column_letter(len(layout) - 1)}{len(rows)}"
    table.setAttribute("printranges", f"{name}.A1:{name}.{last}")
    document.save(path)
