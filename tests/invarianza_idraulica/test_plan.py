import shapely

from invarianza_idraulica.core import plan, surfaces


def measurement(rows):
    boundary = shapely.box(0, 0, 20, 10)
    total = boundary.area
    impermeable = sum(row.area * row.coefficient for row in rows)
    return surfaces.Measurement(boundary, total, rows, impermeable, impermeable / total, [], [])


def row(label, coefficient, geometry):
    return surfaces.Row(label, coefficient, "materiale", geometry.area, geometry)


def test_the_tiff_is_written_at_the_declared_resolution(tmp_path):
    from PIL import Image

    path = tmp_path / "planimetria.tif"
    measured = measurement([row("Asfalto", 0.9, shapely.box(0, 0, 20, 10))])
    assert plan.render(measured, str(path), "Ambito di intervento") is True
    with Image.open(path) as image:
        assert image.info.get("dpi") == (plan.DPI, plan.DPI)
        assert image.mode == "RGB"


def test_rows_sharing_a_coefficient_get_different_hatches():
    rows = [
        row("Ghiaia", 0.6, shapely.box(0, 0, 10, 10)),
        row("Grigliato", 0.6, shapely.box(10, 0, 20, 10)),
    ]
    assigned = plan.hatches(rows)
    assert assigned[0] != assigned[1]


def test_a_coefficient_used_once_is_left_plain():
    rows = [row("Asfalto", 0.9, shapely.box(0, 0, 20, 10))]
    assert plan.hatches(rows) == {0: ""}


def test_the_scale_bar_is_a_round_length_about_a_fifth_of_the_lot():
    assert plan.scale_length(60.0) == 20.0
    assert plan.scale_length(24.0) == 5.0
    assert plan.scale_length(900.0) == 200.0


def test_the_drawing_reserves_room_below_the_lot_for_the_scale_bar(tmp_path):
    """La barra sta sotto il perimetro, e senza banda riservata gli assi la
    tagliavano via: qui si controlla che l'inchiostro arrivi sotto il lotto."""
    from PIL import Image

    path = tmp_path / "planimetria.tif"
    measured = measurement([row("Asfalto", 0.9, shapely.box(0, 0, 20, 10))])
    plan.render(measured, str(path), "Ambito")
    with Image.open(path) as image:
        band = image.convert("L").crop((0, int(image.height * 0.80), image.width, image.height))
    assert min(band.getextrema()) < 64


def test_without_matplotlib_the_table_still_stands(tmp_path, monkeypatch):
    """Il disegno è un di più: se manca, render lo dice e non solleva."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("no matplotlib")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    measured = measurement([row("Asfalto", 0.9, shapely.box(0, 0, 20, 10))])
    assert plan.render(measured, str(tmp_path / "x.tif"), "T") is False
