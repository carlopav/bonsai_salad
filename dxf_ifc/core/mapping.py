# Bonsai Salad — dxf_ifc tool
# Copyright (C) 2026 Carlo Pavan <carlopav@gmail.com>
# GPL-3.0

"""
DXF layer -> IFC class mapping, read from a CSV template (pure Python, no bpy).

The CSV has four columns; only the first two are required:

    dxf_layer,ifc_class,predefined_type,name
    WALL-EXST,IfcWall,,Existing wall
    WALL-*,IfcWall,,
    *,IfcBuildingElementProxy,,

`dxf_layer` is either a layer name or an fnmatch pattern (`WALL-*`, `A-???`).
Exact names win over patterns, and patterns are tried in file order, so put the
specific rows above the general ones. Blank lines and lines starting with `#`
are ignored, and layer names are matched without regard to case.
"""

from __future__ import annotations

import csv
import fnmatch
import re
from collections import OrderedDict
from pathlib import Path
from typing import NamedTuple, Optional

import ifcopenshell

# Layers with no row of their own land here.
FALLBACK_CLASS = "IfcBuildingElementProxy"

FIELDNAMES = ["dxf_layer", "ifc_class", "predefined_type", "name"]

# CAD layer codes -> (IFC class, predefined type). Layer names are abbreviations
# that no schema can know -- GLAZ, CLNG, FNDN, HRAL -- so these are matched first
# and are the only place office conventions are hard-coded. Longest token wins,
# so "curtainwall" beats "wall". Used only to pre-fill a generated template; the
# guess is a starting point for someone to correct, never applied behind their
# back.
#
# The major group codes follow the AIA/NCS CAD Layer Guidelines. The vocabulary
# was checked against the layer list in LetsBIMtogether/AutoLisp (MIT), which
# carries the National CAD Standard v5 codes with their descriptions; only the
# codes that map to a defensible IFC class were taken. Civil and survey groups
# -- easements, parking, topography, property lines -- deliberately are not
# here: no IFC building element fits them, and the proxy fallback says so
# honestly rather than guessing.
_ABBREVIATIONS = [
    # (token, ifc_class, predefined_type)
    ("curtainwall", "IfcCurtainWall", None),
    ("curtwall", "IfcCurtainWall", None),
    ("curtn", "IfcCurtainWall", None),
    ("wall", "IfcWall", None),
    ("part", "IfcWall", "PARTITIONING"),
    ("tptn", "IfcWall", "PARTITIONING"),
    ("prtn", "IfcWall", "PARTITIONING"),
    ("glaz", "IfcWindow", None),
    ("wind", "IfcWindow", None),
    ("door", "IfcDoor", None),
    ("colu", "IfcColumn", None),
    ("cols", "IfcColumn", None),
    ("col", "IfcColumn", None),
    ("beam", "IfcBeam", None),
    ("joist", "IfcBeam", "JOIST"),
    ("slab", "IfcSlab", None),
    ("levl", "IfcSlab", "FLOOR"),
    ("deck", "IfcSlab", None),
    # Bare A-FLOR is the floor itself; the A-FLOR-* minor groups that mean
    # something else (STRS, HRAL, PATT) are caught before this is reached.
    ("flor", "IfcSlab", "FLOOR"),
    ("fndn", "IfcFooting", None),
    ("foot", "IfcFooting", None),
    ("pile", "IfcPile", None),
    ("roof", "IfcRoof", None),
    ("strs", "IfcStair", None),
    ("stair", "IfcStair", None),
    ("hral", "IfcRailing", "HANDRAIL"),
    ("gral", "IfcRailing", "GUARDRAIL"),
    ("rail", "IfcRailing", None),
    ("ramp", "IfcRamp", None),
    ("clng", "IfcCovering", "CEILING"),
    ("ceil", "IfcCovering", "CEILING"),
    ("flor-fin", "IfcCovering", "FLOORING"),
    # NCS interiors: finishes are coverings, millwork is furniture.
    ("crpt", "IfcCovering", "FLOORING"),
    ("fnsh", "IfcCovering", None),
    ("tile", "IfcCovering", None),
    ("mill", "IfcFurniture", None),
    ("case", "IfcFurniture", None),
    ("cswk", "IfcFurniture", None),
    ("cabinet", "IfcFurniture", None),
    ("casework", "IfcFurniture", None),
    ("furn", "IfcFurniture", None),
    ("eqpm", "IfcSystemFurnitureElement", None),
    ("brcg", "IfcMember", "BRACE"),
    ("strt", "IfcMember", None),
    ("mull", "IfcMember", "MULLION"),
    ("sanr", "IfcSanitaryTerminal", None),
    ("pfix", "IfcSanitaryTerminal", None),
    ("plmb", "IfcSanitaryTerminal", None),
    ("fixt", "IfcSanitaryTerminal", None),
    ("duct", "IfcDuctSegment", None),
    ("hvac", "IfcDuctSegment", None),
    ("pipe", "IfcPipeSegment", None),
    # NCS piping major groups, all segments of pipe as far as IFC is concerned.
    ("sswr", "IfcPipeSegment", None),
    ("strm", "IfcPipeSegment", None),
    ("watr", "IfcPipeSegment", None),
    ("domw", "IfcPipeSegment", None),
    ("ngas", "IfcPipeSegment", None),
    ("fuel", "IfcPipeSegment", None),
    ("stem", "IfcPipeSegment", None),
    ("mdgs", "IfcPipeSegment", None),
    ("chwr", "IfcPipeSegment", None),
    ("htwr", "IfcPipeSegment", None),
    ("mhol", "IfcDistributionChamberElement", "MANHOLE"),
    ("comm", "IfcCommunicationsAppliance", None),
    ("alrm", "IfcAlarm", None),
    ("cctv", "IfcAudioVisualAppliance", None),
    ("catv", "IfcAudioVisualAppliance", None),
    ("evtr", "IfcTransportElement", None),
    ("conv", "IfcTransportElement", None),
    ("escl", "IfcTransportElement", "ESCALATOR"),
    # Site and landscape: no building element fits, but these are real features.
    ("plnt", "IfcGeographicElement", None),
    ("topo", "IfcGeographicElement", None),
    ("site", "IfcGeographicElement", None),
    ("lite", "IfcLightFixture", None),
    ("lght", "IfcLightFixture", None),
    ("powr", "IfcElectricAppliance", None),
    ("swch", "IfcSwitchingDevice", None),
    ("switchgear", "IfcSwitchingDevice", None),
    ("area", "IfcSpace", None),
    ("room", "IfcSpace", None),
    ("space", "IfcSpace", None),
    # Drawing furniture, not building fabric.
    ("anno", "IfcAnnotation", None),
    ("text", "IfcAnnotation", None),
    ("dim", "IfcAnnotation", None),
    ("note", "IfcAnnotation", None),
    ("iden", "IfcAnnotation", None),
    ("ttlb", "IfcAnnotation", None),
    ("patt", "IfcAnnotation", None),
    ("hatch", "IfcAnnotation", None),
    ("grid", "IfcAnnotation", None),
    ("symb", "IfcAnnotation", None),
]
_ABBREVIATIONS.sort(key=lambda row: -len(row[0]))

# The AIA convention is D-MJRM-MINR-STAT: discipline, major group, minor group,
# status. The minor group routinely overrides the major one -- A-WALL-IDEN is
# wall tags, not a wall; A-WALL-PATT is hatching, not a wall -- so these fields
# win over whatever the major group says.
_ANNOTATION_FIELDS = {
    "anno", "iden", "idec", "ident", "dims", "dim", "note", "text", "txt",
    "patt", "symb", "tag", "tags", "keyn", "legn", "titl", "ttlb", "mark",
    "targ", "refr", "nplt", "revc", "revs", "sheet",
}

# Whole drawings that are drafted linework rather than building fabric: details,
# elevations, sections, and the weight-graded lines within them.
_DRAWING_MAJORS = {"detl", "elev", "sect", "diag"}
_DRAWING_MINORS = {"dark", "hddn", "medm", "thin", "lite-wt", "prof"}

# (major, minor) -> (class, predefined type), where the pair means something
# different from the major group alone.
_PAIRS = {
    ("clng", "lite"): ("IfcLightFixture", None),
    ("clng", "hvac"): ("IfcAirTerminal", None),
    ("clng", "fire"): ("IfcFireSuppressionTerminal", None),
    ("clng", "grid"): ("IfcCovering", "CEILING"),
    ("clng", "susp"): ("IfcCovering", "CEILING"),
    ("glaz", "mull"): ("IfcMember", "MULLION"),
    ("glaz", "sill"): ("IfcMember", None),
    ("case", "abov"): ("IfcFurniture", None),
    ("flor", "levl"): ("IfcSlab", "FLOOR"),
    ("flor", "strs"): ("IfcStair", None),
    ("flor", "hral"): ("IfcRailing", "HANDRAIL"),
    ("flor", "gral"): ("IfcRailing", "GUARDRAIL"),
    ("flor", "rail"): ("IfcRailing", None),
    ("flor", "fin"): ("IfcCovering", "FLOORING"),
    ("flor", "tptn"): ("IfcWall", "PARTITIONING"),
    ("flor", "evtr"): ("IfcTransportElement", None),
    ("wall", "head"): ("IfcWall", None),
    ("door", "head"): ("IfcDoor", None),
    ("eqpm", "ceil"): ("IfcSystemFurnitureElement", None),
}


def _fields(layer_name: str) -> list:
    """Split a layer name into its lowercase fields, dropping status suffixes."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", layer_name) if p]
    out = []
    for part in parts:
        low = part.lower()
        # Status field: DEMO/EXST/NEW, or the single letters D/E/N used for them.
        if low in ("demo", "exst", "new", "temp", "futr", "d", "e", "n", "f"):
            continue
        if low.isdigit():
            continue
        out.append(low)
    return out

# Spatial structure is built by the project, not guessed from a layer name.
_NOT_GUESSABLE = {
    "IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey",
    "IfcSpatialZone", "IfcExternalSpatialElement",
}

# schema name -> {class: (name_tokens, joined_name, {predefined_type: tokens})}
_schema_cache: dict = {}


def _split_words(name: str) -> list:
    """IfcCurtainWall -> ['curtain', 'wall']; SKIRTINGBOARD -> ['skirtingboard']."""
    if name.startswith("Ifc"):
        name = name[3:]
    return [w.lower() for w in re.findall(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|[a-z]+", name)]


def _schema_index(model: Optional[ifcopenshell.file]) -> dict:
    """
    Every concrete IfcProduct subtype in *model*'s schema, with its name broken
    into words and its PredefinedType values enumerated.

    Taken from the schema ifcopenshell already has rather than a bundled copy of
    the IFC documentation, so it needs no network and always describes the
    schema the project is actually written in.
    """
    wrapper = ifcopenshell.ifcopenshell_wrapper
    schema_name = "IFC4"
    if model is not None:
        schema_name = getattr(model, "schema_identifier", None) or model.schema
    if schema_name in _schema_cache:
        return _schema_cache[schema_name]

    index: dict = {}
    try:
        schema = wrapper.schema_by_name(schema_name)
    except Exception:
        _schema_cache[schema_name] = index
        return index

    def is_product(decl):
        seen = 0
        while decl is not None and seen < 32:
            if decl.name() == "IfcProduct":
                return True
            try:
                decl = decl.supertype()
            except Exception:
                return False
            seen += 1
        return False

    for decl in schema.declarations():
        if not isinstance(decl, wrapper.entity):
            continue
        name = decl.name()
        if name in _NOT_GUESSABLE:
            continue
        try:
            if decl.is_abstract() or not is_product(decl):
                continue
        except Exception:
            continue
        predefined: dict = {}
        try:
            for attr in decl.all_attributes():
                if attr.name() != "PredefinedType":
                    continue
                declared = attr.type_of_attribute()
                while hasattr(declared, "declared_type"):
                    declared = declared.declared_type()
                for item in getattr(declared, "enumeration_items", list)():
                    if item in ("USERDEFINED", "NOTDEFINED"):
                        continue
                    predefined[item] = _split_words(item)
                break
        except Exception:
            pass
        index[name] = (_split_words(name), name[3:].lower(), predefined)

    _schema_cache[schema_name] = index
    return index


def _schema_guess(layer_name: str, model) -> tuple:
    """Best (class, predefined_type) for *layer_name* from the schema index."""
    tokens = [t for t in re.split(r"[^A-Za-z]+", layer_name) if t]
    tokens = [t.lower() for t in tokens]
    if not tokens:
        return None, None
    best, best_score, best_type = None, 0, None
    for ifc_class, (words, joined, predefined) in _schema_index(model).items():
        score, found_type = 0, None
        for token in tokens:
            if token == joined:
                score += 14
            elif token in words:
                score += 10
            elif len(token) >= 5 and any(w.startswith(token) for w in words):
                score += 6
            for ptype, ptokens in predefined.items():
                if token == ptype.lower():
                    score += 12
                    found_type = ptype
                elif token in ptokens:
                    score += 5
                    found_type = found_type or ptype
        if score > best_score or (score == best_score and score and best and len(ifc_class) < len(best)):
            best, best_score, best_type = ifc_class, score, found_type
    return (best, best_type) if best_score else (None, None)


class Rule(NamedTuple):
    """One row of the mapping CSV."""

    pattern: str
    ifc_class: str
    predefined_type: Optional[str] = None
    name: Optional[str] = None

    @property
    def is_pattern(self) -> bool:
        return any(ch in self.pattern for ch in "*?[")


def guess(layer_name: str, model: Optional[ifcopenshell.file] = None) -> tuple:
    """
    A plausible (ifc_class, predefined_type) for *layer_name*, for seeding a
    template. Never applied to an import on its own.

    Two stages. CAD layer codes are abbreviations no schema can know -- GLAZ,
    CLNG, FNDN -- so a curated table is consulted first. Anything it does not
    recognise is scored against the class and PredefinedType names in the
    project's own schema, which covers the long tail (boiler, chiller,
    transformer, tendon) without hard-coding any of it.
    """
    fields = _fields(layer_name)

    # 1. Tags, dimensions and hatch patterns are drawing, whatever they label:
    #    A-WALL-IDEN is wall tags, A-WALL-PATT is poche, neither is a wall.
    if any(f in _ANNOTATION_FIELDS for f in fields):
        return "IfcAnnotation", None

    # 2. Details, elevations and sections are drafted linework throughout.
    if any(f in _DRAWING_MAJORS for f in fields) or any(f in _DRAWING_MINORS for f in fields):
        return "IfcAnnotation", None

    # 3. A major/minor pair that means something other than its major group:
    #    A-CLNG-LITE is a light fixture, not a ceiling.
    for major, minor in zip(fields, fields[1:]):
        pair = _PAIRS.get((major, minor))
        if pair:
            return pair

    # 4. A field that is exactly a known code, most specific field last so a
    #    minor group still refines the major one.
    for field in reversed(fields):
        for token, ifc_class, predefined_type in _ABBREVIATIONS:
            if field == token:
                return ifc_class, predefined_type

    # 5. A known code embedded in a longer field.
    low = layer_name.lower()
    for token, ifc_class, predefined_type in _ABBREVIATIONS:
        if token in low:
            return ifc_class, predefined_type

    ifc_class, predefined_type = _schema_guess(layer_name, model)
    if ifc_class:
        return ifc_class, predefined_type
    return FALLBACK_CLASS, None


def guess_ifc_class(layer_name: str, model: Optional[ifcopenshell.file] = None) -> str:
    """The class half of :func:`guess`."""
    return guess(layer_name, model)[0]


def load_mapping(path) -> list[Rule]:
    """
    Read the mapping CSV at *path* into a list of rules, in file order.

    Raises ValueError if the file has no usable rows, so a typo in the path or a
    header-only file is reported rather than silently mapping everything to the
    fallback class.
    """
    path = Path(path)
    rules: list[Rule] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            first = row[0].strip()
            if not first or first.startswith("#"):
                continue
            if first.lower() == "dxf_layer":  # header
                continue
            cells = [c.strip() for c in row[:4]] + [""] * (4 - len(row[:4]))
            layer, ifc_class, predefined_type, name = cells
            if not layer or not ifc_class:
                continue
            rules.append(Rule(layer, ifc_class, predefined_type or None, name or None))
    if not rules:
        raise ValueError(f"No mapping rows found in {path.name}")
    return rules


def resolve(layer_name: str, rules: list[Rule]) -> Rule:
    """
    The rule that governs *layer_name*: an exact name first, then the first
    matching pattern in file order, then the fallback class.
    """
    low = layer_name.lower()
    for rule in rules:
        if not rule.is_pattern and rule.pattern.lower() == low:
            return rule
    for rule in rules:
        if rule.is_pattern and fnmatch.fnmatch(low, rule.pattern.lower()):
            return rule
    return Rule(layer_name, FALLBACK_CLASS)


def element_name(layer_name: str, rule: Rule) -> str:
    """The name to give the element built from *layer_name*."""
    return rule.name or layer_name


def is_valid_class(model: ifcopenshell.file, ifc_class: str) -> bool:
    """Whether *ifc_class* is instantiable in *model*'s schema."""
    try:
        schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(model.schema_identifier)
    except Exception:
        try:
            schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(model.schema)
        except Exception:
            return True  # cannot tell; let the caller try
    try:
        decl = schema.declaration_by_name(ifc_class)
    except Exception:
        return False
    # Abstract entities cannot be instantiated.
    try:
        return not decl.is_abstract()
    except Exception:
        return True


def dxf_layers(dxf_path) -> "OrderedDict[str, int]":
    """
    Layer name -> entity count for everything modelspace draws, with blocks
    expanded, in descending count order. Used to seed a template from a drawing.

    The expansion is the importer's own, so a template always lists exactly the
    layers an import would produce. Counting them here separately is how a
    generated template came to miss every layer that only appears inside a
    nested block.
    """
    import ezdxf

    # Imported inside the function: the importer imports this module, so taking
    # it at module level would be a cycle.
    from .importer import _expand

    doc = ezdxf.readfile(str(dxf_path))
    counts: dict[str, int] = {}
    for entity in doc.modelspace():
        for leaf in _expand(entity):
            counts[leaf.dxf.layer] = counts.get(leaf.dxf.layer, 0) + 1
    return OrderedDict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def write_mapping_template(path, layers, comment: Optional[str] = None,
                           model: Optional[ifcopenshell.file] = None) -> Path:
    """
    Write a mapping CSV at *path* with a row per layer in *layers*, each
    pre-filled with a guessed class, plus a trailing catch-all row.

    *layers* is any iterable of names, or the mapping that :func:`dxf_layers`
    returns, in which case the entity counts are written as comments.
    """
    path = Path(path)
    counts = layers if isinstance(layers, dict) else {name: None for name in layers}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("# dxf_ifc layer mapping\n")
        if comment:
            fh.write(f"# {comment}\n")
        fh.write("#\n")
        fh.write("# One IFC element is created per layer, carrying that layer's linework\n")
        fh.write("# as a Plan/Annotation/PLAN_VIEW representation.\n")
        fh.write("#\n")
        fh.write("# dxf_layer        name or fnmatch pattern; exact names beat patterns,\n")
        fh.write("#                  patterns are tried top to bottom\n")
        fh.write("# ifc_class        e.g. IfcWall, IfcColumn, IfcFurniture\n")
        fh.write("# predefined_type  optional, e.g. SOLIDWALL; leave blank if unsure\n")
        fh.write("# name             optional element name; defaults to the layer name\n")
        fh.write("#\n")
        fh.write(f"# Layers with no row become {FALLBACK_CLASS}.\n")
        writer = csv.writer(fh)
        writer.writerow(FIELDNAMES)
        for name, count in counts.items():
            ifc_class, predefined_type = guess(name, model)
            writer.writerow([name, ifc_class, predefined_type or "", ""])
            if count is not None:
                fh.write(f"# ^ {count} entities on this layer\n")
        writer.writerow(["*", FALLBACK_CLASS, "", ""])
    return path






