# bonsai_salad — ifc_dxf: Regole di Generazione DXF

## Compatibilità IFC
- La libreria deve funzionare con tutte le versioni IFC disponibili in IfcOpenShell.
- Recuperare i `RepresentationContext` disponibili tramite IfcOpenShell a runtime (ogni file IFC dichiara i propri sub-context), senza hardcode di versione.

### RepresentationIdentifier disponibili in IFC4

Ogni `IfcShapeRepresentation` ha un campo `RepresentationIdentifier` (stringa) che indica il ruolo della rappresentazione. Valori standard IFC4:

| Identifier | Tipo | Uso per ifc_dxf |
|---|---|---|
| `Body` | 3D solido / swept solid / BRep | **Bucket A** (se IfcExtrudedAreaSolid ‖ camera) o **Bucket C** |
| `FootPrint` | Proiezione planimetrica 2D | **Bucket B** (preferito per plan view) |
| `Axis` | Asse o linea centrale | **Bucket B** (es. travi, pilastri) |
| `Profile` | Sezione trasversale 3D | **Bucket A** (se estrusione ‖ camera) |
| `Reference` | Geometria di riferimento (clash/interferenza) | Ignorato / Bucket D |
| `Surface` | Superficie esterna (pelle) | Bucket C |
| `Annotation` | Annotazioni 2D | Gestito da modulo `annotations` |
| `Box` | Bounding box | Ignorato |
| `CoG` | Centro di gravità (punto) | Ignorato |
| `Clearance` | Spazio di manovra / ingombro | Ignorato (opzionale) |
| `Lighting` | Geometria per render illuminazione | Ignorato |

**Priorità di selezione per plan view** (in ordine decrescente):
1. `FootPrint` — contesto 2D nativo, Bucket B
2. `Axis` — se FootPrint assente, Bucket B
3. `Body` con `IfcExtrudedAreaSolid` ‖ camera — Bucket A
4. `Body` generico — Bucket C
5. Qualsiasi altra rappresentazione — Bucket D

> In IFC4.3 (Infrastructure) sono presenti ulteriori identifier (`SurveyPoints`, `Alignment`, …) non rilevanti per gli usi edilizi correnti.

### Sub-context: TargetView (`IfcGeometricProjectionEnum`)

La struttura è gerarchica:
```
IfcGeometricRepresentationContext   (ContextType = "Model" | "Plan")
  └─ IfcGeometricRepresentationSubContext
         ├─ ContextIdentifier  →  "Body", "FootPrint", "Axis", …  (tabella sopra)
         └─ TargetView         →  IfcGeometricProjectionEnum
```

Valori di `TargetView` in IFC4 (`IfcGeometricProjectionEnum`):

| TargetView | Descrizione | Tipo di vista ifc_dxf |
|---|---|---|
| `MODEL_VIEW` | Vista 3D del modello | Axonometric / 3D generico |
| `PLAN_VIEW` | Vista in pianta (dall'alto) | **Plan view** |
| `REFLECTED_PLAN_VIEW` | Pianta riflessa (dal basso, per soffitti) | **RCP** |
| `SECTION_VIEW` | Sezione verticale | **Section view** |
| `ELEVATION_VIEW` | Prospetto esterno | **Elevation** |
| `GRAPH_VIEW` | Rappresentazione schematica / grafo | Asse, riferimento |
| `SKETCH_VIEW` | Schizzo approssimativo | Non usato |
| `USERDEFINED` | Definito dall'utente (accompagnato da `UserDefinedTargetView`) | Caso per caso |
| `NOTDEFINED` | Non definito | Fallback a `MODEL_VIEW` |

**Come interrogare a runtime con IfcOpenShell:**
```python
contexts = ifc_file.by_type("IfcGeometricRepresentationSubContext")
for ctx in contexts:
    print(ctx.ContextIdentifier, ctx.TargetView)
    # es. "FootPrint"  "PLAN_VIEW"
    # es. "Body"       "MODEL_VIEW"
    # es. "Axis"       "GRAPH_VIEW"
```

**Selezione del sub-context** — allineata a Bonsai SVG, con estensioni per elementi non-OCC.

Per elementi con sezione calcolata via OCC (es. IfcWall, IfcSlab, …):
1. `Plan / Body / PLAN_VIEW`
2. `Plan / Body / MODEL_VIEW`
3. `Model / Body / PLAN_VIEW`
4. `Model / Body / MODEL_VIEW`
5. `Facetation` — caso degradato, solo wireframe tessellato

Per elementi senza sezione OCC (es. IfcFurniture, IfcSanitaryTerminal, …):
1. `Plan / Body / PLAN_VIEW`
2. `Plan / Body / MODEL_VIEW`
3. `Model / Body / PLAN_VIEW`
4. `Model / Body / MODEL_VIEW`
5. `FootPrint / PLAN_VIEW`
6. `Axis / PLAN_VIEW`
7. `Facetation` — caso degradato, solo wireframe tessellato

---

## Struttura DXF — Regola fondamentale

Riprodurre la struttura IFC nelle entità DXF nel modo più fedele possibile:

| IFC | DXF |
|-----|-----|
| Tipo IFC (`IfcWindow`, `IfcFurnitureType`, …) | Un **BLOCK** (nome = nome del tipo) |
| Istanza di elemento IFC | Un **INSERT** |
| Elemento senza tipo condiviso (geometria diretta) | BLOCK con nome = `GlobalId` dell'elemento |

### Regole di posizionamento
- **Geometria del BLOCK**: in coordinate locali dell'elemento/tipo. Entità su layer `"0"`, proprietà BYBLOCK (colore, lineweight, linetype ereditati dall'INSERT).
- **INSERT position**: proiezione dell'origine world-space dell'`ObjectPlacement` dell'elemento.
- **INSERT rotation**: angolo (radianti, CCW) che l'asse X locale dell'elemento forma con l'asse X del disegno, dopo proiezione nel piano camera.
- **INSERT layer**: nome esatto della classe IFC (es. `IfcWall`, `IfcFurniture`).
- **MAI** mettere la geometria del block in coordinate world con INSERT a (0,0,0).

### Coordinate
- Sistema: metri reali 1:1. `$INSUNITS = 6` (Metres), `$MEASUREMENT = 1` (Metric).
- Il centro camera corrisponde a (0, 0) nello spazio disegno.
- Y crescente verso l'alto (convenzione DXF/matematica standard).

---

## Classificazione a Bucket (A → D, priorità decrescente)

Per ogni elemento, il classificatore Python sceglie il bucket più alto applicabile.

### Bucket A — Profilo estruso (esatto)
**Condizione:** `IfcExtrudedAreaSolid` senza operazioni booleane, con direzione di estrusione parallela alla direzione della camera (dot product > cos(15°) ≈ 0.966).

**Geometria prodotta:** profilo 2D esatto.
- `IfcArbitraryClosedProfileDef` / `IfcPolyline` → `LWPOLYLINE` chiusa
- `IfcArbitraryClosedProfileDef` / `IfcCompositeCurve` → segmenti LINE + ARC
- `IfcRectangleProfileDef` → `LWPOLYLINE` chiusa (4 vertici)
- `IfcCircleProfileDef` → `CIRCLE`
- `IfcCircleHollowProfileDef` → 2 × `CIRCLE` (esterna + interna)
- `IfcEllipseProfileDef` → `ELLIPSE`
- Profili parametrici (I, L, T, U, …) → punti calcolati in Python → `LWPOLYLINE`

### Bucket B — Rappresentazione 2D nativa

I vertici vengono estratti in **coordinate locali dell'elemento**:
- geometria diretta → ifcopenshell/OCC (C++) via `geom.create_shape(..., use-world-coords=False)`
- `IfcMappedItem` → traversal Python dell'albero IFC + applicazione manuale di `MappingTarget`

Rust separa geometria e posizionamento:
- **BLOCK geometry**: vertici proiettati con sola rotazione camera (`project_local`) → centrati su (0,0)
- **INSERT position**: proiezione completa (rotazione + traslazione) dell'origine world-space dell'`ObjectPlacement`
- **INSERT rotation**: angolo dell'asse X locale nel world XY (`atan2(local_x_world.y, local_x_world.x)`)

La rotazione INSERT è calcolata nel world XY, **non** in camera space. La geometria del BLOCK ha già R_cam^T baked in tramite `project_local`; per plan view (rotazioni tutte intorno a Z) R_cam e R_elem commutano, quindi R_insert = R_elem. Usare R_cam^T * R_elem causerebbe una doppia rotazione camera su tutti gli INSERT.

Assunzione: l'origine dei vertici locali coincide con l'origine dell'`ObjectPlacement`. Vale per file Bonsai; potrebbe non valere per IFC da Revit/ArchiCAD se `MappingOrigin` è non nullo.

### Bucket W — Muri (no BLOCK/INSERT)

**Classi**: `IfcWall`, `IfcWallStandardCase`.

**Regola fondamentale**: i muri non usano mai la struttura BLOCK/INSERT.

**Modalità di sezione** (`wall_mode`):

#### Mode `flat` (fallback)
- Python chiama `req.add_wall_flat(ifc_class, material, verts_local, edges, world_matrix)`.
- Rust proietta ogni edge con `project(world_matrix * v_local)` → entità `LINE` in model space.
- Nessuna hatch.

#### Mode `shapely` (default)
1. Python proietta gli edge locali in 2D drawing space con numpy (matrice `cam_inv @ world_m`).
2. Python usa `shapely.polygonize` + `unary_union` per ricostruire i poligoni di pianta, raggruppati per `(ifc_class, material)`.
3. Python chiama `req.add_wall_polygon(ifc_class, material, outer_pts_2d, holes_2d)`.
4. Rust scrive:
   - LWPOLYLINE chiusa per il contorno esterno → layer `IfcWall_Section`
   - LWPOLYLINE chiuse per ogni hole (apertura) → layer `IfcWall_Section`
   - `HatchRegion` con esterno + holes → layer `IfcWall_Hatches`

**Sorgente geometria attuale**: `geom.create_shape` su `Model/Body/MODEL_VIEW` con `IfcExtrudedAreaSolid` — mesh 3D tessellata con boolean (aperture già applicate da IfcOpenShell). In proiezione pianta:
- Edge orizzontali (top+bottom face) → contorno del muro ✓
- Edge verticali (angoli, stipiti) → proiettano a punti → filtrati ✓
- Edge header/davanzale → segmenti isolati non chiusi → ignorati da polygonize ✓

**Layers**:
- `IfcWall_Section`: contorno della sezione di taglio (outer ring + holes)
- `IfcWall_View`: facce visibili sotto il piano di taglio (da implementare con OCC)
- `IfcWall_Hatches`: campitura delle aree muro

**Wall union**: i poligoni dei muri adiacenti dello stesso `(ifc_class, material)` vengono uniti in Python con `shapely.unary_union` prima di essere passati a Rust.

### Bucket C — Body 3D (wireframe provvisorio)
**Condizione:** rappresentazione Body 3D disponibile; HLR (Hidden Line Removal via OCC) non ancora implementato.
- Geometria in coordinate world-space, proiettata tramite camera completa.
- INSERT fisso a (0, 0) nel disegno (nessun posizionamento utile senza HLR).
- Feature flag `occ` prevista per attivare HLR in futuro.

### Bucket D — Wireframe fallback
Ultimo resort: BRep grezzo proiettato. Stessa logica di C.

---

## Naming dei Layer

| Tipo di layer | Formato | Esempio |
|---|---|---|
| Classe IFC (INSERT) | Nome esatto classe IFC | `IfcWall` |
| Spigolo di taglio (cut) | `BBIM_<CLASSE>_CUT` | `BBIM_IFCWALL_CUT` |
| Spigolo proiettato (proj) | `BBIM_<CLASSE>_PROJ` | `BBIM_IFCWINDOW_PROJ` |
| Hatch materiale (non muri) | `BBIM_HATCH_<MATERIALE>` | `BBIM_HATCH_CONCRETE` |
| Hatch muri uniti | `<CLASSE>_Hatches` | `IfcWall_Hatches` |

- `<CLASSE>` = nome classe IFC tutto maiuscolo.
- `<MATERIALE>` = nome materiale, solo alfanumerici + `_`, maiuscolo, troncato a 24 caratteri.
- Layer INSERT: color index 7 (bianco/nero AutoCAD), lineweight 0.35 mm.
- Layer hatch: color index 254 (grigio chiaro), lineweight 0.13 mm.

---

## Entità DXF prodotte (esatte, non tessellate)

`LINE`, `ARC`, `CIRCLE`, `ELLIPSE`, `LWPOLYLINE` (chiusa o aperta), `SPLINE` (approssimata come `LWPOLYLINE`).

---

## Hatch (campiture)

- Costruite da loop chiusi di spigoli.
- Raggruppate per materiale (layer `BBIM_HATCH_<MATERIALE>`).
- Scritte direttamente nel model space (non dentro BLOCK).
- Pattern e scala definiti per materiale.
- Supporto fori (exterior boundary + interior holes) tramite union poligoni (`geo` crate).

---

## Problemi noti / TODO

### Sorgente geometria muri
`geom.create_shape` su `Model/Body/MODEL_VIEW` con `IfcExtrudedAreaSolid` (non una repr 2D pre-calcolata). IfcOpenShell applica già le boolean delle aperture nella tessellazione del solido 3D. In proiezione pianta: edge orizzontali → contorno muro; edge verticali → zero-length → filtrati; header/davanzale → segmenti isolati non chiusi → ignorati da polygonize.

### Wall union — mode shapely
`shapely.polygonize + unary_union` per `(ifc_class, material)`. Funziona per contorni regolari. Non produce holes per le aperture (i bordi verticali vengono filtrati prima della proiezione). Risultato: LWPOLYLINE outline + hatch fill per la superficie dei muri uniti.

### Layers muri
- `IfcWall_Section`: contorno esterno + bordi aperture orizzontali
- `IfcWall_View`: facce visibili sotto il piano di taglio (da implementare con OCC)
- `IfcWall_Hatches`: campitura aree muro (solo mode shapely)

---

## Tipo di vista (primo discriminante)

Il tipo di vista determina quali elementi includere, quale representazione scegliere e le regole view-specific.

Tipi previsti (terminologia da verificare con IfcOpenShell/Bonsai):
- **Plan view** (pianta) — implementato per primo
- Axonometric projection
- Reflected Ceiling Plan (RCP)
- Elevation (prospetto)
- Section view (sezione)
- Detail (dettaglio)

---

## Regole view-specific

### Plan View
- `IfcFurniture`, `IfcSanitaryTerminal`, `IfcDoor`, `IfcWindow`: sempre inclusi e sempre in primo piano (sopra gli elementi strutturali).
- Usare il contesto `FootPrint` o `Plan` se disponibile (Bucket B), altrimenti Bucket A o C.
- I muri (`IfcWall`, `IfcWallStandardCase`): geometria diretta (raramente hanno tipo condiviso) — trattati separatamente dagli elementi con tipo.

---

## Include / Exclude

- Bonsai mantiene regole di inclusione/esclusione per classe o per elemento.
- ifc_dxf deve rispettare queste regole: leggere la lista di elementi da Bonsai (già filtrata) anziché iterare autonomamente tutti gli elementi IFC.
- Implementazione: `tool.Drawing.get_drawing_elements()` restituisce solo gli elementi attivi per il drawing corrente.

---

## Note architetturali

- Il classificatore bucket risiede in Python (`ifc_dxf/operator.py`); il Rust riceve dati già classificati.
- La proiezione camera è ortografica. La matrice inversa della camera trasforma punti world → camera-local; X e Y camera = X e Y del disegno.
- `project()`: trasformazione completa (rotazione + traslazione) — per punti world-space (origini INSERT).
- `project_local()`: solo rotazione — per vettori/offset in coordinate locali (geometria nei BLOCK).
