# dxf_ifc

Import DXF geometry as an IFC representation on a Bonsai element.

## What it does

Reads a `.dxf` file and assigns its contents as an `IfcShapeRepresentation`
(`RepresentationIdentifier` `Annotation`, `RepresentationType`
`GeometricCurveSet`) on an IFC element — either the active one or a newly
created `IfcAnnotation`.

The geometry is written into the IFC file, not as Blender meshes, so it
normally appears in the 2D drawing view rather than the 3D model view.

Any existing representation in the chosen subcontext is removed and replaced.
Representations in other subcontexts are left alone.

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
| Layer | Only entities on one layer of modelspace |

The Block and Layer dropdowns are filled by scanning the file at the current
filepath, which happens when you switch to that mode — so browse to the `.dxf`
first, otherwise the lists read `— scan a DXF file first —`.

### Destination

| Option | Description |
|---|---|
| Active element | Attach to the selected IFC element |
| New annotation | Create an `IfcAnnotation` (`LINEWORK`) named after the file, written to `Plan / Annotation / PLAN_VIEW` |

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

## Units

The DXF-to-metre scale comes from the file's `$INSUNITS` header. Missing or
unrecognised values fall back to millimetres.

## Entity mapping

| DXF entity | IFC geometry |
|---|---|
| `LINE` | `IfcPolyline` (2 pts) |
| `LWPOLYLINE` | `IfcPolyline` / `IfcTrimmedCurve` (bulge) |
| `POLYLINE` | `IfcPolyline` |
| `ARC` | `IfcTrimmedCurve` |
| `CIRCLE` | `IfcCircle` |
| `ELLIPSE` | `IfcEllipse` |
| `SPLINE` | `IfcPolyline` (control-point approximation) |
| `HATCH` | `IfcPolyline` per boundary path, closed |
| `TEXT` / `MTEXT` | `IfcTextLiteral` |

`INSERT` entities are expanded inline: the block's contents are converted to
world-space geometry and grouped under each virtual entity's own layer. Block
instances therefore do not survive as `IfcMappedItem` — `converter.py` has
`block_to_representation_map` and `insert_to_mapped_item` for that, but the
import pipeline does not currently use them.

Unsupported entity types are skipped silently. The `DEFPOINTS` layer is always
skipped. An import that yields no geometry at all raises an error.

## Layer styles

Entities are grouped by layer, and each layer's items become one
`IfcGeometricCurveSet` carrying an `IfcPresentationLayerWithStyle` with:
- `IfcCurveStyle` (colour, width, linetype pattern)
- Colour resolved from ACI index or 24-bit true color
- Width in mm, from the DXF lineweight (hundredths of a mm), default `0.25`
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
└── core/
    ├── __init__.py   # Public API
    ├── importer.py   # Main pipeline (no bpy)
    ├── converter.py  # DXF entity → IFC geometry
    └── styles.py     # ACI colours, lineweight, linetype patterns
```
