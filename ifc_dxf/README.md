# ifc_dxf: DXF Generation

Export an IFC drawing view to DXF with maximum structural and geometric fidelity.
Implementation: pure Python (`ezdxf` + `shapely` + `ifcopenshell`). No OCC dependency.

| Pipeline | Status | Approach |
|----------|--------|----------|
| **A — Approximate** | ✓ implemented | IFC-native traversal: 2D reprs as BLOCK/INSERT, sections via Shapely profile extraction |
| **B — (to be defined)** | ✗ future | — |

---

## Shared Reference

### IFC Compatibility

- Works with all IFC versions supported by IfcOpenShell.
- Available `RepresentationContext` values are retrieved at runtime; no version hardcoding.

### RepresentationIdentifier (IFC4)

Each `IfcShapeRepresentation` has a `RepresentationIdentifier` field:

| Identifier | Type | Pipeline A handling |
|---|---|---|
| `Body` | 3D solid / swept solid / BRep | Bucket B (Shapely profile) or Bucket C |
| `FootPrint` | 2D plan projection | **Bucket A** |
| `Axis` | Axis or centre line | **Bucket A** (beams, columns) |
| `Profile` | 3D cross-section | Bucket B (if extrusion ‖ camera) |
| `Annotation` | 2D annotations | **Bucket D** |
| `Reference` / `Surface` / `Box` / `CoG` / `Clearance` / `Lighting` | — | Ignored / Bucket C |

**Selection priority for plan view** (descending):
1. `Plan / Body / PLAN_VIEW` → Bucket A
2. `Plan / Body / MODEL_VIEW` → Bucket A
3. `Model / Body / PLAN_VIEW` → Bucket A
4. `Model / Body / MODEL_VIEW` → Bucket A
5. `FootPrint / PLAN_VIEW` → Bucket A
6. No 2D representation → Bucket B or C

### Sub-context: TargetView

```
IfcGeometricRepresentationContext   (ContextType = "Model" | "Plan")
  └─ IfcGeometricRepresentationSubContext
         ├─ ContextIdentifier  →  "Body", "FootPrint", "Axis", …
         └─ TargetView         →  IfcGeometricProjectionEnum
```

| TargetView | Description |
|---|---|
| `PLAN_VIEW` | Plan view (from above) |
| `REFLECTED_PLAN_VIEW` | Reflected ceiling plan |
| `SECTION_VIEW` | Vertical section |
| `ELEVATION_VIEW` | External elevation |
| `MODEL_VIEW` | Generic 3D / axonometric |
| `GRAPH_VIEW` | Schematic / axis |

### DXF Coordinate System

- Real metres 1:1. `$INSUNITS = 6` (Metres), `$MEASUREMENT = 1` (Metric).
- Camera centre → drawing origin (0, 0). Y increases upward.
- `$LTSCALE`: numeric scale factor (e.g. 0.01 for 1:100). Read from `EPset_Drawing.Scale` (format `"1/100"`).

### DXF Template

`ifc_dxf_template_metric.dxf` (maintained in BricsCAD):
- Layers, annotative text styles, dimstyle `dimensions_metric_m`.
- A1 layout with 1:100 viewport and title block (cartiglio): `{{Identification}}`, `{{Name}}`, `{{scale}}`, `{{date}}`.
- Annotation scale in viewport via XREC `ASDK_XREC_ANNOTATION_SCALE_INFO`.

At export: `ezdxf.readfile()` + `msp.delete_all_entities()`. `_fill_cartiglio()` updates title block fields, viewport height/centre, and annotation scale XREC.

Current annotation scale written to `AcDbVariableDictionary → DictionaryVariables("CANNOSCALE", "1:100")`, not to `$CANNOSCALE` (ignored by BricsCAD/AutoCAD).

The scale list (`ACAD_SCALELIST`) uses `SCALE`/`AcDbScale` entities (group codes 300/140/141/290) not natively supported by ezdxf; workaround via `new_entity("SCALE")` + manual subclass setup.

### Layer Naming

| Type | Format | Example |
|---|---|---|
| Bucket A — INSERT elements | IFC class name | `IfcDoor`, `IfcFurniture` |
| Bucket A — overhead fill | `<Class>_Overhead` | `IfcWindow_Overhead` |
| Bucket B — section cut | `<Class>_Section` | `IfcWall_Section` |
| Bucket B — view (below cut) | `<Class>_View` | `IfcWall_View` |
| Bucket B — hatch fill | `<Class>_Hatches` | `IfcWall_Hatches` |
| Geometry inside BLOCKs | `"0"` with BYBLOCK | controlled by INSERT |

---

## Pipeline A — Approximate

No OCC / HLR. Geometry quality depends on what the IFC file provides.
Ifc semantics is taken into account to decide how to process elements.

---

### View specific rules for IfcElements

#### Plan View

The active drawing is an `IfcAnnotation` with `EPset_Drawing`. Its camera defines an orthographic frustum looking down at a horizontal cut plane at height `cut_z`. All geometry is projected to the drawing's 2D space via the camera inverse matrix.

Custom Section or viewed: IfcWall IfcWallStandardCase, IfcColumn
Only 2D representation: IfcWindow, IfcFurniture, IfcSanitaryTerminal.

#### Sections
To be implemented

#### Elevations
To be implemented

#### Details
To be implemented

#### Schedules
To be implemented

---

### Element selection according to the view limits

**Input:** the element list from `tool.Drawing.get_drawing_elements()` (Blender/Bonsai) or from spatial culling in standalone mode (`get_elements` in `ifc_query.py`).

**Frustum 3D bbox culling:** a world-space bounding box `(x_min, x_max, y_min, y_max, z_min, z_max)` is derived from the camera body geometry (`IfcCsgSolid` / `IfcExtrudedAreaSolid`) transformed by the camera's `ObjectPlacement`. Each element's `ObjectPlacement` origin is tested against this box. Elements outside are excluded.

**Known limitation:** `element_in_frustum` tests only the `ObjectPlacement` origin point. Elements whose origin is outside the frustum but whose body extends into view (e.g. a long wall starting outside) may be incorrectly excluded. A full AABB fallback is planned.

**Overhead fill re-addition:** windows/doors that fill openings entirely above `cut_z` are excluded by frustum culling (`z_max = cut_z` for the camera box) but must appear as overhead elements. In `export_drawing`, before `classify_elements` is called, walls/columns are inspected for openings with `z_min_opening > cut_z`; any filling elements not already in the element list are re-added.

---

### Geometry extraction (buckets)

Each element is routed to the highest applicable bucket:

```
element
  ├─ has 2D plan representation? ──────────────────── Bucket A
  ├─ is a section class (IfcWall, …)? ─────────────── Bucket B
  └─ neither ──────────────────────────────────────── Bucket C
```

Annotations (`IfcAnnotation` children of the drawing group) are processed separately as Bucket D.

#### Bucket A — Native 2D Representation

**Condition:** a plan repr (`Plan/Body/PLAN_VIEW` or equivalent) exists on the element or its IFC type (see priority table in Shared Reference).

**Geometry extraction:** direct IFC tree traversal (`IfcMappedItem`, `IfcCompositeCurve`, `IfcTrimmedCurve`, `IfcIndexedPolyCurve`, …). Fallback to `ifcopenshell.geom.create_shape` with `use-world-coords=False` when direct traversal yields nothing.

**Exact curve mapping** (not tessellated):

| IFC curve | DXF entity |
|---|---|
| `IfcPolyline`, `IfcIndexedPolyCurve` (LineIndex) | `LINE` |
| `IfcIndexedPolyCurve` (ArcIndex) | `ARC` |
| `IfcTrimmedCurve` (IfcCircle) | `ARC` or `CIRCLE` |
| `IfcTrimmedCurve` (IfcEllipse, equal axes) | `ARC` |
| `IfcTrimmedCurve` (IfcEllipse, unequal axes) | `ELLIPSE` |
| `IfcCircle` | `CIRCLE` |

**Tessellated meshes (`IfcPolygonalFaceSet`):** only *crease edges* retained — naked edges (1 adjacent face) and shared edges with dihedral angle > `MESH_CREASE_ANGLE_DEG` (15°). Prevents triangle flooding for terrain (`IfcGeographicElement[TERRAIN]`).

**Bonsai bug note:** door arcs exported as `IfcEllipse` instead of `IfcCircle`. Workaround in `_trimmed_ellipse_spec`. Upstream PR pending.

#### Bucket B — Section from 3D

**Condition:** element class is in `_SECTION_CLASSES` (`IfcWall`, `IfcWallStandardCase`, `IfcColumn`; future: `IfcStairFlight`).

> `IfcSlab`/`IfcCovering`/`IfcRoof` use a footprint path in Bucket A, not Bucket B.

**Algorithm:**
1. Extract 2D profile from `IfcExtrudedAreaSolid` (`IfcArbitraryClosedProfileDef`, `IfcRectangleProfileDef`).
2. Project to drawing space.
3. Subtract `IfcOpeningElement` footprints.
4. Group polygons by `(ifc_class, material)`.
5. Shapely union with 0.5 mm snap tolerance.

**Boolean clipping:** `IfcBooleanClippingResult` chains are fully walked. Each clipping operand is applied as a 2D Shapely difference after projecting to camera space:
- `IfcPolygonalBoundedHalfSpace` → boundary polygon projected and subtracted.
- `IfcHalfSpaceSolid` → half-plane rectangle constructed from plane origin + normal.

Only clipping planes whose normal is perpendicular to the view axis (`|normal_cam.z| ≤ 0.1`) are processed; tilted planes are skipped (2D approximation is only valid for vertical planes).

**Limitation:** vertical extrusion only. Does not handle BRep or non-vertical elements.

#### Bucket C — No geometry

Elements with no usable representation. Counted in the export log, not drawn.

Future: wireframe fallback from projected 3D Body.

---

### Visibility rules in exported drawing

**Slab occlusion:** before assigning a Bucket A element, check whether it is hidden under a floor slab. For each `IfcSlab` / `IfcCovering(FLOOR)` with `Z_top ≤ cut_z`: if the element's XY origin falls inside the slab footprint and `z_element < z_top`, the element is sent to Bucket C.

**Section vs View (Bucket B):** a section-class element whose Z range straddles `cut_z` is *cut* → layer `<Class>_Section` (thick line + hatch). An element entirely below `cut_z` is *viewed* → layer `<Class>_View` (thin line, no hatch).

**Overhead fill (Bucket A):** windows/doors re-added after culling (see Element selection) are placed on layer `<Class>_Overhead` with dashed linetype, to indicate they are above the cut plane.

---

### DXF export rules

#### Bucket A output — three paths

**1. Shared BLOCK + INSERT** — geometry comes from the IFC type:
- `from_type=True` (repr found in type's `RepresentationMaps`), or
- `is_mapped_repr=True` (element's own repr consists entirely of `IfcMappedItem` delegating to the type).

Multiple instances of the same type share one BLOCK named `{TypeName}_{TypeGlobalId[:8]}`.

**2. Footprint LWPOLYLINE + GROUP** — `IfcSlab`, `IfcCovering`, `IfcRoof` with instance-specific geometry. Profile extracted from `IfcExtrudedAreaSolid` (through `IfcBooleanResult` chain), projected to drawing space, written as closed `LWPOLYLINE`. Each element gets a GROUP `fp_{GlobalId[:8]}`; interior rings produce additional LWPOLYLINEs in the same GROUP. Scoped to these classes to avoid capturing the Body solid of doors/windows that also contain `IfcExtrudedAreaSolid`.

**3. Unique BLOCK + INSERT** — all other elements with instance-specific geometry: one BLOCK per instance, named `{IfcClass}_{GlobalId[:8]}`. Preserves exact arcs and circles from the 2D plan symbol.

**Key rule:** `GlobalId` as block identifier, never `Name` (non-unique).

#### BLOCK geometry rules

Entities on layer `"0"`, `color=0` (BYBLOCK), `linetype="BYBLOCK"`, `lineweight=-2` (BYBLOCK). The INSERT entity controls all appearance properties.

#### INSERT placement

- **Position:** world-space origin of `ObjectPlacement` projected via `cam_inv_np`.
- **Rotation:** angle (degrees CCW) of the element's local X axis in world XY — computed in world space, not camera space. The BLOCK geometry already has `R_cam` baked in; reapplying it in camera space would double-rotate.

#### Bucket B output

Closed `LWPOLYLINE` → `<Class>_Section` or `<Class>_View`. Solid `HATCH` → `<Class>_Hatches` (sectioned elements only).

#### Annotations (Bucket D)

**Discovery:** match the drawing's `GlobalId` in `RelatedObjects` of every `IfcRelAssignsToGroup` whose group has `ObjectType='DRAWING'`. The drawing `IfcAnnotation` is itself a member of its group. Match by GUID — group `Name` may differ from drawing `Name`.

```python
drawing_guid = drawing.GlobalId
for rel in ifc.by_type("IfcRelAssignsToGroup"):
    group = rel.RelatingGroup
    if not (group.is_a("IfcGroup") and getattr(group, "ObjectType", None) == "DRAWING"):
        continue
    if not any(getattr(obj, "GlobalId", None) == drawing_guid for obj in rel.RelatedObjects):
        continue
    for obj in rel.RelatedObjects:
        if obj.is_a("IfcAnnotation") and obj.id() != drawing.id():
            annotations.append(obj)
```

**D1 — DIMENSION:** `IfcGeometricCurveSet → IfcIndexedPolyCurve → IfcCartesianPointList2D`. Endpoints projected → DXF `DIMENSION` with `distance=0`. Dimstyle `dimensions_metric_m`; parameters in paper-space metres (`paper_mm * 0.001`); `dimscale = 1 / scale_factor`. Each entity receives `AcadAnnotative` XDATA for BricsCAD/AutoCAD annotative scaling.

**D2 — TEXT:**

| CSS Style | Paper (mm) | Model-space at 1:100 |
|---|---|---|
| `title` | 7.0 | 0.70 m |
| `header` | 5.0 | 0.50 m |
| `large` | 3.5 | 0.35 m |
| `regular` | 2.5 | 0.25 m |
| `small` | 1.8 | 0.18 m |

`txt_height = paper_mm * 0.001 / scale_factor`. Bonsai CSS box-align → DXF `halign`/`valign`.

**D3 — Other (future):** symbols, hatches, Bonsai SVG markers.

---

## Pipeline B — (to be defined)

Placeholder. Goals and open questions to be established.

---

## TODO / Roadmap

**Pipeline A:**
1. Add `IfcStairFlight` to Bucket B section path.
2. Report elements with deep boolean chains (> threshold) during export.
3. Frustum culling AABB fallback: test full geometry bounding box when origin test fails.
4. Material-agnostic wall fusion option (`unary_union` regardless of material).
5. Per-layer fusion for `IfcMaterialLayerSet` walls (decompose polygon into layer strips).
6. Bucket D — D3: symbols, markers, hatches.
7. More IFC test fixtures (rotated walls, overhead elements, text annotations, sections, different scales).

**Upstream:**
8. PR ezdxf: native `SCALE`/`AcDbScale` entity type (group codes 300/140/141/290).
9. PR Bonsai: fix door arc exported as `IfcEllipse` instead of `IfcCircle`.

**Future pipelines:**
10. B-Accurate (OCC/HLR): precise linework via ifcopenshell geom serializer.
11. Section view / Elevation: non-zenithal camera logic.
12. Reflected Ceiling Plan, Axonometric.

---

## Implementation Notes

### Camera Projection

Orthographic. Camera inverse matrix transforms world points → camera-local.

- `R_cam` (rotation only) → for BLOCK geometry in local coordinates.
- `cam_inv_np` (rotation + translation) → for INSERT origins and annotation points.

### INSERT Rotation

Computed as `atan2(local_x_world.y, local_x_world.x)` in world XY, not in camera space. The BLOCK geometry already has `R_cam` baked in; applying it again in camera space would double-rotate.

### IFC Model Quality Requirements (Pipeline A)

B-Approximate and the footprint/occlusion filters require:
- Correct Z coordinates in `ObjectPlacement`.
- `IfcExtrudedAreaSolid` profiles with vertical extrusion axis.
- `IfcSlab` / `IfcCovering(FLOOR)` with correct Z for occlusion filtering.

Without OCC, an imprecise model produces incorrect results without explicit errors.
