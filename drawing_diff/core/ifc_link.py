"""Provenance of the comparison drawing on its REFERENCE document."""

import datetime
import os
from dataclasses import dataclass

import ifcopenshell.api.document

PURPOSE = "Raffronto demolizioni/ricostruzioni"
# Whoever opens the IFC has to know which add-on wrote this, not which module.
TOOL_NAME = "bonsai-salad drawing_diff"
TOOL_VERSION = "1.0"
SVG_FORMAT = "image/svg+xml"
# Bonsai tells apart several references of one document by their description,
# the way its sheets keep LAYOUT and SHEET apart (bim/module/drawing/sheeter.py:92).
EXISTING = "DIFF_EXISTING"
PROJECT = "DIFF_PROJECT"


@dataclass
class DiffInfo:
    existing_svg: str
    project_svg: str
    tolerance_cm: float
    min_area_m2: float
    area_demolished_m2: float
    area_new_m2: float


def find_document(ifc_file, path, ifc_path, scope=None):
    target = _normalise(path)
    for document in ifc_file.by_type("IfcDocumentInformation"):
        if scope is not None and document.Scope != scope:
            continue
        location = _location(document)
        if location and _normalise(_absolute(location, ifc_path)) == target:
            return document


def find_reference(ifc_file, svg_path, ifc_path):
    """Without the lookup every run would pile up another REFERENCE document."""
    return find_document(ifc_file, svg_path, ifc_path, scope="REFERENCE")


def ensure_reference(ifc_file, svg_path, ifc_path, create):
    """`create` is Bonsai's core.drawing.add_document, which returns nothing:
    the new document is caught by difference rather than looked up by path,
    which would silently miss whenever our path and Bonsai's URI disagree."""
    document = find_reference(ifc_file, svg_path, ifc_path)
    if document is not None:
        return document
    before = set(ifc_file.by_type("IfcDocumentInformation"))
    create()
    created = set(ifc_file.by_type("IfcDocumentInformation")) - before
    if len(created) != 1:
        raise LookupError(f"Adding the reference created {len(created)} documents instead of one.")
    return created.pop()


def describe_reference(ifc_file, document, info):
    """Everything sits in the standard attributes, which every viewer shows: a
    property set on an IfcDocumentInformation is invisible to Bonsai's
    References list and to ifcopenshell.util.element.get_psets alike."""
    attributes = {"Description": description(info), "Purpose": PURPOSE}
    if ifc_file.schema != "IFC2X3":
        attributes["CreationTime"] = datetime.datetime.now().isoformat(timespec="seconds")
        # In IFC2X3 ElectronicFormat is an IfcDocumentElectronicFormat entity,
        # not a string: assigning the media type there raises.
        attributes["ElectronicFormat"] = SVG_FORMAT
    ifcopenshell.api.document.edit_information(ifc_file, information=document, attributes=attributes)


def comparisons(ifc_file):
    """Every comparison this tool has written into the file, oldest first."""
    return [
        document
        for document in ifc_file.by_type("IfcDocumentInformation")
        if document.Scope == "REFERENCE" and document.Purpose == PURPOSE
    ]


def svg_documents(ifc_file):
    """The documents a comparison can be made of: Bonsai's own drawings, and
    the SVGs imported as references, which is where the existing state lands.
    A comparison is left out, or it could be compared against itself."""
    documents = []
    for document in ifc_file.by_type("IfcDocumentInformation"):
        if document.Scope not in ("DRAWING", "REFERENCE") or document.Purpose == PURPOSE:
            continue
        location = own_location(document)
        if location and location.lower().endswith(".svg"):
            documents.append(document)
    return documents


def find_svg_document(ifc_file, path, ifc_path):
    """The selectable document a path stands for: a comparison remembers its
    sources as URIs, so the lists are set back from those."""
    target = _normalise(path)
    for document in svg_documents(ifc_file):
        if _normalise(_absolute(own_location(document), ifc_path)) == target:
            return document


def set_sources(ifc_file, document, existing_uri, project_uri):
    """The two source drawings, kept as references of the comparison so it can
    be regenerated later. A description in prose could not be read back."""
    for key, uri in ((EXISTING, existing_uri), (PROJECT, project_uri)):
        reference = source_reference(document, key)
        if reference is None:
            reference = ifcopenshell.api.document.add_reference(ifc_file, information=document)
        attributes = {"Location": uri, _description_attribute(ifc_file): key}
        ifcopenshell.api.document.edit_reference(ifc_file, reference=reference, attributes=attributes)


def source_reference(document, key):
    references = getattr(document, "HasDocumentReferences", None) or getattr(document, "DocumentReferences", None)
    for reference in references or []:
        if getattr(reference, _description_attribute(document.file)) == key:
            return reference


def _description_attribute(ifc_file):
    # IFC2X3 has no Description on IfcDocumentReference: Bonsai uses Name there
    # (tool/drawing.py:2282).
    return "Name" if ifc_file.schema == "IFC2X3" else "Description"


def nest_under(ifc_file, document, parent):
    """The comparison is a deliverable like the other drawings, so it hangs off
    the DRAWINGS document as their sibling, not off the drawing it was cut
    against: IfcDocumentInformationRelationship means composition, not
    derivation, and document.remove_information deletes a document's children
    (ifcopenshell/api/document/remove_information.py:51). As a sibling nothing
    cascades sideways - removing the comparison, or the drawing, leaves the
    other alone.

    Reuses the parent's first relationship, which is the convention
    ifcopenshell.api.document.add_information follows for its own children."""
    for relationship in parent.IsPointer or []:
        if document not in relationship.RelatedDocuments:
            relationship.RelatedDocuments = list(relationship.RelatedDocuments) + [document]
        return relationship
    return ifc_file.create_entity(
        "IfcDocumentInformationRelationship", RelatingDocument=parent, RelatedDocuments=[document]
    )


def description(info):
    return (
        f"{TOOL_NAME} {TOOL_VERSION}."
        f" Stato di fatto: {os.path.basename(info.existing_svg)}."
        f" Progetto: {os.path.basename(info.project_svg)}."
        f" Demolizioni {info.area_demolished_m2:.2f} m2, nuove costruzioni {info.area_new_m2:.2f} m2."
        f" Tolleranza {info.tolerance_cm} cm, area minima {info.min_area_m2} m2."
    )


def own_location(document):
    """Never ask Bonsai's get_document_uri for this: it returns the first
    reference that has a location, and a comparison carries three. Writing to
    the wrong one would overwrite a source drawing of the other project."""
    return _location(document)


def _location(document):
    """The file the document stands for, which is not any of the source
    references a comparison carries alongside it."""
    if getattr(document, "Location", None):
        return document.Location
    references = getattr(document, "HasDocumentReferences", None) or getattr(document, "DocumentReferences", None)
    for reference in references or []:
        if reference.Location and getattr(reference, _description_attribute(document.file)) not in (EXISTING, PROJECT):
            return reference.Location


def _absolute(location, ifc_path):
    if os.path.isabs(location):
        return location
    return os.path.join(os.path.dirname(ifc_path), location)


def _normalise(path):
    return os.path.normcase(os.path.normpath(path))
