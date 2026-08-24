import xml.etree.ElementTree as ET

from sheets_to_pdf.core import svg

SVG = f"{{{svg.SVG_NS}}}"
XLINK = f"{{{svg.XLINK_NS}}}"


def _root(body):
    return ET.fromstring(f'<svg xmlns="{svg.SVG_NS}" xmlns:xlink="{svg.XLINK_NS}">{body}</svg>')


def _tspans(root):
    return root.findall(f".//{SVG}tspan")


def test_dy_folds_into_y_against_the_tspan_own_font_size():
    """The <text> Bonsai writes carries font-size 0: the em is the tspan's."""
    root = _root(
        '<text font-size="0" text-anchor="middle">'
        '<tspan dy="0.5em" font-size="4.0" x="77.5" y="9.0">utile (m2)</tspan>'
        '<tspan dy="-0.5em" font-size="4.0" x="77.5" y="9.0">Superficie</tspan>'
        "</text>"
    )

    assert svg.fold_text_dy(root) is True

    below, above = _tspans(root)
    assert float(below.get("y")) == 11.0
    assert float(above.get("y")) == 7.0
    assert "dy" not in below.attrib and "dy" not in above.attrib


def test_dy_in_user_units_folds_too():
    root = _root('<text><tspan dy="-1.5" font-size="3" x="20" y="9">Superficie</tspan></text>')

    assert svg.fold_text_dy(root) is True
    assert float(_tspans(root)[0].get("y")) == 7.5


def test_stacked_lines_are_left_alone():
    """A dy without a y of its own stacks lines from the previous one, which
    typst places correctly: folding it would need the running position."""
    root = _root(
        '<text font-size="3" x="80" y="8">'
        '<tspan x="80">Superficie</tspan>'
        '<tspan x="80" dy="1.2em">utile</tspan>'
        "</text>"
    )

    assert svg.fold_text_dy(root) is False
    assert _tspans(root)[1].get("dy") == "1.2em"


def test_an_unresolvable_em_is_left_alone():
    root = _root('<text font-size="0"><tspan dy="-0.5em" x="20" y="9">Superficie</tspan></text>')

    assert svg.fold_text_dy(root) is False
    assert _tspans(root)[0].get("dy") == "-0.5em"


def test_prepare_returns_nothing_when_the_file_is_already_fine(tmp_path):
    path = tmp_path / "sheet.svg"
    path.write_text(f'<svg xmlns="{svg.SVG_NS}"><rect x="0" y="0" width="1" height="1" /></svg>')

    assert svg.prepare(str(path)) is None


def test_prepare_folds_and_inlines_nested_sheets(tmp_path):
    schedule = tmp_path / "schedule.svg"
    schedule.write_text(
        f'<svg xmlns="{svg.SVG_NS}" width="16" height="16">'
        '<text font-size="0"><tspan dy="-0.5em" font-size="4" x="8" y="9">Superficie</tspan></text>'
        "</svg>"
    )
    sheet = tmp_path / "sheet.svg"
    sheet.write_text(
        f'<svg xmlns="{svg.SVG_NS}" xmlns:xlink="{svg.XLINK_NS}">'
        f'<image xlink:href="schedule.svg" x="30" y="40" width="16" height="16" />'
        "</svg>"
    )

    root = ET.fromstring(svg.prepare(str(sheet)))

    assert root.find(f".//{SVG}image") is None
    inlined = root.find(f"{SVG}svg")
    assert (inlined.get("x"), inlined.get("width")) == ("30", "16")
    assert float(_tspans(root)[0].get("y")) == 7.0
