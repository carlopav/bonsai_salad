# Bonsai Salad — mep tool tests
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

import pytest

import ifcopenshell.api as api

from mep.core import appliance_of, derive_appliance


@pytest.fixture()
def ifc():
    f = api.run("project.create_file", version="IFC4")
    api.run("root.create_entity", f, ifc_class="IfcProject", name="t")
    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    return f


def _terminal(f, predefined):
    t = api.run("root.create_entity", f, ifc_class="IfcSanitaryTerminalType",
                name=predefined, predefined_type=predefined)
    e = api.run("root.create_entity", f, ifc_class="IfcSanitaryTerminal")
    api.run("type.assign_type", f, related_objects=[e], relating_type=t)
    return t, e


def test_derive_from_predefined_type(ifc):
    _, lavabo = _terminal(ifc, "WASHHANDBASIN")
    assert derive_appliance(lavabo) == "lavabo"
    _, doccia = _terminal(ifc, "SHOWER")
    assert derive_appliance(doccia) == "doccia_senza_tappo"


def test_wc_volume_from_cistern_pset(ifc):
    t, wc = _terminal(ifc, "TOILETPAN")
    assert derive_appliance(wc) == "wc_cassetta_6.0l"  # default volume
    pset = api.run("pset.add_pset", ifc, product=t,
                   name="Pset_SanitaryTerminalTypeCistern")
    api.run("pset.edit_pset", ifc, pset=pset,
            properties={"CisternCapacity": 0.009})  # 9 l in m3
    assert derive_appliance(wc) == "wc_cassetta_9.0l"


def test_custom_pset_overrides_standard_data(ifc):
    t, doccia = _terminal(ifc, "SHOWER")
    pset = api.run("pset.add_pset", ifc, product=t, name="EN12056_Utilizzatore")
    api.run("pset.edit_pset", ifc, pset=pset,
            properties={"Apparecchio": "doccia_con_tappo"})
    assert appliance_of(doccia) == "doccia_con_tappo"


def test_untyped_or_unknown_returns_none(ifc):
    e = api.run("root.create_entity", ifc, ifc_class="IfcSanitaryTerminal")
    assert derive_appliance(e) is None
    assert appliance_of(e) is None
