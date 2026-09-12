"""IFC data access: drawing discovery, element queries, representation lookup."""

import functools
import json
import re

import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.selector

from .camera import get_camera_frustum_bbox, filter_elements_in_frustum
from .units import project_unit_scale


# Each entry: (ContextType, ContextIdentifier, TargetView).
# Order = priority: first match wins.
# Mirrors Bonsai SVG context selection for Body/Facetation tiers;
# adds FootPrint/Axis as fallback for elements without an OCC section.
_PLAN_SEARCH = {
    "PLAN_VIEW": [
        ("Plan",  "Body",        "PLAN_VIEW"),
        ("Plan",  "Body",        "MODEL_VIEW"),
        ("Model", "Body",        "PLAN_VIEW"),
        ("Model", "Body",        "MODEL_VIEW"),
        ("Model", "FootPrint",   "PLAN_VIEW"),
        ("Model", "Axis",        "PLAN_VIEW"),
        ("Plan",  "Facetation",  "PLAN_VIEW"),
        ("Plan",  "Facetation",  "MODEL_VIEW"),
        ("Model", "Facetation",  "PLAN_VIEW"),
        ("Model", "Facetation",  "MODEL_VIEW"),
    ],
    "REFLECTED_PLAN_VIEW": [
        ("Plan",  "Body",        "REFLECTED_PLAN_VIEW"),
        ("Plan",  "Body",        "MODEL_VIEW"),
        ("Model", "Body",        "REFLECTED_PLAN_VIEW"),
        ("Model", "Body",        "MODEL_VIEW"),
        ("Plan",  "Facetation",  "REFLECTED_PLAN_VIEW"),
        ("Plan",  "Facetation",  "MODEL_VIEW"),
        ("Model", "Facetation",  "REFLECTED_PLAN_VIEW"),
        ("Model", "Facetation",  "MODEL_VIEW"),
    ],
}


def find_drawings(ifc):
    """Return [(IfcAnnotation, pset_dict)] for every Bonsai drawing.

    A drawing is an IfcAnnotation with ObjectType 'DRAWING'. EPset_Drawing
    alone is not enough: Bonsai also puts it (HasLinework, HasAnnotation, ...)
    on text, dimension and linework annotations, often via their type.
    """
    result = []
    for ann in ifc.by_type("IfcAnnotation"):
        if ann.ObjectType != "DRAWING":
            continue
        pset = ifcopenshell.util.element.get_psets(ann).get("EPset_Drawing", {})
        if pset:
            result.append((ann, pset))
    return result


def _find_repr_in_representations(representations, target_view):
    """Search a list of IfcShapeRepresentation for the highest-priority context match."""
    search = _PLAN_SEARCH.get(target_view, [])
    for ctx_type, ctx_id, tv in search:
        for shape_repr in representations:
            ctx = shape_repr.ContextOfItems
            if (ctx.ContextType == ctx_type
                    and ctx.ContextIdentifier == ctx_id
                    and getattr(ctx, "TargetView", None) == tv):
                return shape_repr
    return None


def find_plan_repr(element, target_view):
    """Return IfcShapeRepresentation for this element's Plan context.

    Lookup order (mirrors IFC override semantics):
    1. Element's own Representation (override wins).
    2. Type's RepresentationMaps (inherited geometry).

    Returns (repr, from_type) where from_type=True means geometry is inherited.
    """
    # 1. Element override
    if hasattr(element, "Representation") and element.Representation is not None:
        r = _find_repr_in_representations(
            element.Representation.Representations, target_view
        )
        if r is not None:
            return r, False

    # 2. Type fallback
    ifc_type = ifcopenshell.util.element.get_type(element)
    if ifc_type is not None:
        rep_maps = getattr(ifc_type, "RepresentationMaps", None) or []
        type_reprs = [rm.MappedRepresentation for rm in rep_maps]
        r = _find_repr_in_representations(type_reprs, target_view)
        if r is not None:
            return r, True

    return None, False


def is_mapped_repr(plan_repr):
    """Return True if all items in the repr are IfcMappedItem (geometry from type)."""
    items = plan_repr.Items
    return bool(items) and all(item.is_a("IfcMappedItem") for item in items)


# Characters DXF block names disallow (plus whitespace), stripped from name parts.
_INVALID_BLOCK_CHARS = re.compile(r'[<>/\\":;?*|=,`\s]+')


def _sanitize_name_part(text):
    """Strip whitespace and characters DXF block names disallow from one name part."""
    return _INVALID_BLOCK_CHARS.sub("", str(text or ""))


# PredefinedType sentinel values that carry no useful info — omitted from names.
# USERDEFINED is handled separately (falls back to the entity's ObjectType).
_SKIP_PREDEFINED = {"NOTDEFINED", "NOTKNOWN"}


def make_block_name(ifc_class, predefined_type, object_type, name, globalid):
    """Build a DXF block name "{Class}_{PredefinedType}_{Name}_{GlobalId[:8]}".

    Class has its leading "Ifc" stripped (IfcCoveringType -> Covering);
    PredefinedType is included unless it's a bare sentinel (NOTDEFINED/NOTKNOWN),
    and when it's USERDEFINED the entity's ObjectType is used in its place;
    Name is sanitized of whitespace and characters DXF block names disallow.
    Empty parts are skipped. The first 8 GlobalId chars keep the name unique even
    when multiple entities share the same class and name, e.g.
    "Covering_FLOORING_PavimentoCucinaInGres_2N4bXk9Q". The full GlobalId is
    stored in the block description (DXF group code 4).
    """
    cls = ifc_class[3:] if ifc_class.startswith("Ifc") else ifc_class
    if predefined_type == "USERDEFINED":
        pdt = object_type
    elif predefined_type in _SKIP_PREDEFINED:
        pdt = None
    else:
        pdt = predefined_type
    parts = [_sanitize_name_part(cls), _sanitize_name_part(pdt),
             _sanitize_name_part(name)]
    parts = [p for p in parts if p]
    parts.append(globalid[:8])
    return "_".join(parts)


def get_type_block_name(element):
    """Return (type_entity, block_name) if element has a type, else (None, None).

    block_name follows make_block_name using the type's own class, PredefinedType,
    Name, and GlobalId, e.g. "Covering_FLOORING_PavimentoCucinaInGres_2N4bXk9Q".
    """
    ifc_type = ifcopenshell.util.element.get_type(element)
    if ifc_type is None:
        return None, None
    return ifc_type, make_block_name(
        ifc_type.is_a(), getattr(ifc_type, "PredefinedType", None),
        getattr(ifc_type, "ObjectType", None),
        getattr(ifc_type, "Name", None), ifc_type.GlobalId
    )


def get_assigned_product(element):
    """Return the IfcRelAssignsToProduct.RelatingProduct linked to element, or None.

    Mirrors Bonsai's tool.Drawing.get_assigned_product (bpy.ops.bim.edit_assigned_product):
    a tag annotation (e.g. a space tag) is linked to its target product (e.g. the
    IfcSpace it labels) this way, not via geometric proximity or naming.
    """
    for rel in getattr(element, "HasAssignments", []) or []:
        if rel.is_a("IfcRelAssignsToProduct"):
            return rel.RelatingProduct
    return None


def get_material_name(element):
    """Return the first material name associated with the element, or empty string."""
    try:
        mats = ifcopenshell.util.element.get_materials(element, should_inherit=True)
        if mats:
            return mats[0].Name or ""
    except Exception:
        pass
    return ""


def get_material_key(element):
    """Identity of an element's whole material assignment, for fusion grouping.

    Not the first material name: a layered wall would then be keyed by whichever
    finish its stack happens to start with, so two unrelated wall types fuse
    when they share a plasterboard lining while the same type mirrored does not.
    Layer/profile/constituent *sets* are keyed by the set itself, a single
    material by its name.
    """
    try:
        mat = ifcopenshell.util.element.get_material(element, should_inherit=True)
    except Exception:
        return ""
    if mat is None:
        return ""
    for attr in ("ForLayerSet", "ForProfileSet"):
        target = getattr(mat, attr, None)
        if target is not None:
            return f"set:{target.id()}"
    if mat.is_a("IfcMaterial"):
        return mat.Name or ""
    return f"set:{mat.id()}"


def _get_elements_via_bonsai(ifc, drawing):
    """Reuse Bonsai's own tool.Drawing.get_drawing_elements when running inside
    Blender with Bonsai loaded and `ifc` being the exact file Bonsai has open.

    This is the same element selection Bonsai's SVG export uses: full Blender
    bound_box AABB vs camera box culling (is_in_camera_view), Include/Exclude
    handling including the "filter_structure" JSON form (tool.Search), and
    aggregate re-addition -- so DXF and SVG exports agree on what is in view,
    and long walls whose placement origin lies outside the view are kept.

    Returns a set of elements, or None when Bonsai is unavailable / the file
    doesn't match / anything fails, so the caller falls back to the
    pure-ifcopenshell path.
    """
    try:
        import bpy  # noqa: F401
        from bonsai import tool
    except ImportError:
        return None
    try:
        if tool.Ifc.get() is not ifc:
            return None  # different file object -> Blender scene can't be trusted
        if tool.Ifc.get_object(drawing) is None:
            return None  # no Blender camera object for this drawing
        return set(tool.Drawing.get_drawing_elements(drawing))
    except Exception:
        return None


@functools.lru_cache(maxsize=None)
def _selector_starts_classless_groups():
    """True when this ifcopenshell's selector starts a filter group that has
    no class from every product, as Bonsai's does (ifcopenshell 0.8.1+).

    0.8.0 starts such a group empty, so a drawing's
    Exclude = `... + "EPset_Status"."Status" = "OTHER"` silently matched
    nothing headless while Bonsai hid those elements. Probed on a one-wall
    file rather than read from the version string.
    """
    probe = ifcopenshell.file(schema="IFC4")
    probe.createIfcWall(ifcopenshell.guid.new(), None, "probe")
    try:
        return bool(ifcopenshell.util.selector.filter_elements(probe, 'Name="probe"'))
    except Exception:
        return False


def get_elements(ifc, drawing, pset):
    """Element selection for a drawing.

    Primary path (inside Blender): delegate to Bonsai's
    tool.Drawing.get_drawing_elements -- see _get_elements_via_bonsai.
    Bonsai re-adds the drawing's own annotations to the set (its SVG pipeline
    draws them inline); we strip them because annotations are processed
    separately (Bucket D, _get_drawing_annotations).

    Fallback path (standalone, no bpy/Bonsai): reproduce
    get_drawing_elements + get_elements_in_camera_view in pure ifcopenshell:
    1. Include/Exclude query filters (from EPset_Drawing pset)
    2. Spatial culling: two passes -- cheap origin-in-frustum test, then a
       geometry-AABB overlap test for origin-outside elements (long walls);
       see camera.filter_elements_in_frustum.
    """
    bonsai_elements = _get_elements_via_bonsai(ifc, drawing)
    if bonsai_elements is not None:
        bonsai_elements -= set(ifc.by_type("IfcOpeningElement"))
        bonsai_elements = {e for e in bonsai_elements if not e.is_a("IfcAnnotation")}
        print(f"  Selection  : Bonsai tool.Drawing.get_drawing_elements "
              f"(Blender AABB culling) -> {len(bonsai_elements)} elements")
        return bonsai_elements

    print("  Selection  : standalone fallback (pure ifcopenshell, origin-point frustum)")
    include = pset.get("Include", None)
    exclude = pset.get("Exclude", None)
    if (include or exclude) and not _selector_starts_classless_groups():
        print(f"  Warning    : ifcopenshell {ifcopenshell.version} predates 0.8.1 -- "
              "Include/Exclude filter groups that start without a class match "
              "nothing here, unlike in Bonsai; upgrade ifcopenshell")

    if include:
        try:
            data = json.loads(include)
            query = (data.get("query") if isinstance(data, dict) else None)
            if query:
                elements = ifcopenshell.util.selector.filter_elements(ifc, query)
            else:
                elements = ifcopenshell.util.selector.filter_elements(ifc, include)
        except (json.JSONDecodeError, ValueError):
            elements = ifcopenshell.util.selector.filter_elements(ifc, include)
    else:
        if ifc.schema == "IFC2X3":
            elements = set(ifc.by_type("IfcElement") + ifc.by_type("IfcSpatialStructureElement"))
        else:
            elements = set(ifc.by_type("IfcElement") + ifc.by_type("IfcSpatialElement"))
        elements = {e for e in elements if e.is_a() != "IfcSpace"}

    if exclude:
        try:
            data = json.loads(exclude)
            query = (data.get("query") if isinstance(data, dict) else None)
            if query:
                elements -= ifcopenshell.util.selector.filter_elements(ifc, query)
            else:
                elements -= ifcopenshell.util.selector.filter_elements(ifc, exclude)
        except (json.JSONDecodeError, ValueError):
            elements -= ifcopenshell.util.selector.filter_elements(ifc, exclude)

    elements -= set(ifc.by_type("IfcOpeningElement"))
    elements  = {e for e in elements if not e.is_a("IfcAnnotation")}

    # Spatial culling from camera frustum (Blender-agnostic): cheap origin
    # test + geometry-AABB second pass for origin-outside elements (long walls)
    frustum = get_camera_frustum_bbox(drawing, project_unit_scale(ifc))
    if frustum is not None:
        before = len(elements)
        elements, n_rescued = filter_elements_in_frustum(ifc, elements, frustum)
        print(f"  Frustum    : {before} -> {len(elements)} elements  "
              f"(Z {frustum[4]:.2f}..{frustum[5]:.2f}, "
              f"{n_rescued} rescued by AABB pass)")

    return elements


def _get_drawing_annotations(ifc, drawing):
    """Return list of IfcAnnotation elements associated with a Bonsai drawing.

    Bonsai links all annotations for a drawing into an IfcGroup
    (ObjectType='DRAWING') whose RelatedObjects include the drawing itself.
    We match by GlobalId so the group Name need not equal the drawing Name.
    """
    drawing_guid = drawing.GlobalId
    result = []
    for rel in ifc.by_type("IfcRelAssignsToGroup"):
        group = rel.RelatingGroup
        if not (group.is_a("IfcGroup")
                and getattr(group, "ObjectType", None) == "DRAWING"):
            continue
        if not any(getattr(obj, "GlobalId", None) == drawing_guid
                   for obj in rel.RelatedObjects):
            continue
        for obj in rel.RelatedObjects:
            if obj.is_a("IfcAnnotation") and obj.id() != drawing.id():
                result.append(obj)
    return result
