# ifc_dxf: DXF Generation Rules

A tool to export an IFC drawing view to DXF, aiming for maximum structural and geometric fidelity.
> Implementation: pure Python (`ezdxf` + `shapely` + `ifcopenshell`). No OCC dependency in ifc_dxf.

---

## IFC Compatibility

- Works with all IFC versions available in IfcOpenShell.
- Available `RepresentationContext` values are retrieved at runtime (each IFC file declares its own sub-contexts), with no version hardcoding.

---

## RepresentationIdentifier (IFC4)

Each `IfcShapeRepresentation` has a `RepresentationIdentifier` field that indicates the role of the representation:

| Identifier | Type | Use in ifc_dxf |
|---|---|---|
| `Body` | 3D solid / swept solid / BRep | Bucket B (if IfcExtrudedAreaSolid ‖ camera) or Bucket C |
| `FootPrint` | 2D plan projection | **Bucket A** (preferred for plan view) |
| `Axis` | Axis or centre line | **Bucket A** (e.g. beams, columns) |
| `Profile` | 3D cross-section | Bucket B (if extrusion ‖ camera) |
| `Reference` | Reference geometry | Ignored |
| `Surface` | External surface | Bucket C |
| `Annotation` | 2D annotations | **Bucket D** (`annotations` module) |
| `Box` | Bounding box | Ignored |
| `CoG` | Centre of gravity | Ignored |
| `Clearance` | Manoeuvre space | Ignored |
| `Lighting` | Render geometry | Ignored |

**Selection priority for plan view** (descending order):
1. `Plan / Body / PLAN_VIEW` → **Bucket A**
2. `Plan / Body / MODEL_VIEW` → **Bucket A**
3. `Model / Body / PLAN_VIEW` → **Bucket A**
4. `Model / Body / MODEL_VIEW` → **Bucket A**
5. `FootPrint / PLAN_VIEW` → **Bucket A**
6. No 2D representation → **Bucket B** or **C**

---

## Sub-context: TargetView (`IfcGeometricProjectionEnum`)

```
IfcGeometricRepresentationContext   (ContextType = "Model" | "Plan")
  └─ IfcGeometricRepresentationSubContext
         ├─ ContextIdentifier  →  "Body", "FootPrint", "Axis", …
         └─ TargetView         →  IfcGeometricProjectionEnum
```

| TargetView | Description | ifc_dxf view |
|---|---|---|
| `PLAN_VIEW` | Plan view (from above) | **Plan view** |
| `REFLECTED_PLAN_VIEW` | Reflected plan (ceilings) | RCP |
| `SECTION_VIEW` | Vertical section | Section view |
| `ELEVATION_VIEW` | External elevation | Elevation |
| `MODEL_VIEW` | Generic 3D view | Axonometric |
| `GRAPH_VIEW` | Schematic representation | Axis, reference |
| `SKETCH_VIEW` | Approximate sketch | Not used |

---

## DXF Structure — Fundamental Rule

Reproduce the IFC structure in DXF entities as faithfully as possible:

| IFC | DXF |
|-----|-----|
| IFC type with 2D repr (`IfcDoorType`, `IfcFurnitureType`, …) | A shared **BLOCK** (name = `GlobalId` of the type) |
| IFC element instance (type has 2D repr) | An **INSERT** |
| Element with no shared type (doors, furniture without type) | Unique **BLOCK** + **INSERT**, name = `{IfcClass}_{GlobalId[:8]}` |
| Flat floor element (`IfcSlab`, `IfcCovering`, `IfcRoof`) | Closed **LWPOLYLINE** in a **GROUP** (`fp_{GlobalId[:8]}`) |

**Key rule:** always use the IFC `GlobalId` as the unique block identifier, not the `Name` field (which may be non-unique, e.g. "Unnamed" for multiple different types).

**Shared block condition:** a shared type block is only used when `from_type=True` (the plan repr comes from the type's `RepresentationMaps` via `IfcMappedItem`) **and** `get_type_block_name` returns a non-`None` name. If the type has no 2D representation in its `RepresentationMaps`, the element falls through to the footprint or unique-block path.

### Placement Rules

- **BLOCK geometry**: in the element/type local coordinates. Entities on layer `"0"` with `color=0` (BYBLOCK), `linetype="BYBLOCK"`, `lineweight=-2` (BYBLOCK). This way the INSERT controls colour, linetype and lineweight.
- **INSERT position**: projection of the world-space origin of the element's `ObjectPlacement`.
- **INSERT rotation**: angle (degrees, CCW) that the element's local X axis makes with the drawing X axis, in world XY (not in camera space — the BLOCK geometry already has R_cam baked in).
- **INSERT layer**: exact IFC class name (e.g. `IfcDoor`, `IfcFurniture`, `IfcWindow_Overhead`).
- **Never** put block geometry in world coordinates with INSERT at (0,0,0).

### Coordinates and DXF Scale

- System: real metres 1:1. `$INSUNITS = 6` (Metres), `$MEASUREMENT = 1` (Metric).
- Camera centre corresponds to (0, 0) in drawing space.
- Y increases upward (standard DXF/mathematical convention).
- `$LTSCALE`: set to the numeric scale factor of the view (e.g. 0.01 for 1:100, 0.02 for 1:50). Read from `EPset_Drawing.Scale` (format `"1/100"`) via `fractions.Fraction`. Default: 0.01.

### DXF Template

The file `ifc_dxf_template_metric.dxf` (maintained manually in BricsCAD) contains:
- Layers, annotative text styles (title/header/large/regular/small/DIMENSION/GRID), dimstyle `dimensions_metric_m` (with dimension arrows and dedicated font).
- A1 layout with a 1:100 viewport and a title block (cartiglio) with fields `{{Identification}}`, `{{Name}}` (in MTEXT, curly braces escaped as `\{\{...\}\}`), `{{scale}}`, `{{date}}` (in TEXT).
- Annotation scale in the viewport set to 1:100 (handle in XREC `ASDK_XREC_ANNOTATION_SCALE_INFO`).

At export: `ezdxf.readfile()` + `msp.delete_all_entities()` to clear the model space. `_fill_cartiglio()` fills the title block placeholders, updates the viewport `view_height` and `view_center_point` based on scale, and updates the annotation scale XREC.

### Annotation Scale

The current annotation scale is written to `AcDbVariableDictionary` → `DictionaryVariables("CANNOSCALE", "1:100")`. **Not** to the header `$CANNOSCALE` (ignored by BricsCAD/AutoCAD). ezdxf supports this via `dict.add_dict_var()`.

The scale list (`ACAD_SCALELIST`) is populated with entities of type `SCALE` / `AcDbScale`. ezdxf does not natively know this type (group codes: `300`=name, `140`=paper, `141`=drawing, `290`=1:1 base flag). Workaround: `new_entity("SCALE")` + `DXFTYPE` override via `type()` + manual subclasses.

---

## Bucket Classification (A → D, decreasing priority)

For each element, the classifier picks the highest applicable bucket.

### Bucket A — Native 2D Representation ✓ implemented

**Condition:** a `Plan/Body/PLAN_VIEW` (or equivalent) representation exists in the element or its IFC type.

**Slab occlusion pre-filter:** before processing a Bucket A element, verify it is not hidden by an overlying slab. The filter combines a Z check with a Shapely XY footprint check of the slab, so elements in courtyards, atriums, or at different levels are not incorrectly excluded.

```
For each IfcSlab / IfcCovering(FLOOR) with Z_top ≤ cut_z:
  - extract 2D footprint in world XY (IfcExtrudedAreaSolid → Shapely Polygon)
  - element excluded if: z_element_origin < z_top  AND  origin_XY ∈ footprint
```

**Overhead windows/doors:** elements filling openings entirely above the cut plane (z_min_opening > cut_z) are shown on the layer `<Class>_Overhead` (e.g. `IfcWindow_Overhead`) with a dashed linetype. Since the camera frustum has Z_max = cut_z, these elements are explicitly re-added to the processing list after frustum culling.

**Geometry source:** direct Python traversal of the IFC tree (`IfcMappedItem`, `IfcCompositeCurve`, `IfcTrimmedCurve`, `IfcIndexedPolyCurve`, etc.). Fallback: `ifcopenshell.geom.create_shape` with `use-world-coords=False`.

**DXF entities produced** (exact, not tessellated):
- `IfcPolyline`, `IfcIndexedPolyCurve` (IfcLineIndex) → `LINE`
- `IfcIndexedPolyCurve` (IfcArcIndex) → `ARC`
- `IfcTrimmedCurve` (IfcCircle) → `ARC` or `CIRCLE`
- `IfcTrimmedCurve` (IfcEllipse, equal axes) → `ARC`
- `IfcTrimmedCurve` (IfcEllipse, unequal axes) → `ELLIPSE`
- `IfcCircle` → `CIRCLE`

**Tessellated meshes (`IfcPolygonalFaceSet`):** when the geom engine returns a mesh (faces non-empty), only *crease edges* are kept — naked edges (belonging to exactly one face) and shared edges whose dihedral angle exceeds `MESH_CREASE_ANGLE_DEG` (15°). This avoids flooding the plan view with every interior triangle edge, relevant for terrain (`IfcGeographicElement[TERRAIN]`) represented as a dense mesh.

**Bonsai bug note:** door opening arcs are exported as `IfcEllipse` instead of `IfcCircle`. Active workaround (`_trimmed_ellipse_spec`). To be reported as an upstream PR.

**IFC fidelity principle:** maximum adherence to geometry as described in IFC. Separate arcs are never merged even if contiguous — two `IfcTrimmedCurve` → two `DXF ARC`.

**Output — three paths depending on geometry source:**

1. **Shared BLOCK + INSERT** — element uses a type with a 2D `RepresentationMaps` (`from_type=True`). Block geometry in type's local coordinates; multiple instances share one BLOCK definition.
2. **Footprint LWPOLYLINE + GROUP** — `IfcSlab`, `IfcCovering`, `IfcRoof` without a shared type block. The `IfcExtrudedAreaSolid` profile is extracted, projected to drawing space, and written as a closed `LWPOLYLINE`. Each element gets its own named GROUP (`fp_{GlobalId[:8]}`). Interior rings (holes) produce additional LWPOLYLINEs in the same GROUP. Scoped to these classes specifically to avoid capturing the `Body` solid of doors/windows that also contain `IfcExtrudedAreaSolid`.
3. **Unique BLOCK + INSERT** — all other elements with instance-specific geometry (`from_type=False` and not a footprint class): each gets a unique BLOCK named `{IfcClass}_{GlobalId[:8]}`. Preserves arcs and circles from the 2D plan symbol.

**Limitation:** requires a geometrically correct IFC model with proper Z coordinates. Without OCC, partial occlusion (e.g. a chair partly covered by a slab) is not handled — the element is shown fully or excluded fully.

---

### Bucket B — Section Generated from 3D ✓ partially implemented

**Condition:** no native 2D representation found by Bucket A.

**Typical classes:** `IfcWall`, `IfcWallStandardCase` (future: `IfcSlab`, `IfcColumn`, `IfcStairFlight`, …).

**Output:** entities written directly to model space (no BLOCK/INSERT). Layer with semantic suffix.

#### B-Approximate — Shapely ✓ implemented

Used for elements with `IfcExtrudedAreaSolid` and an extractable 2D profile.

**Algorithm for walls:**
1. Extract the 2D wall profile (`IfcArbitraryClosedProfileDef`, `IfcRectangleProfileDef`).
2. Project the profile into drawing space (camera matrix).
3. Subtract opening footprints (`IfcOpeningElement`).
4. Group polygons by `(ifc_class, material)`.
5. Shapely union with 0.5 mm snap tolerance → clean contours.
6. Write closed `LWPOLYLINE` → layer `IfcWall_Section` or `IfcWall_View`.
7. Write solid `HATCH` → layer `IfcWall_Hatches` (sectioned elements only).

**Layers:**
- `IfcWall_Section`: cut → thick line + hatch
- `IfcWall_View`: elements seen below the cut plane → thin line, no hatch
- `IfcWall_Hatches`: solid fill of sectioned areas

**Boolean unwrapping:** `IfcBooleanResult` chains (one level per opening subtracted from the body) are unwrapped up to `BOOLEAN_UNWRAP_DEPTH_LIMIT` (default 64) levels to reach the base `IfcExtrudedAreaSolid`. A wall with more openings than the limit is silently skipped to Bucket C. *(Previous hard-coded limit was 8; bumped to 64 after finding a real-world wall with 9 nested booleans that was silently dropped.)*

**Note:** B-Approximate requires a well-built IFC model. Does not handle BRep, complex booleans, or non-vertical walls.

#### B-Accurate — OCC/HLR ✗ future work

Uses `ifcopenshell.geom` with HLR (Hidden Line Removal) to generate precise linework. Equivalent to the OCC path in Bonsai SVG. Requires OCC to be available in ifcopenshell.

---

### Bucket C — Fallback ✗ skipped (future work)

**Condition:** no higher bucket is applicable.

Elements in Bucket C are currently logged but not drawn. Future: wireframe from the projected 3D Body (without HLR).

---

### Bucket D — Annotations ✓ partially implemented

2D annotations extracted from the `IfcRelAssignsToGroup` → `IfcGroup(ObjectType='DRAWING')` group with the same name as the active drawing. Child annotations (`IfcAnnotation`) are classified by type and written as native DXF entities.

**How to find the annotations for a drawing:**

Match by the drawing's `GlobalId` in `RelatedObjects`, not by `Name`. The drawing `IfcAnnotation` is itself a member of the group, alongside its child annotations. The group `Name` may differ from the drawing `Name` (e.g. drawing `"01 PIANO TERRA ARCHITETTURA"` → group `"01 PIANO TERRA PLAN"`).

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

**Coordinate projection:** the 3D geometry of annotations is projected to 2D by multiplying by the camera inverse matrix (`cam_inv_np`).

#### D1 — DIMENSION ✓ implemented

Geometry source: `IfcGeometricCurveSet` → `IfcIndexedPolyCurve` → `IfcCartesianPointList2D`. The two endpoints of the dimension line are projected and written as DXF `DIMENSION` entities with `distance=0` (Bonsai stores the dimension line endpoints directly, not the measured object + offset).

```python
dim = msp.add_aligned_dim(p1=p0, p2=p1, distance=0,
    text=text, dimstyle="dimensions_metric_m",
    dxfattribs={"layer": "IfcAnnotation_Dimension"})
dim.render()
```

Dimstyle `dimensions_metric_m` (from template): dimension arrows, dedicated font. Dimensional parameters (text height, extension lines, gap) set in paper-space units (metres): `paper_mm * 0.001`. `dimscale = 1 / scale_factor` (e.g. 100 for 1:100) so that paper-space sizes render correctly in model space. Fallback `BONSAI_DIM` (oblique ticks) if the template is not available.

**Annotative flag:** each `DIMENSION` entity receives `AcadAnnotative` XDATA so that BricsCAD/AutoCAD treat it as an annotative object. Without this flag the dimension is visible only when the annotation scale matches, and appears tiny at fixed model-space size otherwise.

#### D2 — TEXT ✓ implemented

DXF `TEXT` entities with heights matched to Bonsai CSS styles:

| CSS Style | Paper height (mm) | Model-space (m) at 1:100 |
|---|---|---|
| `title` | 7.0 | 0.70 |
| `header` | 5.0 | 0.50 |
| `large` | 3.5 | 0.35 |
| `regular` | 2.5 | 0.25 |
| `small` | 1.8 | 0.18 |

Formula: `txt_height = paper_mm * 0.001 / scale_factor`

Alignment: mapped from the Bonsai CSS box-align attribute (`top-left`, `center`, etc.) to the DXF `halign` / `valign` codes of the `TEXT` entity.

#### D3 — Other types ✗ future work

Symbols, hatches, Bonsai SVG markers.

---

## Layer Naming

| Type | Format | Example |
|---|---|---|
| INSERT elements (Bucket A) | IFC class name | `IfcDoor`, `IfcFurniture` |
| INSERT overhead (Bucket A) | `<Class>_Overhead` | `IfcWindow_Overhead` |
| Wall section (Bucket B) | `<Class>_Section` | `IfcWall_Section` |
| Wall view (Bucket B) | `<Class>_View` | `IfcWall_View` |
| Wall hatch (Bucket B) | `<Class>_Hatches` | `IfcWall_Hatches` |
| Geometry inside BLOCKs | `"0"` with BYBLOCK | `0` (colour/linetype/lw from INSERT) |

Layer styles defined in `ifc_dxf_template_metric.dxf` (ACI colour, lineweight mm, linetype).

---

## DXF Entities Produced

`LINE`, `ARC`, `CIRCLE`, `ELLIPSE`, `LWPOLYLINE`, `HATCH` (solid fill), `DIMENSION`, `TEXT`.

---

## View Type (first discriminant)

| View | Status |
|---|---|
| Plan view | ✓ implemented |
| Section view (vertical) | future work |
| Elevation | future work |
| Reflected Ceiling Plan | future work |
| Axonometric | future work |

---

## Include / Exclude

In Blender (via operator), ifc_dxf reads the filtered element list from Bonsai via `tool.Drawing.get_drawing_elements()`. In standalone mode (`cli.py`), the list is built via frustum Z-culling + skipped-class filter.

---

## TODO / Roadmap

1. **Extend B-Approximate**: add `IfcColumn`, `IfcStairFlight` to the Shapely section path. (`IfcSlab` is now handled via the Bucket A footprint path — closed LWPOLYLINE from profile — rather than Bucket B.)
2. **B-Accurate (OCC/HLR)**: precise linework via ifcopenshell geom serializer.
3. **Bucket C**: wireframe fallback from the 3D Body.
4. **Bucket D - other types**: symbols, markers, hatches (D1 DIMENSION and D2 TEXT already implemented).
5. **Upstream PR ezdxf**: add `SCALE`/`AcDbScale` as a native entity type (group codes 300/140/141/290). Active workaround with `type()` hack.
6. **Upstream PR Bonsai**: fix door arc export as `IfcCircle` instead of `IfcEllipse`.
7. **Section view / Elevation**: non-zenithal camera logic.
8. **More IFC test fixtures**: `test_ifc_02` through `test_ifc_05` covering rotated walls, overhead elements, text annotations, section view, different scales.
9. **Report deep boolean chains**: during export, warn when an element's `IfcBooleanResult` chain depth exceeds a threshold, so users know to simplify geometry before exporting (B-Approximate silently drops such elements to Bucket C).
10. **frustum culling AABB fallback**: `element_in_frustum` currently tests only the `ObjectPlacement` origin. Walls/beams/slabs whose origin is at one end may be incorrectly culled when the body extends into the view. Fix: fall back to a full geometry AABB intersection (`ifcopenshell.geom`) when the origin test fails.
11. **Material-agnostic wall fusion option**: add an export flag to merge all `IfcWall_Section` polygons in a single `unary_union` regardless of material, useful when material assignments are inconsistent across the model.
12. **Per-layer fusion for `IfcMaterialLayerSet` walls**: decompose each wall polygon into per-layer strips using the layer thicknesses from `IfcMaterialLayerSet`, then key fusion groups by individual layer material. Concrete strips fuse with concrete, plaster with plaster, etc.

---

## Implementation Notes

### Camera Projection

Orthographic. The camera inverse matrix transforms world points → camera-local; camera X and Y map to drawing X and Y.

- `project()`: rotation + translation — for INSERT origins (world-space).
- `project_local()`: rotation only (`_cam_R`) — for BLOCK geometry (local coordinates).

### INSERT Rotation

Calculated in world XY (`atan2(local_x_world.y, local_x_world.x)`), **not** in camera space. The BLOCK geometry already has `R_cam` baked in; using `R_cam^T * R_elem` would cause a double camera rotation on all INSERTs.

### Required IFC Model Quality

B-Approximate and the slab occlusion filter assume:
- Correct Z coordinates for each element (accurate ObjectPlacement).
- Correct storey assignment.
- `IfcExtrudedAreaSolid` profiles with vertical extrusion.
- Slabs (`IfcSlab`) and floors (`IfcCovering FLOOR`) with correct Z coordinates.

Without OCC, an imprecise IFC model produces incorrect results without explicit error messages.
