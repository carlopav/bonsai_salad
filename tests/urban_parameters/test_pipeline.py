"""Model to spreadsheet, the way the export operator walks it."""

import pytest

from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.teletype import extractText

from urban_parameters.core import ods, quantities

LOT = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]  # 50 m²
GARAGE = [(0.0, 0.0), (4.0, 0.0), (4.0, 5.0), (0.0, 5.0)]  # 20 m²
TYPE = "Volume urbanistico"


@pytest.fixture
def project(ifc_file, add_zone):
    add_zone("Lotto A", LOT, 3.0, object_type=TYPE)
    garage = add_zone("Garage", GARAGE, 3.0, object_type=TYPE)
    add_zone("Fuori computo", LOT, 3.0, object_type="Altro")
    quantities.write_coefficient(ifc_file, garage, -1.0)
    return ifc_file


def test_only_the_chosen_type_is_measured(project):
    rows = quantities.measure_zones(project, quantities.spatial_zones(project, TYPE))
    assert [row.name for row in rows] == ["Garage", "Lotto A"]


def test_the_detraction_is_subtracted_from_the_totals(project):
    rows = quantities.measure_zones(project, quantities.spatial_zones(project, TYPE))
    area, height, volume = quantities.totals(rows)
    assert area == pytest.approx(30.0, rel=1e-4)
    assert volume == pytest.approx(90.0, rel=1e-4)
    assert height == pytest.approx(3.0, rel=1e-4)


def test_the_written_table_reads_as_two_blocks_and_a_net_total(project, tmp_path):
    rows = quantities.measure_zones(project, quantities.spatial_zones(project, TYPE))
    path = tmp_path / "table.ods"
    ods.write(path, quantities.headers(project), quantities.sections(rows), quantities.NET)

    table = load(str(path)).getElementsByType(Table)[0]
    written = [[extractText(cell) for cell in row.getElementsByType(TableCell)] for row in table.getElementsByType(TableRow)]
    assert [row[0] if row else "" for row in written] == [
        "Name",
        "Lotto A",
        quantities.ADDITIONS,
        "",
        "Garage",
        quantities.DETRACTIONS,
        "",
        quantities.NET,
    ]
    assert written[1] == ["Lotto A", "1.00", "50.00", "3.00", "150.00"]
    assert written[4] == ["Garage", "-1.00", "20.00", "3.00", "60.00"]
    assert written[-1] == [quantities.NET, "", "30.00", "3.00", "90.00"]
