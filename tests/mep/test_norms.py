# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import pytest

from mep.core.norms import load_en12056, load_en1401


def test_en12056_tables_load():
    tables = load_en12056()
    assert tables.apparecchi["lavabo"].du["I"] == 0.5
    assert tables.apparecchi["wc_cassetta_6.0l"].du["I"] == 2.0
    assert tables.apparecchi["wc_cassetta_4.0l"].du["I"] is None  # non ammesso
    assert tables.k == {
        "intermittente": 0.5, "frequente": 0.7, "molto_frequente": 1.0, "speciale": 1.2
    }
    assert tables.diametri_interni_minimi[100] == 96
    assert tables.diramazioni_limiti["sistema_I"]["pendenza_min_percento"] == 1.0
    assert [row.dn for row in tables.colonne_primaria] == [60, 70, 80, 90, 100, 125, 150, 200]
    # prospetto B.1: 10 slopes x 7 DNs, both filling degrees
    assert len(tables.collettori[0.5]) == 70
    assert len(tables.collettori[0.7]) == 70


def test_en12056_citations():
    tables = load_en12056()
    ref = tables.apparecchi["lavabo"].ref
    assert ref.norma == "UNI EN 12056-2:2001"
    assert ref.riferimento == "prospetto 2"
    assert ref.as_dict("DU lavabo") == {
        "norma": "UNI EN 12056-2:2001", "voce": "prospetto 2", "per": "DU lavabo"
    }


def test_en1401_dimensions_load():
    diametri = load_en1401()
    by_dn = {d.dn_od: d for d in diametri}
    assert by_dn[160].e_min_mm == {"SN4": 4.0, "SN8": 4.7}
    assert by_dn[160].de_max_mm == 160.4
    assert by_dn[110].ref.norma == "UNI EN 1401-1:2019+A1:2023"
