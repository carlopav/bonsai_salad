"""
Integration tests for ifc_dxf export pipeline.

Each IFC file in tests/ifc_dxf/files/ is a self-contained test fixture
covering a specific scenario.  The parametrized test exports every drawing
found in the file and runs basic sanity checks.  Case-specific assertions
are added as needed for each fixture.

Run with:
    python -m pytest tests/ifc_dxf/test_export.py -v

Requires: pytest, ifcopenshell, ezdxf, shapely (optional but recommended).
"""

import os
import sys
import pytest
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.guid
import ezdxf

# conftest.py at repo root registered bpy/ifc_dxf stubs before any package
# __init__.py was imported, so normal ifc_dxf.core imports work here.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FILES_DIR = os.path.join(os.path.dirname(__file__), "files")
_OUT_DIR = os.path.join(_REPO_ROOT, "output", "test_runs")
_TEMPLATE_PATH = os.path.join(_REPO_ROOT, "ifc_dxf", "templates", "ifc_dxf_template_metric.dxf")

from ifc_dxf.core.ifc_query import find_drawings
# Import from the concrete subpackage, not the package root: the test harness
# (root conftest.py) stubs `ifc_dxf.core` as an empty namespace and never runs
# its real __init__.py, so the `export_drawing` alias defined there is absent.
from ifc_dxf.core.approximate import export_drawing
from ifc_dxf.core.accurate import export_drawing as export_drawing_accurate
from ifc_dxf.core.accurate import pipeline as accurate_pipeline
from ifc_dxf.core import layers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ifc_files():
    """Collect all .ifc fixtures in tests/ifc_dxf/files/."""
    if not os.path.isdir(_FILES_DIR):
        return []
    return sorted(
        f for f in os.listdir(_FILES_DIR) if f.lower().endswith(".ifc")
    )


def _output_path(ifc_name, drawing_name):
    os.makedirs(_OUT_DIR, exist_ok=True)
    safe = drawing_name.replace(" ", "_").replace("/", "-")
    stem = os.path.splitext(ifc_name)[0]
    return os.path.join(_OUT_DIR, f"{stem}__{safe}.dxf")


def _build_has_edge_classification():
    """True when this ifcopenshell build knows the #3668 SVG settings."""
    settings = ifcopenshell.geom.settings()
    try:
        settings.set("svg-use-edge-classification", True)
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Parametrized smoke test: export every drawing in every IFC fixture
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ifc_filename", _ifc_files())
def test_export_all_drawings(ifc_filename, tmp_path):
    """Export every drawing in the IFC fixture and verify the output DXF."""
    ifc_path = os.path.join(_FILES_DIR, ifc_filename)
    ifc = ifcopenshell.open(ifc_path)

    drawings = find_drawings(ifc)
    assert drawings, f"No drawings found in {ifc_filename}"

    for drawing, pset in drawings:
        out = _output_path(ifc_filename, drawing.Name or "unnamed")
        export_drawing(ifc, drawing, pset, out, wall_mode="shapely")

        assert os.path.isfile(out), f"Output DXF not created for {drawing.Name}"
        assert os.path.getsize(out) > 1000, f"Output DXF suspiciously small for {drawing.Name}"

        doc = ezdxf.readfile(out)
        msp = doc.modelspace()
        assert len(list(msp)) > 0, f"Model space is empty for {drawing.Name}"


@pytest.mark.parametrize("ifc_filename", _ifc_files())
def test_export_all_drawings_accurate(ifc_filename, tmp_path):
    """Same smoke test through the accurate (global HLR oracle) pipeline."""
    ifc_path = os.path.join(_FILES_DIR, ifc_filename)
    ifc = ifcopenshell.open(ifc_path)

    drawings = find_drawings(ifc)
    assert drawings, f"No drawings found in {ifc_filename}"

    for drawing, pset in drawings:
        out = _output_path(ifc_filename, (drawing.Name or "unnamed") + "_accurate")
        export_drawing_accurate(ifc, drawing, pset, out)

        assert os.path.isfile(out), f"Output DXF not created for {drawing.Name}"
        assert os.path.getsize(out) > 1000, f"Output DXF suspiciously small for {drawing.Name}"

        doc = ezdxf.readfile(out)
        assert len(list(doc.modelspace())) > 0, f"Model space is empty for {drawing.Name}"


# ---------------------------------------------------------------------------
# Case-specific assertions
# ---------------------------------------------------------------------------

class TestEdgeClassification:
    """SVG edge classification (IfcOpenShell PR #8608, issue #3668).

    The serializer writes the class on each <path>, so the pure parsing side
    and the crease-mask bypass are testable without a classifying build.
    """

    IFC = "test_ifc_01.ifc"

    SVG = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:ifc="http://www.ifcopenshell.org/ns">'
        '<g class="projection IfcWall" ifc:guid="0aaa">'
        '<path d="M0,0 L1000,0" class="sharp"/>'
        '<path d="M0,0 L0,1000 M0,1000 L1000,1000" class="crease"/>'
        '<path d="M2000,0 L3000,0"/>'
        '</g></svg>'
    )

    def _drawing(self):
        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, self.IFC))
        drawing, pset = find_drawings(ifc)[0]
        return ifc, drawing, pset

    def test_path_class_reaches_line_records(self):
        """The class lives on the <path>, not the <g>, and applies to each of
        its subpaths; an unclassified path yields None."""
        out = accurate_pipeline._parse_hlr_svg(self.SVG, 0.0, 0.0, 1000.0)
        lines = out["0aaa"]["lines"]
        assert [cls for _pts, cls in lines] == ["sharp", "crease", "crease", None]
        assert lines[0][0] == [(0.0, 0.0), (1.0, 0.0)]

    def test_lines_group_by_class(self):
        out = accurate_pipeline._parse_hlr_svg(self.SVG, 0.0, 0.0, 1000.0)
        groups = accurate_pipeline._group_lines_by_class(out["0aaa"]["lines"])
        assert sorted(groups, key=str) == [None, "crease", "sharp"]
        assert len(groups["crease"]) == 2
        assert accurate_pipeline._count_edge_classes(out) == {
            "crease": 2, "sharp": 1, "unclassified": 1}

    def test_settings_come_from_the_drawing_pset(self):
        """Same source as Bonsai: EPset_Drawing, with its property defaults."""
        assert accurate_pipeline._edge_classification_settings({}) == {
            "svg-use-edge-classification": False,
            "svg-render-crease-edges": True,
            "svg-valley-angle-min-degrees": 12.0,
            "svg-render-sharp-edges": True,
            "svg-ridge-angle-min-degrees": 45.0,
            "svg-emit-flush-edges": False,
        }
        values = accurate_pipeline._edge_classification_settings(
            {"UseEdgeClassification": True, "RenderFlush": True,
             "ValleyAngleMinDegrees": 20})
        assert values["svg-use-edge-classification"] is True
        assert values["svg-emit-flush-edges"] is True
        assert values["svg-valley-angle-min-degrees"] == 20.0
        assert values["svg-ridge-angle-min-degrees"] == 45.0  # untouched default

    def test_setup_serialiser_reports_availability(self):
        """With the drawing opted in, the flag mirrors what this ifcopenshell
        build accepts; on older builds gs.set() raises and the pipeline falls
        back to the crease mask."""
        ifc, drawing, pset = self._drawing()
        settings = accurate_pipeline._edge_classification_settings(
            {"UseEdgeClassification": True})
        _gs, _buf, _ser, flag = accurate_pipeline._setup_serialiser(
            ifc, drawing, 0.01, pset.get("TargetView", "PLAN_VIEW"), settings)
        assert flag is _build_has_edge_classification()

    def test_setup_serialiser_opt_out(self):
        """A drawing without UseEdgeClassification keeps the old linework."""
        ifc, drawing, pset = self._drawing()
        _gs, _buf, _ser, flag = accurate_pipeline._setup_serialiser(
            ifc, drawing, 0.01, pset.get("TargetView", "PLAN_VIEW"),
            accurate_pipeline._edge_classification_settings({}))
        assert flag is False

    def test_no_second_geometry_pass(self):
        """The serializer owns the linework filtering; the pipeline must not
        re-derive it from a second geometry pass (removed ago 2026)."""
        assert not hasattr(accurate_pipeline, "_build_keep_edge_masks")
        assert not hasattr(accurate_pipeline, "_filter_lines_by_mask")

    @pytest.mark.parametrize("flush", [True, False])
    def test_render_flush_is_the_drawings_call(self, flush, tmp_path):
        """RenderFlush is honoured, not second-guessed: either way the export
        goes through and draws whatever the serializer emitted."""
        ifc, drawing, pset = self._drawing()
        out = str(tmp_path / f"flush_{flush}.dxf")
        accurate_pipeline.export_drawing(
            ifc, drawing, dict(pset, RenderFlush=flush), out)
        assert len(list(ezdxf.readfile(out).modelspace())) > 0


class TestLayersAndRoles:
    """Layer = IFC class, entity style = drawing role (core/layers.py)."""

    IFC = "test_ifc_01.ifc"
    _SUFFIXES = ("_Section", "_View", "_Overhead", "_Hatches")

    def _export(self, tmp_path, accurate):
        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, self.IFC))
        drawing, pset = find_drawings(ifc)[0]
        out = str(tmp_path / ("acc.dxf" if accurate else "app.dxf"))
        tpl = _TEMPLATE_PATH if os.path.isfile(_TEMPLATE_PATH) else None
        if accurate:
            export_drawing_accurate(ifc, drawing, pset, out, template_path=tpl)
        else:
            export_drawing(ifc, drawing, pset, out, wall_mode="shapely",
                           template_path=tpl)
        return ezdxf.readfile(out)

    def test_template_declares_classes_only(self):
        """The template carries one layer per IFC class -- no role suffixes."""
        doc = ezdxf.readfile(_TEMPLATE_PATH)
        names = [l.dxf.name for l in doc.layers]
        assert not [n for n in names if n.endswith(self._SUFFIXES)]
        assert "IfcWall" in names and "IfcSlab" in names
        # Deprecated classes are left to runtime creation.
        assert "IfcWallStandardCase" not in names

    def test_apply_role_writes_the_style(self):
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for role, (color, linetype, lineweight) in layers.ROLE_STYLES.items():
            e = msp.add_line((0, 0), (1, 0))
            layers.apply_role(e, role)
            assert (e.dxf.color, e.dxf.linetype, e.dxf.lineweight) == (
                color, linetype, lineweight), role

    def test_ensure_layer_creates_but_never_restyles(self):
        doc = ezdxf.new("R2010")
        existing = doc.layers.add("IfcWall_Hatches")
        existing.color = 42
        layers.ensure_layer(doc, "IfcWall_Hatches", "hatch")
        assert doc.layers.get("IfcWall_Hatches").color == 42, "restyled a declared layer"

        layers.ensure_layer(doc, "IfcRoof_Hatches", "hatch")
        created = doc.layers.get("IfcRoof_Hatches")
        assert created.color == 254 and created.dxf.lineweight == 9

        # A class the template leaves out (deprecated, or simply unforeseen).
        layers.ensure_layer(doc, "IfcWallStandardCase")
        assert doc.layers.get("IfcWallStandardCase").dxf.lineweight == 9

    @pytest.mark.parametrize("accurate", [True, False])
    def test_every_used_layer_is_declared(self, accurate, tmp_path):
        """The regression this whole scheme exists for: an entity on a layer
        the document never declares gets CAD defaults, silently."""
        doc = self._export(tmp_path, accurate)
        declared = {l.dxf.name for l in doc.layers}
        used = {e.dxf.layer for e in doc.modelspace()}
        for block in doc.blocks:
            used |= {e.dxf.layer for e in block}
        assert used - declared == set()

    @pytest.mark.parametrize("accurate", [True, False])
    def test_roles_reach_the_entities(self, accurate, tmp_path):
        """Cut walls print thick, everything viewed thin -- on the class layer."""
        doc = self._export(tmp_path, accurate)
        walls = [e for e in doc.modelspace() if e.dxf.layer == "IfcWall"]
        assert walls, "no wall geometry on the IfcWall layer"
        section = layers.ROLE_STYLES["section"]
        cut = [e for e in walls
               if (e.dxf.color, e.dxf.linetype, e.dxf.lineweight) == section]
        assert cut, "cut walls carry no section style"

    def test_view_linework_is_not_buried_under_a_hatched_cut(self, tmp_path):
        """A cut wall reports both its section loop and a projection of the
        same prism; the section is drawn fused and hatched, so the duplicate
        must not survive underneath it."""
        import shapely, shapely.ops
        doc = self._export(tmp_path, accurate=True)
        section = layers.ROLE_STYLES["section"]
        view = layers.ROLE_STYLES["view"]
        cut_areas = []
        for e in doc.modelspace():
            if e.dxftype() != "LWPOLYLINE" or not e.closed:
                continue
            if (e.dxf.color, e.dxf.linetype, e.dxf.lineweight) != section:
                continue
            pts = [(p[0], p[1]) for p in e.get_points()]
            if len(pts) >= 3:
                poly = shapely.Polygon(pts)
                cut_areas.append(poly if poly.is_valid else poly.buffer(0))
        assert cut_areas, "no hatched cut in this drawing"
        buried = shapely.ops.unary_union(cut_areas)
        for e in doc.modelspace():
            if e.dxftype() != "LWPOLYLINE":
                continue
            if (e.dxf.color, e.dxf.linetype, e.dxf.lineweight) != view:
                continue
            pts = [(p[0], p[1]) for p in e.get_points()]
            if len(pts) > 1:
                assert not buried.covers(shapely.LineString(pts)), (
                    f"view linework buried under the cut on {e.dxf.layer}")

    def test_blocks_are_bylayer(self, tmp_path):
        """A BLOCK reads everything from its layer: the INSERT carries no
        explicit colour/linetype/lineweight, its content stays BYBLOCK."""
        doc = self._export(tmp_path, accurate=False)
        inserts = [e for e in doc.modelspace() if e.dxftype() == "INSERT"]
        assert inserts
        for ins in inserts:
            assert ins.dxf.color == 256, "INSERT carries an explicit colour"
            assert ins.dxf.linetype == "BYLAYER"
            assert ins.dxf.lineweight == -1  # BYLAYER
        # Only the blocks we generate; the template ships its own (cartiglio,
        # markers) and those keep whatever BricsCAD authored.
        for name in {ins.dxf.name for ins in inserts}:
            for e in doc.blocks.get(name):
                if e.dxftype() in ("LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE"):
                    assert e.dxf.color == 0, f"block {name} content is not BYBLOCK"

    def test_overhead_symbol_lives_on_its_own_layer(self, tmp_path):
        """A window filling an opening above the cut plane draws dashed -- via
        the layer, since its INSERT cannot be styled."""
        doc = self._export(tmp_path, accurate=False)
        color, linetype, lineweight = layers.ROLE_STYLES["overhead"]
        overhead = [e for e in doc.modelspace()
                    if e.dxf.layer.endswith("_Overhead")]
        assert overhead, "no overhead entity found"
        for e in overhead:
            layer = doc.layers.get(e.dxf.layer)
            assert (layer.color, layer.dxf.linetype, layer.dxf.lineweight) == (
                color, linetype, lineweight)


class TestMaterialFusionKey:
    """Grouping walls for fusion by "material" must mean the whole assignment.

    Real case (2T-Fontane): WAL390-Divisorio Garage and WAL330-Muri esterni
    garage are different wall types with different layer stacks, but both start
    with 'Controparete in cartongesso' — keyed by the first material they fused
    into one outline, while walls of the same type mirrored would not.
    """

    def _layered_wall(self, ifc, first, second):
        layers_ = [ifc.createIfcMaterialLayer(ifc.createIfcMaterial(name), t, None)
                   for name, t in ((first, 0.02), (second, 0.2))]
        layer_set = ifc.createIfcMaterialLayerSet(layers_, None)
        usage = ifc.createIfcMaterialLayerSetUsage(layer_set, "AXIS2", "POSITIVE", 0.0)
        wall = ifc.createIfcWall(ifcopenshell.guid.new(), None, "Wall")
        ifc.createIfcRelAssociatesMaterial(
            ifcopenshell.guid.new(), None, None, None, [wall], usage)
        return wall

    def test_same_first_layer_different_stack_does_not_fuse(self):
        from ifc_dxf.core.ifc_query import get_material_key, get_material_name

        ifc = ifcopenshell.file(schema="IFC4")
        a = self._layered_wall(ifc, "Controparete in cartongesso", "Calcestruzzo armato")
        b = self._layered_wall(ifc, "Controparete in cartongesso", "Laterizio")

        assert get_material_name(a) == get_material_name(b), "premise: same first layer"
        assert get_material_key(a) != get_material_key(b), (
            "different layer stacks must not share a fusion group")

    def test_single_material_keys_by_name(self):
        from ifc_dxf.core.ifc_query import get_material_key

        ifc = ifcopenshell.file(schema="IFC4")
        wall = ifc.createIfcWall(ifcopenshell.guid.new(), None, "Wall")
        ifc.createIfcRelAssociatesMaterial(
            ifcopenshell.guid.new(), None, None, None, [wall],
            ifc.createIfcMaterial("Calcestruzzo armato"))
        assert get_material_key(wall) == "Calcestruzzo armato"


class TestSpatialZones:
    """test_ifc_02_spatial_zone.ifc — the same plan with an IfcSpatialZone
    enclosing the room.

    Zones take no part in the visibility pass: they are outlined from their
    authored footprint on a no-plot layer, and never occlude. What these tests
    lock is that handling. They do *not* reproduce the occlusion that motivated
    it: written into the HLR scene, a real project's zones hid 59 of 84
    furniture and all 13 sanitary terminals (2T-Fontane, drawing
    0GBxUdA8nE5BbFy9k81SHQ), but this synthetic zone does not occlude, so the
    trigger is some property of those zones that is not reproduced here.
    """

    IFC = "test_ifc_02_spatial_zone.ifc"

    def _export(self, tmp_path):
        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, self.IFC))
        drawing, pset = find_drawings(ifc)[0]
        out = str(tmp_path / "zone.dxf")
        tpl = _TEMPLATE_PATH if os.path.isfile(_TEMPLATE_PATH) else None
        export_drawing_accurate(ifc, drawing, pset, out, template_path=tpl)
        return ezdxf.readfile(out)

    def test_zone_contents_are_drawn(self, tmp_path):
        doc = self._export(tmp_path)
        layers_used = {e.dxf.layer for e in doc.modelspace()}
        assert "IfcFurniture" in layers_used, "the zone swallowed the furniture"
        assert "IfcSanitaryTerminal" in layers_used

    def test_zone_stays_out_of_the_hlr_pass(self, tmp_path):
        """The guarantee that makes the occlusion impossible in the first
        place: no zone is ever written to the serializer."""
        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, self.IFC))
        drawing, pset = find_drawings(ifc)[0]
        zones = [e for e in ifc.by_type("IfcSpatialZone")]
        assert zones, "fixture carries no zone"
        assert all(accurate_pipeline._is_space_volume(z) for z in zones)
        out, _n, _t, _f = accurate_pipeline._run_hlr_scene(
            ifc, drawing, zones, 0.01, "PLAN_VIEW",
            accurate_pipeline._edge_classification_settings(pset))
        assert not [z for z in zones if z.GlobalId in out]

    def test_zone_is_outlined_on_a_noplot_layer(self, tmp_path):
        doc = self._export(tmp_path)
        outline = [e for e in doc.modelspace() if e.dxf.layer == "IfcSpatialZone"]
        assert outline, "the zone has no boundary polyline"
        assert all(e.dxftype() == "LWPOLYLINE" for e in outline)
        assert doc.layers.get("IfcSpatialZone").dxf.plot == 0


class TestAnnotativeText:
    """test_ifc_01.ifc with its TEXT annotation turned 90 degrees.

    An annotative TEXT is drawn from its context data, not from the entity, so
    the two must agree -- and the CAD app must have nothing left to resize it
    with when the text is edited.
    """

    IFC = "test_ifc_01.ifc"

    def _export(self, tmp_path, rotate=False):
        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, self.IFC))
        ann = next(a for a in ifc.by_type("IfcAnnotation") if a.ObjectType == "TEXT")
        if rotate:
            placement = ann.ObjectPlacement.RelativePlacement
            placement.RefDirection = ifc.createIfcDirection((0.0, 1.0, 0.0))
        drawing, pset = find_drawings(ifc)[0]
        out = str(tmp_path / "text.dxf")
        tpl = _TEMPLATE_PATH if os.path.isfile(_TEMPLATE_PATH) else None
        export_drawing(ifc, drawing, pset, out, wall_mode="shapely", template_path=tpl)
        return ezdxf.readfile(out)

    @staticmethod
    def _annotative_texts(doc):
        """[(TEXT entity, {group code: value} of its *A1 context data)]."""
        found = []
        for e in doc.modelspace().query("TEXT"):
            if not e.has_extension_dict:
                continue
            d = e.get_extension_dict().dictionary
            if "AcDbContextDataManager" not in d:
                continue
            scales = d["AcDbContextDataManager"]["ACDB_ANNOTATIONSCALES"]
            for key in scales.keys():
                ctx = scales.get(key)
                tags = {t.code: t.value
                        for sub in ctx.xtags.subclasses for t in sub}
                found.append((e, tags))
        return found

    def test_context_data_repeats_the_entity_rotation(self, tmp_path):
        """Group 50 is in degrees here like everywhere else in DXF.

        Written in radians, a text turned 90 degrees came out at 1.57 degrees --
        visually unrotated, since BricsCAD draws the context data.
        """
        doc = self._export(tmp_path, rotate=True)
        texts = self._annotative_texts(doc)
        assert texts, "no annotative TEXT in the export"
        for entity, tags in texts:
            assert abs(entity.dxf.rotation - 90.0) < 1e-3, \
                "the annotation's own 90 degree turn was lost"
            assert abs(tags[50] - entity.dxf.rotation) < 1e-6, \
                f"context data rotation {tags[50]} != entity {entity.dxf.rotation}"

    def test_fixed_height_text_styles_are_annotative(self, tmp_path):
        """The styles hold the paper height (small = 1.8 mm), which the CAD only
        multiplies by the annotation scale for an *annotative* style. Plain, that
        fixed height overrides the entity height and DIMTXT, and every text and
        dimension collapses to paper size as soon as it is edited."""
        doc = self._export(tmp_path)
        checked = 0
        for style in doc.styles:
            if not style.dxf.get("height", 0.0):
                continue
            assert style.has_extension_dict, \
                f"fixed-height style {style.dxf.name} is not annotative"
            xrec = style.get_extension_dict().dictionary.get("AcadAnnotative")
            assert xrec is not None, \
                f"fixed-height style {style.dxf.name} lacks AcadAnnotative"
            assert [t.value for t in xrec.tags if t.code == 1070] == [1, 1]
            checked += 1
        assert checked, "no fixed-height text style in the export"

    def test_text_height_matches_its_style_at_the_drawing_scale(self, tmp_path):
        """Entity height (model) == style height (paper) / scale factor: the two
        must agree, or an annotative regen resizes the text."""
        doc = self._export(tmp_path)
        scale = 0.01  # the fixture's drawing is 1:100
        for entity, _tags in self._annotative_texts(doc):
            paper = doc.styles.get(entity.dxf.style).dxf.get("height", 0.0)
            if not paper:
                continue
            assert abs(entity.dxf.height - paper / scale) < 1e-9, \
                f"{entity.dxf.text!r}: {entity.dxf.height} != {paper}/{scale}"


# Tag sequence of the scale representation of an annotative aligned dimension,
# transcribed from a DXF written by BricsCAD itself. None = value carried over
# from the exported entity (handles, block name, points), asserted separately.
# Order and group codes are the contract: BricsCAD reads this object positionally
# and silently ignores one it does not recognise, which is how the dimensions
# ended up flagged annotative with an empty annotation scale field.
_BRICSCAD_DIM_CONTEXT_TAGS = [
    (100, "AcDbObjectContextData"), (70, 3), (290, 1),
    (100, "AcDbAnnotScaleObjectContextData"), (340, None),
    (100, "AcDbDimensionObjectContextData"),
    (2, None),                    # this scale's picture block
    (293, 0),
    (10, None),                   # text midpoint
    (294, 1), (140, 0.0), (298, 0), (291, 0),
    (70, 0), (292, 0), (71, 0), (280, 0), (295, 0), (296, 0), (297, 0),
    (100, "AcDbAlignedDimensionObjectContextData"),
    (11, None),                   # dimension line defpoint
]

_ANNOTATIVE_DIM_EXPORTS = {}


def _annotative_dim_export(scale_name):
    """Export the basic-plan fixture at scale_name, once per session."""
    if scale_name not in _ANNOTATIVE_DIM_EXPORTS:
        denom = int(scale_name.split(":")[1])
        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, "test_ifc_01.ifc"))
        drawing, pset = find_drawings(ifc)[0]
        pset = {**pset, "Scale": f"1/{denom}", "HumanScale": scale_name}
        out = _output_path("test_ifc_01.ifc", f"annotative_dim_1_{denom}")
        tpl = _TEMPLATE_PATH if os.path.isfile(_TEMPLATE_PATH) else None
        export_drawing(ifc, drawing, pset, out, wall_mode="shapely", template_path=tpl)
        _ANNOTATIVE_DIM_EXPORTS[scale_name] = ezdxf.readfile(out)
    return _ANNOTATIVE_DIM_EXPORTS[scale_name]


class TestAnnotativeDimensions:
    """test_ifc_01.ifc exported at several scales, none of them its own 1:100.

    A DIMENSION is annotative only if the CAD can read the scale representation
    hanging off it: BricsCAD then shows e.g. "1:200" in the entity's annotative
    scale field and redraws it at that scale. When it cannot, the field stays
    empty and an edit redraws the dimension at paper size -- 200x too small.

    What the CAD needs is not obvious from the DXF reference, so these tests
    lock the export against a dimension BricsCAD wrote itself.
    """

    @pytest.fixture(autouse=True, params=["1:50", "1:100", "1:200"])
    def export(self, request):
        self.scale = request.param
        self.doc = _annotative_dim_export(self.scale)
        self.dims = list(self.doc.modelspace().query("DIMENSION"))
        assert self.dims, "no DIMENSION in the export"

    @staticmethod
    def _context_data(dim):
        """The dimension's *A1 scale representation object."""
        d = dim.get_extension_dict().dictionary
        scales = d["AcDbContextDataManager"]["ACDB_ANNOTATIONSCALES"]
        return scales.get("*A1")

    def test_dimension_style_is_annotative(self):
        """DIMSCALE 0 alone is not enough: BricsCAD writes the AnnotativeData
        payload on the style twice, as an extension-dict XRECORD and as XDATA."""
        for name in {d.dxf.dimstyle for d in self.dims}:
            style = self.doc.dimstyles.get(name)
            assert style.dxf.get("dimscale", 1.0) == 0.0, \
                f"dimstyle {name} has a fixed DIMSCALE"
            assert style.has_extension_dict, f"dimstyle {name} is not annotative"
            xrec = style.get_extension_dict().dictionary.get("AcadAnnotative")
            assert xrec is not None, f"dimstyle {name} lacks the AcadAnnotative XRECORD"
            assert [t.value for t in xrec.tags if t.code == 1070] == [1, 1]
            xdata = style.get_xdata("AcadAnnotative")
            assert [t.value for t in xdata if t.code == 1070] == [1, 1], \
                f"dimstyle {name} lacks the AcadAnnotative XDATA"

    def test_context_data_class_is_declared(self):
        """An object of an undeclared class is unknown to the CAD, which drops
        it -- leaving the dimension flagged annotative with nothing behind it."""
        for dim in self.dims:
            ctx = self._context_data(dim)
            assert ctx.dxftype() == "ACDB_ALDIMOBJECTCONTEXTDATA_CLASS", \
                f"context data written as {ctx.dxftype()}"
            self.doc.classes.get(ctx.dxftype())  # raises if not in CLASSES

    def test_dimension_is_written_as_an_aligned_dimension(self):
        """The context data class describes an aligned dimension (dimtype 1);
        ezdxf's default is the rotated variant (dimtype 0), whose context data
        is a different, undeclared class."""
        for dim in self.dims:
            assert dim.dxf.dimtype & 15 == 1, \
                f"dimtype {dim.dxf.dimtype & 15} is not an aligned dimension"

    def test_context_data_repeats_the_entity_points(self):
        """Group 10 of the context data is the *text* midpoint and group 11 of
        the aligned subclass the dimension-line defpoint -- the entity's two
        points the other way round. Swapped, BricsCAD draws the dimension from
        nonsense and the scale field stays empty."""
        for dim in self.dims:
            tags = [(t.code, t.value)
                    for sub in self._context_data(dim).xtags.subclasses for t in sub]
            values = dict(tags)
            assert values[2] == dim.dxf.geometry, "context data names another block"
            text_pt = ezdxf.math.Vec2(values[10])
            dim_pt = ezdxf.math.Vec2(values[11])
            assert text_pt.isclose(ezdxf.math.Vec2(dim.dxf.text_midpoint)), \
                f"context data text point {text_pt} != {dim.dxf.text_midpoint}"
            assert dim_pt.isclose(ezdxf.math.Vec2(dim.dxf.defpoint)), \
                f"context data dimension point {dim_pt} != {dim.dxf.defpoint}"

    def test_context_data_points_at_the_current_annotation_scale(self):
        """The 340 handle must resolve to the SCALE named by CANNOSCALE, or the
        annotative scale field comes out empty whatever the drawing scale."""
        scale_list = self.doc.rootdict["ACAD_SCALELIST"]
        wanted = {scale_list.get(k).dxf.handle: k for k in scale_list.keys()}
        for dim in self.dims:
            assert dim.has_extension_dict, "DIMENSION carries no context data"
            d = dim.get_extension_dict().dictionary
            scales = d["AcDbContextDataManager"]["ACDB_ANNOTATIONSCALES"]
            for key in scales.keys():
                ctx = scales.get(key)
                handles = [t.value for sub in ctx.xtags.subclasses
                           for t in sub if t.code == 340]
                assert handles, "context data references no SCALE"
                assert wanted.get(handles[0]) == self.scale, \
                    f"context data points at {wanted.get(handles[0])!r}, not {self.scale}"

    def test_context_data_matches_the_bricscad_tag_sequence(self):
        """Group codes, order and constants exactly as BricsCAD writes them.

        The regression guard proper: the CAD reads this object positionally, so
        a tag added, dropped or moved silently costs the whole annotative scale
        representation -- with no warning anywhere, in BricsCAD or in the audit.
        """
        for dim in self.dims:
            ctx = self._context_data(dim)
            # subclasses[0] holds the object header (type, handle, owner)
            actual = [(t.code, t.value)
                      for sub in ctx.xtags.subclasses[1:] for t in sub]
            assert [c for c, _ in actual] == [c for c, _ in _BRICSCAD_DIM_CONTEXT_TAGS], \
                "context data tag sequence drifted from the BricsCAD reference"
            for (code, expected), (_, value) in zip(_BRICSCAD_DIM_CONTEXT_TAGS, actual):
                if expected is not None:
                    assert value == expected, f"group {code}: {value!r} != {expected!r}"


def test_fallback_dimstyle_is_annotative(tmp_path):
    """Without a template the export builds its own dimension style, and is then
    the only source of both annotative flags and of the CLASS declaration -- the
    shipped template carries its own, which would otherwise hide a regression.

    template_path=None means "use the shipped template", so only a missing path
    actually drops the export onto the fallback.
    """
    ifc = ifcopenshell.open(os.path.join(_FILES_DIR, "test_ifc_01.ifc"))
    drawing, pset = find_drawings(ifc)[0]
    out = str(tmp_path / "no_template.dxf")
    export_drawing(ifc, drawing, pset, out, wall_mode="shapely",
                   template_path=str(tmp_path / "no_such_template.dxf"))
    doc = ezdxf.readfile(out)
    assert "BONSAI_DIM" in doc.dimstyles, "the export still used a template"

    dims = list(doc.modelspace().query("DIMENSION"))
    assert dims, "no DIMENSION in the template-less export"
    for name in {d.dxf.dimstyle for d in dims}:
        style = doc.dimstyles.get(name)
        assert style.dxf.get("dimscale", 1.0) == 0.0, \
            f"fallback dimstyle {name} has a fixed DIMSCALE"
        assert style.has_extension_dict and \
            style.get_extension_dict().dictionary.get("AcadAnnotative") is not None, \
            f"fallback dimstyle {name} lacks the AcadAnnotative XRECORD"
        assert [t.value for t in style.get_xdata("AcadAnnotative") if t.code == 1070] \
            == [1, 1], f"fallback dimstyle {name} lacks the AcadAnnotative XDATA"
    # the context data class is not in ezdxf's own CLASS_DEFINITIONS
    doc.classes.get("ACDB_ALDIMOBJECTCONTEXTDATA_CLASS")


def _add_extruded_pipe(ifc, direction, name="TestPipe",
                       location=(1.0, 1.0, 0.0), radius=0.04,
                       thickness=0.001, depth=2.5):
    """Add an IfcPipeSegment: hollow circle profile extruded along `direction`."""
    body_ctx = next(c for c in ifc.by_type("IfcGeometricRepresentationSubContext")
                    if c.ContextIdentifier == "Body" and c.ContextType == "Model")
    storey = ifc.by_type("IfcBuildingStorey")[0]

    def pt(*c):
        return ifc.createIfcCartesianPoint([float(v) for v in c])

    def dr(*c):
        return ifc.createIfcDirection([float(v) for v in c])

    solid = ifc.createIfcExtrudedAreaSolid(
        ifc.createIfcCircleHollowProfileDef("AREA", "PIPE_TEST", None,
                                            radius, thickness),
        ifc.createIfcAxis2Placement3D(pt(0, 0, 0), dr(0, 0, 1), dr(1, 0, 0)),
        dr(*direction), depth,
    )
    pipe = ifc.createIfcPipeSegment(
        ifcopenshell.guid.new(), None, name, None, None,
        ifc.createIfcLocalPlacement(
            storey.ObjectPlacement,
            ifc.createIfcAxis2Placement3D(pt(*location), dr(0, 0, 1), dr(1, 0, 0))),
        ifc.createIfcProductDefinitionShape(
            None, None,
            [ifc.createIfcShapeRepresentation(body_ctx, "Body", "SweptSolid", [solid])]),
        None, None,
    )
    rel = ifc.by_type("IfcRelContainedInSpatialStructure")[0]
    rel.RelatedElements = list(rel.RelatedElements) + [pipe]
    return pipe


class TestExtrudedCircularProfile:
    """A circular profile extruded along the view axis is a circle in plan.

    Tessellating it instead costs a Ø80 pipe two 52-vertex polylines that trace
    the same 26-gon twice -- visibly faceted, and not a circle to snap to. The
    authored profile is the shape; the mesh is only a fallback.
    """

    PLACEMENT = (1.0, 1.0, 0.0)

    def _export(self, tmp_path, direction):
        """Export the fixture with one pipe added; also return where the pipe's
        placement lands in drawing coordinates (the camera has its own origin)."""
        import numpy as np
        from ifc_dxf.core.camera import camera_matrix_inv_col_major

        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, "test_ifc_01.ifc"))
        pipe = _add_extruded_pipe(ifc, direction, location=self.PLACEMENT)
        drawing, pset = find_drawings(ifc)[0]
        out = str(tmp_path / "pipe.dxf")
        tpl = _TEMPLATE_PATH if os.path.isfile(_TEMPLATE_PATH) else None
        export_drawing(ifc, drawing, pset, out, wall_mode="shapely", template_path=tpl)

        cam_inv = np.array(camera_matrix_inv_col_major(drawing)).reshape(4, 4, order="F")
        expected = cam_inv @ np.array([*self.PLACEMENT, 1.0])
        return ezdxf.readfile(out), pipe.GlobalId, (expected[0], expected[1])

    @staticmethod
    def _entities_of(doc, gid):
        from ezdxf.lldxf.const import DXFValueError

        found = []
        for e in doc.modelspace():
            try:
                tags = e.get_xdata("IFC_DXF")
            except DXFValueError:
                continue
            if gid in [t.value for t in tags if t.code == 1000][1:]:
                found.append(e)
        return found

    def test_vertical_pipe_exports_as_circles(self, tmp_path):
        """Outer and inner wall of the pipe, as native CIRCLE entities."""
        doc, gid, (cx, cy) = self._export(tmp_path, (0.0, 0.0, 1.0))
        entities = self._entities_of(doc, gid)
        assert entities, "the pipe is missing from the export"
        assert {e.dxftype() for e in entities} == {"CIRCLE"}, \
            f"pipe exported as {sorted(e.dxftype() for e in entities)}"

        radii = sorted(round(e.dxf.radius, 6) for e in entities)
        assert radii == [0.039, 0.04], f"radii {radii} are not the profile's"
        for e in entities:
            assert abs(e.dxf.center.x - cx) < 1e-6 and abs(e.dxf.center.y - cy) < 1e-6, \
                f"circle at {e.dxf.center}, not at the pipe's placement ({cx}, {cy})"

    def test_pipe_across_the_view_keeps_the_tessellation(self, tmp_path):
        """The profile is the plan shape only when it faces the viewer. Extruded
        across the view the pipe reads as a rectangle, which the profile cannot
        describe -- so that case must stay on the mesh fallback."""
        doc, gid, _ = self._export(tmp_path, (1.0, 0.0, 0.0))
        entities = self._entities_of(doc, gid)
        assert entities, "the pipe is missing from the export"
        assert "CIRCLE" not in {e.dxftype() for e in entities}, \
            "a pipe extruded across the view was drawn as its profile"


class TestCase01BasicPlan:
    """test_ifc_01.ifc — basic 1:100 plan: walls, door, window, slab, furniture."""

    IFC = "test_ifc_01.ifc"

    @pytest.fixture(autouse=True)
    def export(self):
        ifc = ifcopenshell.open(os.path.join(_FILES_DIR, self.IFC))
        drawings = find_drawings(ifc)
        drawing, pset = drawings[0]
        self.out = _output_path(self.IFC, drawing.Name)
        tpl = _TEMPLATE_PATH if os.path.isfile(_TEMPLATE_PATH) else None
        self.has_template = tpl is not None
        export_drawing(ifc, drawing, pset, self.out, wall_mode="shapely", template_path=tpl)
        self.doc = ezdxf.readfile(self.out)
        self.ifc = ifc

    def test_dxf_has_lwpolylines(self):
        """Walls exported as LWPOLYLINE (shapely mode)."""
        msp = self.doc.modelspace()
        polys = [e for e in msp if e.dxftype() == "LWPOLYLINE"]
        assert len(polys) >= 1

    def test_dxf_has_inserts(self):
        """Door and windows exported as INSERT blocks."""
        msp = self.doc.modelspace()
        inserts = [e for e in msp if e.dxftype() == "INSERT"]
        assert len(inserts) >= 2

    def test_dxf_has_hatch(self):
        """Section walls produce HATCH fill."""
        msp = self.doc.modelspace()
        hatches = [e for e in msp if e.dxftype() == "HATCH"]
        assert len(hatches) >= 1

    def test_dxf_has_dimension(self):
        """Dimension annotation produces DIMENSION entity."""
        msp = self.doc.modelspace()
        dims = [e for e in msp if e.dxftype() == "DIMENSION"]
        assert len(dims) >= 1

    def test_dxf_passes_ezdxf_audit(self):
        """Exported DXF is structurally clean per ezdxf's Auditor."""
        from ifc_dxf.core.audit import audit_dxf_file
        is_clean, report = audit_dxf_file(self.out)
        assert is_clean, report

    def test_hatch_boundaries_have_no_duplicate_vertices(self):
        """No HATCH boundary polyline has consecutive duplicate vertices.

        ezdxf's Auditor does not check this, but BricsCAD's audit rejects it,
        so assert it explicitly (locks in _dedup_ring)."""
        msp = self.doc.modelspace()
        for hatch in (e for e in msp if e.dxftype() == "HATCH"):
            for path in hatch.paths:
                verts = getattr(path, "vertices", None)
                if not verts:
                    continue
                pts = [(round(v[0], 6), round(v[1], 6)) for v in verts]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    assert a != b, f"duplicate hatch vertex {a} on layer {hatch.dxf.layer}"

    def test_entities_carry_ifc_xdata(self):
        """Geometry carries IFC identity as XDATA under appid IFC_DXF.

        Layout: first 1000-string = IfcClass, following 1000-strings = one
        GlobalId per contributing element (fused walls list several). Every
        GlobalId must resolve in the source IFC to an element of that class.
        The DXF counterpart of Bonsai's SVG ifc:guid/class attributes.
        """
        from ezdxf.lldxf.const import DXFValueError

        msp = self.doc.modelspace()
        tagged = set()
        for e in msp:
            try:
                tags = e.get_xdata("IFC_DXF")
            except DXFValueError:
                continue
            values = [t.value for t in tags if t.code == 1000]
            assert len(values) >= 2, \
                f"IFC_DXF XDATA needs class + >=1 guid, got {values}"
            cls, gids = values[0], values[1:]
            assert cls.startswith("Ifc"), f"bad IFC class {cls!r}"
            for g in gids:
                element = self.ifc.by_guid(g)  # raises if unknown guid
                assert element.is_a() == cls, \
                    f"XDATA class {cls} != element class {element.is_a()} ({g})"
            tagged.add(e.dxf.handle)

        # Every INSERT (plan symbols, tag blocks) and HATCH must be tagged;
        # wall outlines (LWPOLYLINE) too, except untagged auxiliary lines.
        for e in msp:
            if e.dxftype() in ("INSERT", "HATCH"):
                assert e.dxf.handle in tagged, \
                    f"{e.dxftype()} on layer {e.dxf.layer} lacks IFC_DXF XDATA"
        polys_tagged = [e for e in msp
                        if e.dxftype() == "LWPOLYLINE" and e.dxf.handle in tagged]
        assert polys_tagged, "no LWPOLYLINE carries IFC_DXF XDATA"

    def test_annotation_context_data_has_owner(self):
        """Every annotation context-data object has a non-null owner handle.

        Guards the Owner Id (0) defect BricsCAD's audit flagged and repaired."""
        ctx_types = (
            "ACDB_TEXTOBJECTCONTEXTDATA_CLASS",
            "ACDB_DIMENSIONOBJECTCONTEXTDATA_CLASS",
        )
        for obj in self.doc.objects:
            if obj.dxftype() in ctx_types:
                owner = obj.dxf.get("owner", None)
                assert owner not in (None, "0"), \
                    f"{obj.dxftype()} has invalid owner {owner!r}"

    def test_a1_layout_cartiglio_date(self):
        """A1 paper-space layout has a date TEXT entity (cartiglio filled)."""
        if not self.has_template:
            pytest.skip("Template not found — cartiglio test skipped")
        for layout_name in self.doc.layouts.names():
            if layout_name == "Model":
                continue
            layout = self.doc.layouts.get(layout_name)
            texts = [e for e in layout if e.dxftype() == "TEXT"]
            date_texts = [
                e for e in texts
                if e.dxf.get("text", "").count(".") == 2  # dd.mm.yyyy
            ]
            assert date_texts, "No date text found in paper-space layout"
            return
        pytest.skip("No paper-space layout in DXF")
