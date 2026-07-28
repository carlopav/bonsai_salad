"""Registration of the comparison as a REFERENCE document inside the IFC."""

import os

import ifcopenshell
import ifcopenshell.api.document
import ifcopenshell.api.root
import ifcopenshell.validate
import pytest

from drawing_diff.core import ifc_link


@pytest.fixture
def project():
    ifc_file = ifcopenshell.file(schema="IFC4")
    ifcopenshell.api.root.create_entity(ifc_file, ifc_class="IfcProject", name="P")
    return ifc_file


@pytest.fixture
def paths(tmp_path):
    drawings = tmp_path / "drawings"
    drawings.mkdir()
    return str(drawings / "01 PIANO TERRA PLAN RAFFRONTO.svg"), str(tmp_path / "SP.ifc")


@pytest.fixture
def info():
    return ifc_link.DiffInfo(
        existing_svg="SF PLAN.svg",
        project_svg="SP PLAN.svg",
        tolerance_cm=0.5,
        min_area_m2=0.005,
        area_demolished_m2=12.5,
        area_new_m2=3.25,
    )


def add_document(ifc_file, location):
    """Stands in for bonsai.core.drawing.add_document: same entities, same
    relative Location, and the same habit of returning nothing."""

    def create():
        document = ifcopenshell.api.document.add_information(ifc_file)
        reference = ifcopenshell.api.document.add_reference(ifc_file, information=document)
        ifcopenshell.api.document.edit_information(
            ifc_file, information=document, attributes={"Identification": "X", "Name": "REF", "Scope": "REFERENCE"}
        )
        ifcopenshell.api.document.edit_reference(ifc_file, reference=reference, attributes={"Location": location})

    return create


def test_creates_the_reference_when_none_matches(project, paths, info):
    svg_path, ifc_path = paths
    document = ifc_link.ensure_reference(
        project, svg_path, ifc_path, add_document(project, "drawings/01 PIANO TERRA PLAN RAFFRONTO.svg")
    )
    ifc_link.describe_reference(project, document, info)

    assert document.Scope == "REFERENCE"
    assert document.Purpose == ifc_link.PURPOSE
    assert document.CreationTime
    assert "SF PLAN.svg" in document.Description and "SP PLAN.svg" in document.Description
    assert "12.50" in document.Description and "3.25" in document.Description


def test_second_run_reuses_the_document_instead_of_piling_them_up(project, paths, info):
    svg_path, ifc_path = paths
    create = add_document(project, "drawings/01 PIANO TERRA PLAN RAFFRONTO.svg")
    first = ifc_link.ensure_reference(project, svg_path, ifc_path, create)
    ifc_link.describe_reference(project, first, info)

    info.area_new_m2 = 4.0
    second = ifc_link.ensure_reference(project, svg_path, ifc_path, create)
    ifc_link.describe_reference(project, second, info)

    assert first == second
    assert len(project.by_type("IfcDocumentInformation")) == 1
    assert "4.00" in second.Description


def test_an_absolute_location_is_matched_too(project, paths, info):
    svg_path, ifc_path = paths
    document = ifc_link.ensure_reference(project, svg_path, ifc_path, add_document(project, svg_path))
    assert ifc_link.find_reference(project, svg_path, ifc_path) == document


def test_a_reference_to_another_file_is_not_reused(project, paths, info):
    svg_path, ifc_path = paths
    ifc_link.ensure_reference(project, svg_path.replace("RAFFRONTO", "ALTRO"), ifc_path, add_document(project, "a.svg"))
    ifc_link.ensure_reference(project, svg_path, ifc_path, add_document(project, "b.svg"))

    assert len(project.by_type("IfcDocumentInformation")) == 2


def test_a_drawing_document_is_never_mistaken_for_the_comparison(project, paths, info):
    """Bonsai's own drawings are documents too, scoped DRAWING."""
    svg_path, ifc_path = paths
    document = ifcopenshell.api.document.add_information(project)
    reference = ifcopenshell.api.document.add_reference(project, information=document)
    ifcopenshell.api.document.edit_information(project, information=document, attributes={"Scope": "DRAWING"})
    ifcopenshell.api.document.edit_reference(
        project, reference=reference, attributes={"Location": "drawings/01 PIANO TERRA PLAN RAFFRONTO.svg"}
    )
    assert ifc_link.find_reference(project, svg_path, ifc_path) is None


def drawing_document(ifc_file, location, name="MY STOREY PLAN"):
    """A Bonsai drawing: the same shape core.drawing.add_drawing leaves behind."""
    document = ifcopenshell.api.document.add_information(ifc_file)
    reference = ifcopenshell.api.document.add_reference(ifc_file, information=document)
    ifcopenshell.api.document.edit_information(
        ifc_file, information=document, attributes={"Identification": "X", "Name": name, "Scope": "DRAWING"}
    )
    ifcopenshell.api.document.edit_reference(ifc_file, reference=reference, attributes={"Location": location})
    return document


def test_the_sources_survive_as_references_so_it_can_be_regenerated(project, paths, info):
    svg_path, ifc_path = paths
    document = ifc_link.ensure_reference(project, svg_path, ifc_path, add_document(project, "drawings/R.svg"))

    ifc_link.set_sources(project, document, "../SF/drawings/P.svg", "drawings/MY STOREY PLAN.svg")

    assert ifc_link.source_reference(document, ifc_link.EXISTING).Location == "../SF/drawings/P.svg"
    assert ifc_link.source_reference(document, ifc_link.PROJECT).Location == "drawings/MY STOREY PLAN.svg"


def test_regenerating_updates_the_sources_instead_of_adding_references(project, paths, info):
    svg_path, ifc_path = paths
    document = ifc_link.ensure_reference(project, svg_path, ifc_path, add_document(project, "drawings/R.svg"))

    ifc_link.set_sources(project, document, "a.svg", "b.svg")
    ifc_link.set_sources(project, document, "c.svg", "b.svg")

    assert len(document.HasDocumentReferences) == 3  # its own file, plus the two sources
    assert ifc_link.source_reference(document, ifc_link.EXISTING).Location == "c.svg"


def test_the_source_references_never_pass_for_the_comparison_itself(project, paths, info):
    """find_reference walks the references: the sources must not answer for the
    file the document stands for, or a second run would not recognise it."""
    svg_path, ifc_path = paths
    document = ifc_link.ensure_reference(
        project, svg_path, ifc_path, add_document(project, "drawings/01 PIANO TERRA PLAN RAFFRONTO.svg")
    )
    ifc_link.set_sources(project, document, "drawings/SF.svg", "drawings/SP.svg")

    assert ifc_link.find_reference(project, svg_path, ifc_path) == document
    assert ifc_link.find_reference(project, os.path.join(os.path.dirname(svg_path), "SF.svg"), ifc_path) is None


def test_the_file_to_rewrite_is_the_comparison_never_a_source(project, paths, info):
    """Regeneration writes to own_location: picking a source reference instead
    would overwrite a drawing of the other project."""
    svg_path, ifc_path = paths
    document = ifc_link.ensure_reference(
        project, svg_path, ifc_path, add_document(project, "drawings/01 PIANO TERRA PLAN RAFFRONTO.svg")
    )
    ifc_link.set_sources(project, document, "C:/altro/SF.svg", "drawings/SP.svg")

    assert ifc_link.own_location(document) == "drawings/01 PIANO TERRA PLAN RAFFRONTO.svg"


def test_comparisons_lists_only_what_this_tool_wrote(project, paths, info):
    svg_path, ifc_path = paths
    mine = ifc_link.ensure_reference(project, svg_path, ifc_path, add_document(project, "drawings/R.svg"))
    ifc_link.describe_reference(project, mine, info)
    ifc_link.ensure_reference(
        project, svg_path.replace("RAFFRONTO", "ALTRO"), ifc_path, add_document(project, "drawings/altro.svg")
    )
    drawing_document(project, "drawings/MY STOREY PLAN.svg")

    assert ifc_link.comparisons(project) == [mine]


def reference_document(ifc_file, location, name="SF PLAN"):
    """An SVG brought in with Bonsai's bim.add_reference: the existing state."""
    document = drawing_document(ifc_file, location, name)
    ifcopenshell.api.document.edit_information(ifc_file, information=document, attributes={"Scope": "REFERENCE"})
    return document


def test_the_selectable_documents_are_the_svg_ones_and_never_a_comparison(project, paths, info):
    svg_path, ifc_path = paths
    drawing = drawing_document(project, "drawings/MY STOREY PLAN.svg")
    reference = reference_document(project, "../SF/drawings/PLAN.svg")
    reference_document(project, "docs/relazione.pdf", name="RELAZIONE")
    ifcopenshell.api.document.add_information(project)  # a document standing for no file
    comparison = ifc_link.ensure_reference(project, svg_path, ifc_path, add_document(project, "drawings/R.svg"))
    ifc_link.describe_reference(project, comparison, info)

    assert ifc_link.svg_documents(project) == [drawing, reference]


def test_a_stored_source_is_found_back_among_the_selectable_documents(project, paths, info):
    svg_path, ifc_path = paths
    drawing = drawing_document(project, "drawings/MY STOREY PLAN.svg")
    comparison = ifc_link.ensure_reference(
        project, svg_path, ifc_path, add_document(project, "drawings/01 PIANO TERRA PLAN RAFFRONTO.svg")
    )
    ifc_link.describe_reference(project, comparison, info)

    plan = os.path.join(os.path.dirname(ifc_path), "drawings", "MY STOREY PLAN.svg")
    assert ifc_link.find_svg_document(project, plan, ifc_path) == drawing
    # The comparison itself is no source, so it cannot be selected back either.
    assert ifc_link.find_svg_document(project, svg_path, ifc_path) is None


def children_of(parent):
    return [d for rel in parent.IsPointer for d in rel.RelatedDocuments]


def drawings_tree(project, paths):
    """The DRAWINGS document with one drawing under it, as Bonsai leaves it,
    plus the comparison document."""
    svg_path, ifc_path = paths
    parent = ifcopenshell.api.document.add_information(project)
    ifcopenshell.api.document.edit_information(
        project, information=parent, attributes={"Identification": "DRAWINGS", "Name": "DRAWINGS", "Scope": "DRAWINGS"}
    )
    drawing = drawing_document(project, "drawings/MY STOREY PLAN.svg")
    ifc_link.nest_under(project, drawing, parent)
    document = ifc_link.ensure_reference(project, svg_path, ifc_path, add_document(project, "drawings/R.svg"))
    return parent, drawing, document


def test_the_comparison_sits_beside_the_drawings_under_drawings(project, paths):
    parent, drawing, document = drawings_tree(project, paths)

    ifc_link.nest_under(project, document, parent)

    assert children_of(parent) == [drawing, document]
    assert children_of(drawing) == []  # not nested under the drawing it was cut against


def test_removing_the_comparison_leaves_the_drawings_alone(project, paths):
    """document.remove_information deletes a document's children: hanging the
    comparison off a drawing would make Bonsai's Remove Reference take that
    drawing's document down with it."""
    parent, drawing, document = drawings_tree(project, paths)
    ifc_link.nest_under(project, document, parent)

    ifcopenshell.api.document.remove_information(project, information=document)

    assert drawing in project.by_type("IfcDocumentInformation")
    assert children_of(parent) == [drawing]


def test_removing_the_comparison_leaves_the_file_sound(project, paths):
    """What the Remove button does, through Bonsai's own remove_document: the
    source references go with it, nothing is left dangling in the tree."""
    parent, drawing, document = drawings_tree(project, paths)
    ifc_link.nest_under(project, document, parent)
    ifc_link.set_sources(project, document, "../SF/PLAN.svg", "drawings/MY STOREY PLAN.svg")
    assert len(document.HasDocumentReferences) == 3

    ifcopenshell.api.document.remove_information(project, information=document)

    assert [d.Name for d in project.by_type("IfcDocumentInformation")] == ["DRAWINGS", "MY STOREY PLAN"]
    assert [r for r in project.by_type("IfcDocumentReference") if not r.ReferencedDocument] == []
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(project, logger, express_rules=True)
    assert logger.statements == []


def test_removing_an_only_child_drops_the_relationship(project, paths):
    parent, _, document = drawings_tree(project, paths)
    lonely = ifcopenshell.api.document.add_information(project)
    ifc_link.nest_under(project, lonely, document)

    ifcopenshell.api.document.remove_information(project, information=lonely)

    assert document.IsPointer == ()


def test_removing_a_drawing_leaves_the_comparison_alone(project, paths):
    parent, drawing, document = drawings_tree(project, paths)
    ifc_link.nest_under(project, document, parent)

    ifcopenshell.api.document.remove_information(project, information=drawing)

    assert document in project.by_type("IfcDocumentInformation")


def test_renesting_does_not_pile_up_relationships(project, paths):
    parent, drawing, document = drawings_tree(project, paths)

    for _ in range(3):
        ifc_link.nest_under(project, document, parent)

    assert len(project.by_type("IfcDocumentInformationRelationship")) == 1
    assert children_of(parent) == [drawing, document]


def test_ifc2x3_takes_only_the_attributes_it_has(paths, info):
    """In IFC2X3 ElectronicFormat is an entity, not a string, and there is no
    CreationTime: writing them raises in the middle of the transaction, after
    the SVG has already been written."""
    # Raw entities: on IFC2X3 the API wants an owner history, and that wants a
    # user, which exists in Blender and not here.
    ifc_file = ifcopenshell.file(schema="IFC2X3")
    ifc_file.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), Name="P")
    svg_path, ifc_path = paths
    document = ifc_file.create_entity("IfcDocumentInformation", DocumentId="X", Name="R", Scope="REFERENCE")
    # In IFC2X3 the document owns its references, the other way round from IFC4.
    document.DocumentReferences = [ifc_file.create_entity("IfcDocumentReference", Location="drawings/R.svg")]

    ifc_link.describe_reference(ifc_file, document, info)
    ifc_link.set_sources(ifc_file, document, "../SF/PLAN.svg", "drawings/PLAN.svg")

    assert document.Purpose == ifc_link.PURPOSE
    assert "SF PLAN.svg" in document.Description
    assert document.ElectronicFormat is None
    # IFC2X3 has no Description on IfcDocumentReference: the key rides on Name.
    assert ifc_link.source_reference(document, ifc_link.EXISTING).Location == "../SF/PLAN.svg"
    assert ifc_link.own_location(document) == "drawings/R.svg"


def test_the_media_type_is_declared(project, paths, info):
    svg_path, ifc_path = paths
    document = ifc_link.ensure_reference(project, svg_path, ifc_path, add_document(project, "drawings/R.svg"))
    ifc_link.describe_reference(project, document, info)
    assert document.ElectronicFormat == ifc_link.SVG_FORMAT


def test_a_creation_that_adds_nothing_is_an_error(project, paths):
    svg_path, ifc_path = paths
    with pytest.raises(LookupError, match="0 documents"):
        ifc_link.ensure_reference(project, svg_path, ifc_path, lambda: None)
