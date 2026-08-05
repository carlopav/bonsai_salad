import pytest

import ifcopenshell.util.element

from urban_parameters.core import quantities

from . import conftest

RECTANGLE = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]
L_SHAPE = [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (6.0, 5.0), (6.0, 8.0), (0.0, 8.0)]


def test_measures_a_prism(add_zone):
    area, height, volume = quantities.measure(add_zone("A", RECTANGLE, 3.0))
    assert area == pytest.approx(50.0, rel=1e-4)
    assert height == pytest.approx(3.0, rel=1e-4)
    assert volume == pytest.approx(150.0, rel=1e-4)


def test_area_is_the_footprint_not_the_bounding_box(add_zone):
    area, _, volume = quantities.measure(add_zone("L", L_SHAPE, 3.0))
    assert area == pytest.approx(68.0, rel=1e-4)
    assert volume == pytest.approx(204.0, rel=1e-4)


def test_elevation_does_not_change_the_measures(add_zone):
    on_the_ground = quantities.measure(add_zone("A", RECTANGLE, 3.0))
    lifted = quantities.measure(add_zone("B", RECTANGLE, 3.0, elevation=12.0))
    assert lifted == pytest.approx(on_the_ground, rel=1e-4)


def test_measures_a_closed_face_set(add_tessellated_zone):
    """The guard has to let a sound mesh through, not just reject broken ones."""
    area, height, volume = quantities.measure(add_tessellated_zone("Box"))
    assert area == pytest.approx(50.0, rel=1e-4)
    assert height == pytest.approx(3.0, rel=1e-4)
    assert volume == pytest.approx(150.0, rel=1e-4)


def test_a_mesh_with_a_missing_face_is_refused(add_tessellated_zone):
    """The defect found in the field: one wall of the zone never modelled."""
    walls = [face for face in conftest.BOX_FACES if face != [3, 4, 8, 7]]
    zone = add_tessellated_zone("Open", faces=walls)
    with pytest.raises(quantities.UnmeasurableGeometry) as raised:
        quantities.measure(zone)
    assert "Open" in str(raised.value)
    assert "not closed" in str(raised.value)


def test_a_mesh_with_a_flipped_face_is_refused(add_tessellated_zone):
    """Closed but inconsistently wound: get_volume would quietly subtract the
    reversed face instead of adding it."""
    faces = [list(reversed(face)) if face == [5, 6, 7, 8] else face for face in conftest.BOX_FACES]
    with pytest.raises(quantities.UnmeasurableGeometry, match="consistently oriented"):
        quantities.measure(add_tessellated_zone("Flipped", faces=faces))


def test_zones_of_a_file_are_listed_by_name(ifc_file, add_zone):
    add_zone("Lotto B", RECTANGLE, 3.0)
    add_zone("Lotto A", RECTANGLE, 3.0)
    assert [zone.Name for zone in quantities.spatial_zones(ifc_file)] == ["Lotto A", "Lotto B"]


def test_zones_are_filtered_by_object_type(ifc_file, add_zone):
    add_zone("Lotto A", RECTANGLE, 3.0, object_type="Volume urbanistico")
    add_zone("Piano interrato", RECTANGLE, 3.0, object_type="Volume interrato")
    add_zone("Senza tipo", RECTANGLE, 3.0)

    listed = quantities.spatial_zones(ifc_file, "Volume urbanistico")
    assert [zone.Name for zone in listed] == ["Lotto A"]
    # The untyped zones are a group of their own, not a leftover.
    untyped = quantities.spatial_zones(ifc_file, quantities.NO_TYPE)
    assert [zone.Name for zone in untyped] == ["Senza tipo"]


def test_the_object_types_of_a_file_are_offered_once_each(ifc_file, add_zone):
    add_zone("Lotto A", RECTANGLE, 3.0, object_type="Volume urbanistico")
    add_zone("Lotto B", RECTANGLE, 3.0, object_type="Volume urbanistico")
    add_zone("Interrato", RECTANGLE, 3.0, object_type="Volume interrato")
    add_zone("Senza tipo", RECTANGLE, 3.0)

    assert quantities.object_types(ifc_file) == [quantities.NO_TYPE, "Volume interrato", "Volume urbanistico"]


def _measured(name, area, height, volume, coefficient=1.0):
    return quantities.Measured(None, name, area, height, volume, coefficient)


def test_totals_weight_the_mean_height_by_volume():
    rows = [_measured("A", 50.0, 3.0, 150.0), _measured("B", 16.0, 6.0, 96.0)]
    area, height, volume = quantities.totals(rows)
    assert (area, volume) == (66.0, 246.0)
    assert height == pytest.approx(246.0 / 66.0)
    # The total row multiplies back to the total volume, as every other row does.
    assert area * height == pytest.approx(volume)


def test_a_negative_coefficient_is_subtracted_from_the_totals():
    rows = [_measured("A", 50.0, 3.0, 150.0), _measured("Garage", 16.0, 6.0, 96.0, coefficient=-1.0)]
    area, _, volume = quantities.totals(rows)
    assert (area, volume) == (34.0, 54.0)


def test_a_fraction_counts_the_zone_in_part():
    rows = [_measured("Portico", 50.0, 3.0, 150.0, coefficient=0.5)]
    area, height, volume = quantities.totals(rows)
    assert (area, volume) == (25.0, 75.0)
    # Halving both keeps the mean height a geometric fact.
    assert height == pytest.approx(3.0)


def test_detracted_zones_go_to_their_own_block():
    rows = [
        _measured("Lotto A", 500.0, 9.0, 4500.0),
        _measured("Garage", 120.0, 3.0, 360.0, coefficient=-1.0),
    ]
    (gross_label, gross), (detracted_label, detracted) = quantities.sections(rows)
    assert (gross_label, detracted_label) == (quantities.ADDITIONS, quantities.DETRACTIONS)
    assert gross == [("Lotto A", 1.0, 500.0, 9.0, 4500.0)]
    # Positive in its own block: the subtraction happens once, at the total.
    assert detracted == [("Garage", -1.0, 120.0, 3.0, 360.0)]


def test_without_detractions_there_is_a_single_block():
    sections = quantities.sections([_measured("Lotto A", 500.0, 9.0, 4500.0)])
    assert len(sections) == 1
    assert sections[0][0] == quantities.TOTAL


def test_a_partial_row_shows_the_half_it_contributes_and_the_coefficient():
    """The columns hold what is totalled; the coefficient says why it is half
    of what the model measures."""
    (_, rows), = quantities.sections([_measured("Portico", 50.0, 3.0, 150.0, coefficient=0.5)])
    assert rows == [("Portico", 0.5, 25.0, 3.0, 75.0)]


def test_the_coefficient_defaults_to_one(ifc_file, add_zone):
    assert quantities.coefficient(add_zone("A", RECTANGLE, 3.0)) == 1.0


def test_the_coefficient_is_stored_in_the_quantity_set(ifc_file, add_zone):
    zone = add_zone("A", RECTANGLE, 3.0)
    quantities.write_coefficient(ifc_file, zone, -1.0)

    assert quantities.coefficient(zone) == -1.0
    assert ifcopenshell.util.element.get_pset(zone, quantities.QTO_NAME)[quantities.COEFFICIENT] == -1.0
    assert len(ifc_file.by_type("IfcElementQuantity")) == 1

    quantities.write_coefficient(ifc_file, zone, 0.5)
    assert quantities.coefficient(zone) == 0.5


def test_a_take_off_seeds_the_coefficient_at_one(ifc_file, add_zone):
    zone = add_zone("A", RECTANGLE, 3.0)
    quantities.write_qto(ifc_file, zone, 50.0, 3.0, 150.0)
    assert quantities.coefficient(zone) == 1.0


def test_a_take_off_never_writes_over_an_edited_coefficient(ifc_file, add_zone):
    """A coefficient that is not 1 is there because someone put it there."""
    zone = add_zone("A", RECTANGLE, 3.0)
    quantities.write_coefficient(ifc_file, zone, -1.0)
    quantities.write_qto(ifc_file, zone, 50.0, 3.0, 150.0)

    assert quantities.coefficient(zone) == -1.0
    assert ifcopenshell.util.element.get_pset(zone, quantities.QTO_NAME)[quantities.AREA] == 50.0


def test_the_coefficient_is_a_real_number_on_ifc4x3(add_zone):
    """IFC4X3 turned CountValue into an integer: a halved zone needs the real
    valued quantity that schema added."""
    ifc_file = conftest.build_file("IFC4X3")
    zone = ifc_file.createIfcSpatialZone(ifcopenshell.guid.new(), None, "A")
    quantities.write_coefficient(ifc_file, zone, 0.5)

    assert quantities.coefficient(zone) == 0.5
    assert ifc_file.by_type("IfcPhysicalSimpleQuantity")[0].is_a() == "IfcQuantityNumber"


def test_headers_carry_the_project_units(ifc_file):
    assert quantities.headers(ifc_file) == [
        "Name",
        "Coefficiente",  # a pure number: no unit to carry
        "Superficie lorda (m²)",
        "Altezza media (m)",
        "Volume urbanistico (m³)",
    ]


def test_qto_quantities_keep_their_measure(ifc_file, add_zone):
    zone = add_zone("A", RECTANGLE, 3.0)
    quantities.write_qto(ifc_file, zone, 50.0, 3.0, 150.0)

    qto = ifcopenshell.util.element.get_pset(zone, quantities.QTO_NAME)
    assert qto[quantities.AREA] == 50.0
    assert qto[quantities.VOLUME] == 150.0
    written = {q.Name: q.is_a() for q in ifc_file.by_type("IfcPhysicalSimpleQuantity")}
    assert written == {
        quantities.AREA: "IfcQuantityArea",
        quantities.HEIGHT: "IfcQuantityLength",
        quantities.VOLUME: "IfcQuantityVolume",
        # IFC4 has no real valued dimensionless quantity: count is the one
        # whose value is still a number there.
        quantities.COEFFICIENT: "IfcQuantityCount",
    }


def test_quantifying_twice_does_not_duplicate_the_qto(ifc_file, add_zone):
    zone = add_zone("A", RECTANGLE, 3.0)
    quantities.write_qto(ifc_file, zone, 50.0, 3.0, 150.0)
    quantities.write_qto(ifc_file, zone, 60.0, 4.0, 240.0)

    assert len(ifc_file.by_type("IfcElementQuantity")) == 1
    assert ifcopenshell.util.element.get_pset(zone, quantities.QTO_NAME)[quantities.AREA] == 60.0
