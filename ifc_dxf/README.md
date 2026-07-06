# ifc_dxf: DXF Generation

Export an IFC drawing view to DXF with maximum structural and geometric fidelity.

**Upstream-merge principle:** this work is aimed at being merged upstream into
Bonsai. Therefore ifc_dxf must not reimplement what Bonsai already provides:
whenever `bonsai.tool` is importable, its functions are reused directly (e.g.
`tool.Drawing.get_drawing_elements` for element selection); pure-ifcopenshell
reimplementations exist only as *standalone shims* (headless use without bpy)
and are throwaway code at merge time. Before writing new logic, check whether
`tool.Drawing` / `ifcopenshell.util` already has it.
Two independent extraction pipelines (`ifc_dxf/core/approximate/`, `ifc_dxf/core/accurate/`)
share the same DXF writer, template handling, materials classification and
annotation writer (`ifc_dxf/core/dxf_writer.py`, `dxf_template.py`, `materials.py`,
`annotations.py`). No OCC Python bindings are required for either pipeline —
Accurate uses ifcopenshell's own native HLR serializer.

| Pipeline | Status | Approach |
|----------|--------|----------|
| **A — Approximate** | ✓ implemented | IFC-native traversal: 2D reprs as BLOCK/INSERT, sections via Shapely profile extraction |
| **B — Accurate** | ✓ v1 (linework only) | OCC/HLR via `ifcopenshell.geom.serializers.svg` — matches Bonsai's own SVG export engine |

**Convergence plan (decided lug 2026):** the two-pipeline split is temporary.
Accurate keeps being developed until it reaches feature parity with Approximate
(overhead fills, slab/covering/roof footprints, material-layer decomposition —
see roadmap items 9/11/12). Then the pipelines merge into a single one where
the section-cut extractor is a strategy — HLR (default, Bonsai's own engine) or
Shapely profile projection — and Approximate stops existing as a separate
pipeline: its extractor survives as the fallback strategy (it already powers
below-cut view walls, which HLR structurally cannot produce, and remains the
second opinion when HLR misbehaves on a model). `wall_mode="flat"` will be
dropped in the merge.

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

**Primary path (inside Blender):** `get_elements` delegates to Bonsai's own `tool.Drawing.get_drawing_elements(drawing)` whenever Bonsai is importable, its loaded file is the *same object* passed to the exporter, and the drawing has a Blender camera object. This reuses Bonsai's exact selection semantics: full Blender `bound_box` AABB vs camera box culling (`is_in_camera_view`), `Include`/`Exclude` handling including the `filter_structure` JSON form (via `tool.Search`), and aggregate re-addition — so DXF and SVG exports agree on what is in view. Bonsai re-adds the drawing's own annotations to the set (its SVG pipeline draws them inline); we strip them since annotations are handled separately as Bucket D.

**Fallback path (standalone, no bpy/Bonsai):** pure-ifcopenshell reproduction of the same pipeline. A world-space bounding box `(x_min, x_max, y_min, y_max, z_min, z_max)` is derived from the camera body geometry (`IfcCsgSolid` / `IfcExtrudedAreaSolid`) transformed by the camera's `ObjectPlacement`. Each element's `ObjectPlacement` origin is tested against this box. Elements outside are excluded.

**Two-pass culling (fallback path):** pass 1 is the cheap `ObjectPlacement`-origin test (`element_in_frustum`); elements whose origin falls outside are not discarded but re-tested in pass 2 with a real world-AABB overlap check from tessellated body geometry (`filter_elements_in_frustum` → `_aabb_overlap_pass`, one batched multicore `ifcopenshell.geom.iterator` run over only the failed candidates). This keeps long walls/slabs/beams whose placement origin lies outside the view but whose body extends into it. Elements the iterator yields no shape for stay excluded. The export log reports how many elements the AABB pass rescued.

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

`txt_height = paper_mm * 0.001 / scale_factor`.

Two sub-cases, both `ObjectType == "TEXT"`, distinguished by whether the annotation
has an `IfcTypeProduct` (`IsTypedBy` → `IfcRelDefinesByType`):

- **Plain/untyped** (free-standing notes): each `IfcTextLiteralWithExtent` in the
  element's own `Representation` becomes one DXF `TEXT` entity, positioned at the
  literal's own local `Placement` (not just the annotation origin — needed once
  an annotation carries more than one literal). Gets a single annotative-scale
  representation via `_make_text_annotative()`.

- **Typed tags** (space tags, and any other Bonsai "tag" built from a library
  type — door tags, level tags, ...): Bonsai shares the tag's text-literal
  layout from the type's `RepresentationMaps` (same mechanism as door/window
  plan symbols sharing a type's Body geometry), and the *stored* `Literal`
  string keeps Bonsai's unresolved template syntax forever — `{{Name}}`,
  `` `` `round({{Qto_SpaceBaseQuantities.NetFloorArea}}, 0.01)` `` `` — resolving
  it live against the tag's assigned product every time Bonsai renders an SVG
  (`tool.Drawing.replace_text_literal_variables`, never baked back into the IFC).
  Mapped onto DXF as a **shared BLOCK with one ATTDEF per text literal** (default
  text = the raw template, position/`halign`/`valign` from the literal's own
  `Placement`/`BoxAlignment`) plus **one INSERT+ATTRIB per instance**, with each
  ATTRIB holding that instance's resolved value. One BLOCK per `IfcTypeProduct`,
  reused across every tagged instance (`get_type_block_name`, same helper Bucket A
  plan symbols use). The instance → product link (e.g. which `IfcSpace` a tag
  belongs to) comes from `IfcRelAssignsToProduct` (`HasAssignments`), matching
  Bonsai's `bpy.ops.bim.edit_assigned_product` — not geometric proximity or naming.
  Template resolution reuses `ifcopenshell.util.selector.get_element_value()` /
  `.format()`, the same standard (non-Bonsai-specific) functions Bonsai itself calls.

**D3 — Other (future):** symbols, hatches, Bonsai SVG markers.

---

## Pipeline B — Accurate (OCC/HLR)

Hybrid: a true HLR section cut only for classes that need one (`IfcWall`,
`IfcWallStandardCase`, `IfcColumn` — same set as Pipeline A's `_SECTION_CLASSES`);
everything else (doors, windows, furniture, sanitary fixtures, ...) reuses
Pipeline A's Bucket A logic verbatim — native 2D plan representation, shared
BLOCK per `IfcTypeObject` — via the shared `plan_symbols.place_plan_symbol()`.

**Why hybrid:** the HLR serializer's `class="projection"` catch-all group
(meant for below-cut visible-but-not-sliced geometry) was found to place its
paths at coordinates unrelated to the element's real position — verified even
for a single isolated element written to the serializer alone, so it is not a
camera/parsing bug on this side. Root cause suspected to be in how the native
serializer handles shared/mapped Type representations, not yet diagnosed
further. Rather than draw misleading linework, these classes skip HLR entirely
and use the same native-representation path as Pipeline A, which is both
reliable and (for door/window/furniture symbols) usually what you want to see
in a plan anyway.

### HLR section cut (Wall / WallStandardCase / Column, cut by the plane)

Elements are first split into **cut** (Z range straddles `cut_z`, real HLR
section) vs **view** (entirely below `cut_z`, see next section) using the same
`_wall_z_range` test as Pipeline A's `classify_elements`.

For cut elements: calls `ifcopenshell.geom.serializers.svg`, the native C++
serializer built on OpenCASCADE's HLRBRep engine — the same engine Bonsai's own
SVG export uses. No separate OCC Python bindings needed, it ships in the
standard ifcopenshell wheel.

**Camera setup:** `SvgSerializer.addDrawing(pos, view_dir, ref_dir, name, True)`.
`ref_dir` must be the drawing placement's local **+X** axis, not +Y — empirically
verified: passing local Y rotates the HLR output 90° relative to the rest of the
pipeline (Pipeline A's camera-space projection). With local X, HLR path
coordinates match Pipeline A's to float precision, no further transform needed.
`pos`/`view_dir`/`ref_dir` are plain `(x, y, z)` tuples — the SWIG binding accepts
them directly in place of `gp_Pnt`/`gp_Dir`. See `camera.camera_pos_dir_ref()`.

**Pipeline:** `addDrawing` → iterate elements via `ifcopenshell.geom.iterator` →
`serialiser.write(elem)` per element → `finalize()` → `buf.get_value()` returns
an SVG XML string directly (no file I/O). Parsed with `xml.etree.ElementTree`.

**Output structure:** one `<g class="section" ifc:plane="...">` wrapping the
whole drawing. Inside it, `<g id="product-{guid}-body" class="{IfcClass}"
ifc:guid="...">` gives a clean per-element HLR outline. Each `<path d="...">`
may contain several `M`-delimited closed loops; each loop is built directly
into a Shapely `Polygon` (HLR closes loops back to their start point, so no
extra work is needed) and grouped into the same `wall_polys_by_key` structure
Pipeline A uses, keyed by `(ifc_class, material, f"{ifc_class}_Section", None)`.
`_write_dxf` then Shapely-unions (fuses adjacent/overlapping wall outlines at
corners and T-junctions) and hatches them exactly like Pipeline A — no
Accurate-specific fusion/hatch code was needed, only feeding HLR polygons
through the existing shared machinery.

The `class="projection"` catch-all (see above) is counted and dropped, not drawn.

### View walls/columns (below the cut plane, not sliced)

HLR's single `addDrawing` pass only returns geometry actually sliced by the
cut plane (empirically verified: an isolated below-cut element produces
nothing at all). Rather than build a second HLR configuration for this, view
elements reuse Pipeline A's `_extract_wall_polygon_with_openings` Shapely
profile projection directly (from the shared `core/geometry.py`) —
for a simple vertical wall/column prism this gives the exact same silhouette a
true top-down HLR projection would, so it's not a loss of accuracy for the
common case. Grouped into `wall_polys_by_key` under `f"{ifc_class}_View"`,
keyed additionally by `z_max` (rounded) like Pipeline A, so polygons from
different floor levels never fuse together.

### Native 2D plan symbol (everything else)

Same code as Pipeline A's Bucket A, factored out into `plan_symbols.place_plan_symbol()`
(shared module, used by both pipelines): `find_plan_repr` looks up the
element's or its type's Plan/Body representation, `curves._extract_local_curves`
extracts exact arcs/circles/ellipses (factored into `curves.py` for this
reuse), one BLOCK is shared across instances of the
same `IfcTypeObject`.

**v1 limitations (by design, to revisit):**
- No material-layer hatch decomposition (`IfcMaterialLayerSet` strips) for HLR/
  view walls yet — Pipeline A's `export_material_layers` option isn't wired up
  here; HLR/view walls currently only get the standard single-material hatch.
- No overhead-fill re-addition (Pipeline A re-adds windows/doors whose opening
  is entirely above the cut plane, marked `_Overhead`); Pipeline B's shared
  `get_elements()` call doesn't perform this step yet, so such elements are
  currently missing from the accurate export.
- `IfcSlab`/`IfcCovering`/`IfcRoof` footprint extraction (Pipeline A's Shapely-
  based `_slab_footprint_world` path) isn't ported to Pipeline B; these classes
  fall back to `place_plan_symbol` only, or are skipped if that fails.
- Root cause of the `class="projection"` mispositioning is still undiagnosed;
  worth a proper upstream investigation before relying on it for anything.

---

## TODO / Roadmap

**Pipeline A:**
1. Add `IfcStairFlight` to Bucket B section path.
2. Report elements with deep boolean chains (> threshold) during export.
3. ~~Frustum culling AABB fallback: test full geometry bounding box when origin
   test fails~~ — FATTO (lug 2026), two ways: inside Blender, Bonsai's own
   `tool.Drawing.get_drawing_elements` (Blender bound_box AABB culling) is
   reused; standalone, `filter_elements_in_frustum` adds a batched
   geometry-AABB second pass for origin-outside elements.
4. Material-agnostic wall fusion option (`unary_union` regardless of material).
5. Bucket D — D3: symbols, markers, hatches.
6. More IFC test fixtures (rotated walls, overhead elements, text annotations, sections, different scales).

**Pipeline B:**
7. ~~Hatches + fusion for section-cut walls/columns (feed HLR polygons through
   Pipeline A's wall_polys_by_key -> shared Shapely union + hatch)~~ — FATTO (lug 2026)
8. ~~Below-cut "view" geometry (elements not crossing the cut plane)~~ — FATTO (lug 2026),
   via Pipeline A's Shapely profile projection, not a second HLR pass
9. Material-layer hatch decomposition (`IfcMaterialLayerSet` strips) for HLR/view walls.
10. Diagnose the `class="projection"` mispositioning (shared/mapped Type
    representations?) — currently dropped rather than attributed.
11. Overhead-fill re-addition, matching Pipeline A.
12. `IfcSlab`/`IfcCovering`/`IfcRoof` footprint extraction, matching Pipeline A.
13. ~~Naming convention for dxf blocks: ClassNameSenzaIfc_TypeNameSenzaIfc_GuidLast8Chars
    for example: FurnitureType_Bigtablewithchairs_851asdas~~ — FATTO (lug 2026),
    via `make_block_name` in `core/ifc_query.py` (type-based and per-instance blocks)
14. Template-based space tag: se il template DXF contiene già un blocco con nome corrispondente al tipo esportato da Bonsai e attributi coincidenti con i {{}}, usare quello invece di generarlo — si parte aggiungendo il blocco al template


**Upstream:**
13. PR ezdxf: native `SCALE`/`AcDbScale` entity type (group codes 300/140/141/290).
14. PR Bonsai: fix door arc exported as `IfcEllipse` instead of `IfcCircle`.

**Upstream-merge principle — remaining reimplementations to convert to Bonsai
reuse (guarded import, own code demoted to standalone shim), same pattern as
`_get_elements_via_bonsai`:**
- `_get_drawing_annotations` (`ifc_query.py`) → `tool.Drawing.get_drawing_group`
  + `get_group_elements`. Bonsai's version also reads `drawing.HasAssignments`
  directly instead of scanning every `IfcRelAssignsToGroup` in the file.
- `get_assigned_product` (`ifc_query.py`) → `tool.Drawing.get_assigned_product`.
  Bonsai's version additionally resolves `IfcGrid` axis assignments
  (`rel.Name` vs `AxisTag`), which ours misses.
- `_parse_scale_factor` (`dxf_template.py`) → check `tool.Drawing` /
  `ifcopenshell.util.unit` scale helpers before keeping our parser.
- Any *new* logic: check `tool.Drawing` / `ifcopenshell.util` first (see
  Upstream-merge principle at the top).

**Convergence (after Pipeline B reaches parity — items 9/11/12):**
15. Merge A and B into a single pipeline: shared orchestration (selection,
    classification, symbols, annotations, writer), section-cut extractor as a
    strategy ("hlr" default / "profile" fallback). Drop `wall_mode="flat"`.

**Future pipelines:**
16. Section view / Elevation: non-zenithal camera logic.
17. Reflected Ceiling Plan, Axonometric.

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
