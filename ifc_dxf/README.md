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

### Concept

Each IFC element is routed to the highest applicable bucket. No OCC / HLR. Geometry quality depends on what the IFC file provides.

```
element
  │
  ├─ has 2D plan representation? ──────────────────── Bucket A (native 2D)
  │
  ├─ is a section class (IfcWall, …)? ─────────────── Bucket B (Shapely profile)
  │
  └─ neither ──────────────────────────────────────── Bucket C (skipped)

Annotations (IfcAnnotation children of the drawing group) ── Bucket D
```

### Bucket A — Native 2D Representation

**Condition:** a plan repr (`Plan/Body/PLAN_VIEW` or equivalent) exists on the element or its type.

**Pre-filters applied before Bucket A geometry extraction:**

- **Slab occlusion:** element origin is below a slab (`IfcSlab` / `IfcCovering(FLOOR)`) whose footprint covers the origin in XY → element sent to Bucket C (hidden under slab).
- **Overhead fill:** windows/doors filling openings entirely above the cut plane (`z_min_opening > cut_z`) are shown on `<Class>_Overhead` layer with dashed linetype. Explicitly re-added after frustum culling since the camera frustum has `Z_max = cut_z`.

**Geometry extraction:** direct IFC tree traversal (`IfcMappedItem`, `IfcCompositeCurve`, `IfcTrimmedCurve`, `IfcIndexedPolyCurve`, …). Fallback: `ifcopenshell.geom.create_shape` with `use-world-coords=False`.

**Exact DXF entities** (not tessellated):

| IFC curve | DXF entity |
|---|---|
| `IfcPolyline`, `IfcIndexedPolyCurve` (LineIndex) | `LINE` |
| `IfcIndexedPolyCurve` (ArcIndex) | `ARC` |
| `IfcTrimmedCurve` (IfcCircle) | `ARC` or `CIRCLE` |
| `IfcTrimmedCurve` (IfcEllipse, equal axes) | `ARC` |
| `IfcTrimmedCurve` (IfcEllipse, unequal axes) | `ELLIPSE` |
| `IfcCircle` | `CIRCLE` |

**Tessellated meshes (`IfcPolygonalFaceSet`):** only *crease edges* are kept — naked edges (1 adjacent face) and shared edges whose dihedral angle exceeds `MESH_CREASE_ANGLE_DEG` (15°). Prevents triangle-edge flooding for terrain meshes (`IfcGeographicElement[TERRAIN]`).

**Bonsai bug note:** door opening arcs are exported as `IfcEllipse` instead of `IfcCircle`. Workaround active in `_trimmed_ellipse_spec`. Upstream PR pending.

**Output — three paths depending on geometry source:**

1. **Shared BLOCK + INSERT** — type provides 2D geometry (`from_type=True`, or element repr consists entirely of `IfcMappedItem` delegating to the type). Multiple instances share one BLOCK definition named `{TypeName}_{TypeGlobalId[:8]}`.

2. **Footprint LWPOLYLINE + GROUP** — `IfcSlab`, `IfcCovering`, `IfcRoof` without a shared type block. Profile extracted from `IfcExtrudedAreaSolid` (unwrapping `IfcBooleanResult` chains), projected to drawing space, written as closed `LWPOLYLINE`. Grouped per element (`fp_{GlobalId[:8]}`). Interior rings produce additional LWPOLYLINEs in the same GROUP. Scoped to these classes to avoid capturing the `Body` solid of doors/windows.

3. **Unique BLOCK + INSERT** — all other elements with instance-specific geometry: unique BLOCK named `{IfcClass}_{GlobalId[:8]}`. Preserves arcs and circles from the 2D plan symbol.

**Key rule:** `GlobalId` as block identifier, never `Name` (non-unique across types).

**BLOCK geometry:** entities on layer `"0"` with `color=0`, `linetype="BYBLOCK"`, `lineweight=-2` (all BYBLOCK). INSERT controls colour/linetype/lineweight.

**INSERT position:** projection of the world-space origin of `ObjectPlacement` via camera inverse matrix.

**INSERT rotation:** angle (degrees CCW) of the element's local X axis in world XY. Computed in world space, not camera space — BLOCK geometry already has `R_cam` baked in.

### Bucket B — Section from 3D (B-Approximate)

**Condition:** element is in a section class (`IfcWall`, `IfcWallStandardCase`; future: `IfcColumn`, `IfcStairFlight`).

> `IfcSlab` is handled via the Bucket A footprint path, not Bucket B.

**Algorithm:**
1. Extract 2D wall profile (`IfcArbitraryClosedProfileDef`, `IfcRectangleProfileDef`).
2. Project to drawing space (camera matrix).
3. Subtract opening footprints (`IfcOpeningElement`).
4. Group polygons by `(ifc_class, material)`.
5. Shapely union with 0.5 mm snap tolerance.
6. Write closed `LWPOLYLINE` → `<Class>_Section` or `<Class>_View`.
7. Write solid `HATCH` → `<Class>_Hatches` (sectioned elements only).

**Boolean unwrapping:** `IfcBooleanResult` chains unwrapped up to `BOOLEAN_UNWRAP_DEPTH_LIMIT` (64) to reach the base `IfcExtrudedAreaSolid`. Elements exceeding the limit silently skip to Bucket C.

**Limitation:** requires vertical extrusion and a well-formed IFC profile. Does not handle BRep or non-vertical elements.

### Bucket C — Skipped

Elements with no usable representation, or occluded by a slab. Counted in export log but not drawn.

Future: wireframe fallback from projected 3D Body.

### Bucket D — Annotations

**Discovery:** match the drawing's `GlobalId` in `RelatedObjects` of every `IfcRelAssignsToGroup` whose `RelatingGroup` is `IfcGroup(ObjectType='DRAWING')`. The drawing `IfcAnnotation` is itself a member of the group. Match by GUID, not by `Name` — the group name may differ from the drawing name.

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

**Coordinate projection:** 3D annotation geometry → 2D via `cam_inv_np`.

#### D1 — DIMENSION

Geometry: `IfcGeometricCurveSet → IfcIndexedPolyCurve → IfcCartesianPointList2D`. Endpoints projected and written as DXF `DIMENSION` with `distance=0`.

Dimstyle `dimensions_metric_m` (from template). Parameters in paper-space metres: `paper_mm * 0.001`. `dimscale = 1 / scale_factor` (e.g. 100 for 1:100). Fallback `BONSAI_DIM` (oblique ticks) if template unavailable.

Each `DIMENSION` entity receives `AcadAnnotative` XDATA so BricsCAD/AutoCAD treat it as annotative.

#### D2 — TEXT

| CSS Style | Paper (mm) | Model-space at 1:100 |
|---|---|---|
| `title` | 7.0 | 0.70 m |
| `header` | 5.0 | 0.50 m |
| `large` | 3.5 | 0.35 m |
| `regular` | 2.5 | 0.25 m |
| `small` | 1.8 | 0.18 m |

Formula: `txt_height = paper_mm * 0.001 / scale_factor`. Alignment mapped from Bonsai CSS box-align to DXF `halign`/`valign`.

#### D3 — Other (future)

Symbols, hatches, Bonsai SVG markers.

### Limitations

- Requires correct Z coordinates and `IfcExtrudedAreaSolid` profiles (B-Approximate, footprint).
- No OCC: partial occlusion not handled — element shown fully or excluded fully.
- `element_in_frustum` tests only `ObjectPlacement` origin — elements whose origin is outside the frustum but whose body extends into view may be incorrectly culled (known issue).

---

## Pipeline B — (to be defined)

Placeholder. Goals and open questions to be established.

---

## TODO / Roadmap

**Pipeline A:**
1. Add `IfcColumn`, `IfcStairFlight` to Bucket B section path.
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
