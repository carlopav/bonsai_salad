"""DXF layer naming and entity styling.

Two orthogonal dimensions, deliberately kept apart:

- the **layer** says *what* an entity is -- one layer per IFC class
  (``IfcWall``, ``IfcSlab``, ...), so freezing a layer hides a class;
- the **entity style** says *how it prints* -- cut / viewed / overhead, written
  explicitly on the entity (colour, linetype, lineweight), not BYLAYER.

Encoding both in the layer name (the old ``IfcWall_Section`` / ``IfcWall_View``
scheme) needed the cartesian product of classes x roles in the template; every
combination the template missed produced entities on an undeclared layer, drawn
with CAD defaults.

Hatches keep their own layer (``{Class}_Hatches``, plus one per material when
the wall is decomposed) so fills stay switchable per class; those layers are
created only when a hatch is actually written.
"""

from .materials import classify_material_color

# role -> (ACI colour, linetype, lineweight in 1/100 mm)
ROLE_STYLES = {
    "section":  (7, "Continuous", 30),
    "view":     (1, "Continuous", 9),
    "overhead": (2, "Dashed", 9),
}

# Layers a hatch lives on, by kind. Class layers are declared in the template;
# these are not (a drawing that cuts nothing must not carry empty hatch layers).
_HATCH_COLOR = 254

# Style for a class layer created at runtime (a class missing from the
# template, e.g. legacy IfcWallStandardCase). Largely inert: entities carry
# their own explicit style.
_CLASS_LAYER_STYLE = (7, "Continuous", 9)

# Analytical volumes: their boundary is drawn for reference but never plotted.
NO_PLOT_CLASSES = frozenset({"IfcSpace", "IfcSpatialZone"})


def layer_name(ifc_class, kind="class", material=None):
    """DXF layer for an IFC class: the bare class, its hatch or overhead layer."""
    if kind == "class":
        return ifc_class
    if kind == "hatch":
        return f"{ifc_class}_Hatches{f'_{material}' if material else ''}"
    if kind == "overhead":
        return f"{ifc_class}_Overhead"
    raise ValueError(f"unknown layer kind: {kind}")


def insert_layer(ifc_class, role):
    """Layer for a block INSERT, which never carries an explicit style.

    A BLOCK must read everything from its layer (its content is BYBLOCK, the
    INSERT stays BYLAYER), so a symbol in a role other than the plain view --
    an overhead filling, drawn dashed -- can only say so by living on the
    role's own layer.
    """
    return layer_name(ifc_class, "overhead") if role == "overhead" else ifc_class


def apply_role(entity, role):
    """Style an entity for its drawing role (see ROLE_STYLES).

    Never applied to INSERTs: see insert_layer.
    """
    style = ROLE_STYLES.get(role)
    if style is None:
        return
    color, linetype, lineweight = style
    entity.dxf.color = color
    entity.dxf.linetype = linetype
    entity.dxf.lineweight = lineweight


def ensure_layer(doc, name, kind="class", material=None):
    """Create `name` if the document lacks it; never restyle an existing layer.

    A template that declares a layer -- ours or the user's own -- keeps its
    colour and lineweight; this only fills the gaps.
    """
    if name in doc.layers:
        return doc.layers.get(name)
    if kind == "hatch":
        color, linetype, lineweight = _HATCH_COLOR, "Continuous", 9
        if material:
            color = classify_material_color(material)
    elif kind == "overhead":
        # Symbols cannot be styled per entity, so this layer carries the role.
        color, linetype, lineweight = ROLE_STYLES["overhead"]
    else:
        color, linetype, lineweight = _CLASS_LAYER_STYLE
    layer = doc.layers.add(name)
    layer.color = color
    # Lineweight only exists as a DXF attribute: `layer.lineweight = ...` binds
    # a plain Python attribute and silently leaves the layer at its default.
    layer.dxf.lineweight = lineweight
    ensure_linetype(doc, linetype)
    layer.dxf.linetype = linetype
    if name in NO_PLOT_CLASSES:
        layer.dxf.plot = 0
    return layer


def ensure_linetype(doc, name):
    """Register a linetype the template may not carry (e.g. Dashed)."""
    if not name or name.upper() == "CONTINUOUS" or name in doc.linetypes:
        return
    try:
        doc.linetypes.add(
            name, pattern=[0.6, 0.4, -0.2], description="Dashed ____ ____")
    except Exception:
        pass


def ensure_entity_layers(doc, names):
    """Create every class layer in `names` that the template lacks."""
    for name in names:
        if name:
            ensure_layer(doc, name)
