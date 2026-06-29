# dxf_ifc

Import DXF geometry as an IFC representation on a selected Bonsai element.

## What it does

Reads a `.dxf` file and assigns its contents as an `IfcShapeRepresentation`
(type `GeometricCurveSet`) on the active IFC element in Bonsai.
The target subcontext is `Plan / Annotation / PLAN_VIEW` by default,
but can be changed from the panel before importing.

Any existing representation in the chosen subcontext is removed and replaced.

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
| `HATCH` | `IfcPolyline` (outer boundary only) |
| `INSERT` | `IfcMappedItem` |
| `TEXT` / `MTEXT` | `IfcTextLiteral` |

## Layer styles

Each DXF layer becomes an `IfcPresentationLayerWithStyle` with:
- `IfcCurveStyle` (colour, linewidth, linetype pattern)
- Colour resolved from ACI index or 24-bit true color
- Lineweight converted from DXF hundredths-of-mm to metres

## Panel options

| Option | Description |
|---|---|
| Context | `Plan` (2D) or `Model` (3D) |
| Subcontext | Identifier string, default `Annotation` |
| Target View | e.g. `PLAN_VIEW`, `SECTION_VIEW` |
| Write Pset_DXFSource | Optionally attach DXF metadata (source file, layer, colour, lineweight) as a property set |

## Dependencies

- `ezdxf` — DXF parsing
- `ifcopenshell` — IFC model manipulation
- `ifcopenshell.api` — geometry assignment / context creation

## Structure

```
dxf_ifc/
├── __init__.py       # Blender registration
├── operator.py       # Blender operator + PropertyGroup
├── ui.py             # Sidebar panel
└── core/
    ├── __init__.py   # Public API
    ├── importer.py   # Main pipeline (no bpy)
    ├── converter.py  # DXF entity → IFC geometry
    └── styles.py     # ACI colours, lineweight, linetype patterns
```
