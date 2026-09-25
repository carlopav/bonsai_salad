# dxf_ifc

Import DXF geometry as an IFC representation on a Bonsai element.

## What it does

Reads a `.dxf` file and assigns its contents as an `IfcShapeRepresentation`
(`RepresentationIdentifier` `Annotation`, `RepresentationType`
`GeometricCurveSet`) on an IFC element — either the active one, a newly created
`IfcAnnotation`, or one element per DXF layer classed by a
[mapping CSV](#elements-by-layer).

The geometry is written into the IFC file, not as Blender meshes, so it
normally appears in the 2D drawing view rather than the 3D model view.

Any existing representation in the chosen subcontext is removed and replaced.
Representations in other subcontexts are left alone.

[NOTES.md](NOTES.md) collects what was not obvious about DXF, IFC and Blender
while building this — symptoms first, so it is searchable from whatever you are
staring at. **Add to it in the same commit that fixes a non-obvious thing.** A
bug that takes an afternoon to corner and a line to fix leaves nothing behind
otherwise.

## Usage

An IFC project must be loaded. Optionally select the element you want the
geometry attached to, then:

**Sidebar → Bonsai Salad → Geometry → Import External Geometry as
Representation → Import DXF as Representation**

A file browser opens with the options below in its sidebar.

### Source

| Option | Description |
|---|---|
| All | Every supported entity in modelspace |
| Block | Only entities from one block definition |
| Layer | Only entities on one layer, after blocks are expanded |

The Block and Layer dropdowns are filled by scanning the file at the current
filepath, which happens when you switch to that mode — so browse to the `.dxf`
first, otherwise the lists read `— scan a DXF file first —`.

The Layer list is the layers **an import would actually produce**, with an
entity count beside each. That is not the same as the layers of the drawing's
top-level entities: blocks are expanded on import, so a layer holding nothing
but block references contributes no geometry of its own, and its contents
arrive under the layers their own entities name. Such a layer is therefore not
offered, and layers that exist only inside a block are.

Blender remembers an operator's settings between runs, so a Source mode and
layer chosen once stay chosen. If an import reports no geometry, check that
Source is still what you want.

### Destination

| Option | Description |
|---|---|
| Active element | Attach to the selected IFC element |
| New annotation | Create an `IfcAnnotation` (`LINEWORK`) named after the file, written to `Plan / Annotation / PLAN_VIEW` |
| Elements by layer | Create one IFC element per DXF layer, classed by a mapping CSV |

With **Active element**, one of two modes applies:

| Mode | Description |
|---|---|
| Overwrite active context | Replace the representation in the element's current subcontext — `Plan/Annotation` in `PLAN_VIEW` or `REFLECTED_PLAN_VIEW` if present, else the element's first representation |
| Use context | Pick any existing context or subcontext in the model |

Defaults are chosen when the dialog opens: no selection gives **New
annotation**; a selected element with no representation gives **Use context**
and a warning that no representation was found.

A new annotation is assigned to a spatial container — the default one if
Bonsai has one set, otherwise the first `IfcBuildingStorey`, `IfcBuilding` or
`IfcSite` in the model.

## Elements by layer

With **Elements by layer**, each DXF layer becomes one IFC element of a class
you choose, carrying that layer's linework. `WALL-EXST` becomes an `IfcWall`,
`COL` an `IfcColumn`, and so on.

The elements are typed, named and placed in the spatial tree, but the geometry
is still 2D linework in `Plan / Annotation / PLAN_VIEW` — **no 3D bodies are
created**, so the result shows in drawings rather than in the 3D model view.

### The mapping CSV

Four columns, of which only the first two are required:

```csv
dxf_layer,ifc_class,predefined_type,name
WALL-EXST,IfcWall,,Existing wall
A-GLAZ*,IfcWindow,,
*TEXT*,IfcAnnotation,,
*,IfcBuildingElementProxy,,
```

| Column | Meaning |
|---|---|
| `dxf_layer` | A layer name, or an fnmatch pattern (`*`, `?`, `[abc]`) |
| `ifc_class` | Any class the project's schema can instantiate |
| `predefined_type` | Optional, e.g. `CEILING`. Leave blank if unsure |
| `name` | Optional element name; defaults to the layer name |

Exact names beat patterns, and patterns are tried top to bottom, so keep the
specific rows above the general ones. Matching ignores case. Blank lines and
lines starting with `#` are ignored. Layers with no row at all become
`IfcBuildingElementProxy`, so nothing is dropped silently.

A class that the schema cannot instantiate — a typo, or an abstract class like
`IfcElement` — causes that layer to be skipped, and the count of skipped layers
is reported when the import finishes.

### Which CSV gets used

Set it in the **Elements by layer** box on the sidebar panel, *before* running
the import. It is chosen there rather than in the import dialog because that
dialog is itself a file browser, and Blender allows only one open at a time —
a second one fails with *"Cannot activate a file selector dialog, one already
open"*.

In order of preference:

1. The operator's `mapping_filepath`, for scripted calls
2. `<drawing>.layers.csv`, `<drawing>.csv`, or `layer_mapping.csv` beside the DXF
3. The **Layer Mapping** field on the panel
4. [`templates/layer_mapping.csv`](templates/layer_mapping.csv), shipped with the add-on

**A CSV beside the drawing beats the panel field.** The panel setting is a
default for drawings that have none of their own, and it is sticky — left
pointing at the last drawing's CSV, it would classify a new drawing against
another one's layer names. Those match nothing, and every layer would land on
`IfcBuildingElementProxy` with no error to explain it.

The import dialog shows both the CSV it settled on and where that came from
(`beside the DXF`, `from the panel`, `add-on template`), and the same appears
in the completion report, so the file in force is never a guess.

The shipped template covers AIA-style prefixes (`A-WALL*`, `S-BEAM*`,
`M-HVAC-DUCT*`, …) and is a reasonable starting point, but layer conventions
vary per office, so expect to edit it.

### Generating one from a drawing

**Generate Layer Mapping CSV** in the panel reads a DXF and writes
`<drawing>.layers.csv` beside it, with a row per layer actually found —
blocks expanded — each pre-filled with a guessed class and an entity count in a
comment. Edit it, then run the import; it is picked up automatically because it
sits next to the DXF.

The scan uses the importer's own block expansion, so the template lists exactly
the layers an import produces. Regenerate it whenever the drawing changes: a
CSV has no row for a layer that did not exist when it was written, and every
such layer lands on `IfcBuildingElementProxy`. A quick check is the row count
against the layer count reported after an import — if the CSV is short, it is
stale.

### How the guessing works

The result is always a starting point to correct — a guess is never applied to
an import on its own.

Layer names are read as the AIA convention **`D-MJRM-MINR-STAT`**: discipline,
major group, minor group, status. Status fields (`DEMO`, `EXST`, `NEW`, or the
bare `D` / `E` / `N`) are ignored for classification, so `A-WALL-N` and
`A-WALL-D` both give `IfcWall`. The fields are then tried in this order:

**1. Tags, dimensions and hatching.** A minor group like `IDEN`, `PATT`, `DIMS`
or `NOTE` overrides whatever it labels — `A-WALL-IDEN` is wall *tags* and
`A-WALL-PATT` is poché, so both are `IfcAnnotation`, not `IfcWall`. This is the
single biggest source of wrong guesses if you match on substrings alone.

**2. Details, elevations and sections.** `A-DETL-*`, `A-ELEV-*`, `A-SECT-*` are
drafted linework throughout, including their weight grades (`DARK`, `HDDN`,
`MEDM`, `THIN`).

**3. Major/minor pairs that change meaning.** `A-CLNG-LITE` is a light fixture,
not a ceiling; `A-CLNG-HVAC` is an air terminal; `A-GLAZ-MULL` is
`IfcMember` / `MULLION`.

**4. CAD layer codes.** `GLAZ`, `CLNG`, `FNDN`, `HRAL`, `STRS`, `TPTN` are
office conventions no schema can know, so a curated table in
[`core/mapping.py`](core/mapping.py) is consulted next. Fields are tried from
most specific to least, so a minor group still refines its major group. Many
rows carry a predefined type — `A-FLOR-HRAL` gives `IfcRailing` / `HANDRAIL`.

**5. The project's own schema.** Anything the table does not recognise is scored
against every concrete `IfcProduct` subtype in the schema, matching the layer's
words against class names and `PredefinedType` values. This covers the long tail
— `M-BOILER`, `M-CHILLER`, `E-TRANSFORMER`, `S-TENDON`, `M-COOLINGTOWER` — with
nothing hard-coded.

Against the full National CAD Standard v5 layer list (1455 codes), this places
99% of Architectural and 100% of Interiors layers. Civil and survey groups
— easements, parking lots, topography, property lines — are deliberately left
on the `IfcBuildingElementProxy` fallback: no IFC building element fits them,
and saying so is better than guessing.

Only class and predefined-type *names* are matched, never description prose.
Scoring layer descriptions as free text was tried and measurably made things
worse — "Water supply" scored to `IfcBoiler`, "Fences: barbed wire" to
`IfcTendon`, "Roadways: fire lane" to `IfcFireSuppressionTerminal` — so it is
not used.

The class list comes from the schema that ifcopenshell already carries, not from
a bundled copy of the IFC documentation, so there is no data file to keep in
sync, nothing is fetched at run time, and the candidates always match the schema
the project is actually written in. An IFC2X3 project offers 69 classes and no
`IfcBoiler`; IFC4 offers 150; IFC4X3 offers 187. Building the index takes about
10 ms and is cached per schema.

Spatial structure — `IfcProject`, `IfcSite`, `IfcBuilding`, `IfcBuildingStorey`
— is deliberately excluded, since that tree belongs to the project rather than
to a layer. `IfcSpace` is not excluded, so an `A-AREA` layer can become one.

## Units

The DXF-to-metre scale comes from the file's `$INSUNITS` header. Missing or
unrecognised values fall back to millimetres.

That scale is then divided by the project's own length unit, because an IFC
stores lengths in project units rather than metres. Writing metres into a
millimetre project puts the whole drawing inside a couple of metres, and the
geometry becomes too small for the tessellator to subdivide, which reads as
coarse faceting rather than as a scale error.

Arcs and elliptical arcs are trimmed by **endpoint**, not by angle
(`MasterRepresentation` is `CARTESIAN`). A conic's trim parameters are angles
in the project's `PLANEANGLEUNIT`, and Bonsai does not always declare one; with
no unit present IfcOpenShell takes the complementary arc, turning a 60 degree
door swing into 300. Cartesian trims do not depend on a unit that may be
missing. The angles are still written alongside the points for readers that
prefer `PARAMETER`.

## Entity mapping

| DXF entity | IFC geometry |
|---|---|
| `LINE` | `IfcPolyline` (2 pts) |
| `LWPOLYLINE` | `IfcPolyline` / `IfcTrimmedCurve` (bulge) |
| `POLYLINE` | `IfcPolyline` |
| `ARC` | `IfcTrimmedCurve` |
| `CIRCLE` | `IfcCircle` |
| `ELLIPSE` | `IfcEllipse`, trimmed when partial |
| `SPLINE` | `IfcPolyline` (flattened along the curve) |
| `HATCH` | `IfcPolyline` per boundary path, closed |
| `TEXT` / `MTEXT` | `IfcTextLiteral` |

`INSERT` entities are expanded inline and **recursively**: a block holding
another block is followed all the way down, to a depth limit of 16 that only a
malformed file would reach. The contents are converted to world-space geometry
and grouped under each entity's own layer — so geometry from a nested block
appears under the layer its own entities name, not the layer of the outer
block reference. Block instances therefore do not survive as `IfcMappedItem`;
`converter.py` has `block_to_representation_map` and `insert_to_mapped_item`
for that, but the import pipeline does not currently use them.

`ELLIPSE` is trimmed whenever the DXF gives it a start or end parameter, which
in practice is almost always. An untrimmed elliptical arc is drawn as the whole
ellipse, and since a shallow arc belongs to a very large ellipse, that replaces
a small sliver with a figure hundreds of times the size of its surroundings.

`SPLINE` is evaluated along the curve to a tolerance of 0.2% of its own size.
Its control points are a hull the curve is pulled towards rather than points on
it, so they are used only if evaluation fails.

Unsupported entity types are skipped silently. The `DEFPOINTS` layer is always
skipped. An import that yields no geometry at all raises an error.

## Layer styles

Entities are grouped by layer, and each layer's items become one
`IfcGeometricCurveSet` carrying an `IfcPresentationLayerWithStyle` with:
- `IfcCurveStyle` (colour, width, linetype pattern)
- Colour resolved from ACI index or 24-bit true color
- Width from the DXF lineweight (hundredths of a mm), default `0.25` mm,
  converted to the project's length unit like any other length
- `IfcCurveStyleFont` for known dashed linetypes; none for `CONTINUOUS`

## Dependencies

- `ezdxf` — DXF parsing
- `ifcopenshell` — IFC model manipulation
- `ifcopenshell.api` — context creation, spatial assignment

## Structure

```
dxf_ifc/
├── __init__.py       # Blender registration
├── operator.py       # Blender operator (file browser + options)
├── ui.py             # Sidebar panel
├── templates/
│   └── layer_mapping.csv   # shipped starting mapping
├── NOTES.md          # lessons learned; update it with the fix
└── core/
    ├── __init__.py   # Public API
    ├── importer.py   # Main pipeline (no bpy)
    ├── converter.py  # DXF entity → IFC geometry
    ├── mapping.py    # layer → IFC class rules, CSV read/write
    └── styles.py     # ACI colours, lineweight, linetype patterns
```

`core/` never imports `bpy`. Element creation is passed in as a callback, so the
Blender side owns object creation while the geometry pipeline stays testable
outside Blender.

`importer._expand()` is the only block expansion in the add-on. The layer
scanner behind the Source dropdown and the mapping-template generator both call
it, so the layers offered, the layers a template lists, and the layers an import
produces cannot drift apart. They did, twice, when each kept its own copy.
