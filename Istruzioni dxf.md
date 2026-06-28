# bonsai_salad — ifc_dxf: Regole di Generazione DXF

> Implementazione: Python puro (`ezdxf` + `shapely` + `ifcopenshell`). Nessuna dipendenza Rust/OCC lato ifc_dxf.

---

## Compatibilità IFC

- Funziona con tutte le versioni IFC disponibili in IfcOpenShell.
- I `RepresentationContext` disponibili vengono recuperati a runtime (ogni file IFC dichiara i propri sub-context), senza hardcode di versione.

---

## RepresentationIdentifier (IFC4)

Ogni `IfcShapeRepresentation` ha un campo `RepresentationIdentifier` che indica il ruolo della rappresentazione:

| Identifier | Tipo | Uso per ifc_dxf |
|---|---|---|
| `Body` | 3D solido / swept solid / BRep | Bucket B (se IfcExtrudedAreaSolid ‖ camera) o Bucket C |
| `FootPrint` | Proiezione planimetrica 2D | **Bucket A** (preferito per plan view) |
| `Axis` | Asse o linea centrale | **Bucket A** (es. travi, pilastri) |
| `Profile` | Sezione trasversale 3D | Bucket B (se estrusione ‖ camera) |
| `Reference` | Geometria di riferimento | Ignorato |
| `Surface` | Superficie esterna | Bucket C |
| `Annotation` | Annotazioni 2D | **Bucket D** (modulo `annotations`, future) |
| `Box` | Bounding box | Ignorato |
| `CoG` | Centro di gravità | Ignorato |
| `Clearance` | Spazio di manovra | Ignorato |
| `Lighting` | Geometria per render | Ignorato |

**Priorità di selezione per plan view** (ordine decrescente):
1. `Plan / Body / PLAN_VIEW` → **Bucket A**
2. `Plan / Body / MODEL_VIEW` → **Bucket A**
3. `Model / Body / PLAN_VIEW` → **Bucket A**
4. `Model / Body / MODEL_VIEW` → **Bucket A**
5. `FootPrint / PLAN_VIEW` → **Bucket A**
6. Nessuna repr 2D → **Bucket B** o **C**

---

## Sub-context: TargetView (`IfcGeometricProjectionEnum`)

```
IfcGeometricRepresentationContext   (ContextType = "Model" | "Plan")
  └─ IfcGeometricRepresentationSubContext
         ├─ ContextIdentifier  →  "Body", "FootPrint", "Axis", …
         └─ TargetView         →  IfcGeometricProjectionEnum
```

| TargetView | Descrizione | Vista ifc_dxf |
|---|---|---|
| `PLAN_VIEW` | Vista in pianta (dall'alto) | **Plan view** |
| `REFLECTED_PLAN_VIEW` | Pianta riflessa (soffitti) | RCP |
| `SECTION_VIEW` | Sezione verticale | Section view |
| `ELEVATION_VIEW` | Prospetto esterno | Elevation |
| `MODEL_VIEW` | Vista 3D generica | Axonometric |
| `GRAPH_VIEW` | Rappresentazione schematica | Asse, riferimento |
| `SKETCH_VIEW` | Schizzo approssimativo | Non usato |

---

## Struttura DXF — Regola fondamentale

Riprodurre la struttura IFC nelle entità DXF nel modo più fedele possibile:

| IFC | DXF |
|-----|-----|
| Tipo IFC (`IfcDoorType`, `IfcFurnitureType`, …) | Un **BLOCK** (nome = `GlobalId` del tipo) |
| Istanza di elemento IFC | Un **INSERT** |
| Elemento senza tipo condiviso | BLOCK con nome = `GlobalId` dell'elemento |

**Regola chiave:** usare sempre il `GlobalId` IFC come identificatore univoco di blocchi, non il campo `Name` (che può essere non univoco, es. "Unnamed" per più tipi diversi).

### Regole di posizionamento

- **Geometria del BLOCK**: in coordinate locali dell'elemento/tipo. Entità su layer `"0"` con `color=0` (BYBLOCK), `linetype="BYBLOCK"`, `lineweight=-2` (BYBLOCK). Così l'INSERT controlla colore, tipo linea e spessore.
- **INSERT position**: proiezione dell'origine world-space dell'`ObjectPlacement` dell'elemento.
- **INSERT rotation**: angolo (gradi, CCW) che l'asse X locale dell'elemento forma con l'asse X del disegno, nel world XY (non in camera space — la geometria del BLOCK ha già R_cam baked in).
- **INSERT layer**: nome esatto della classe IFC (es. `IfcDoor`, `IfcFurniture`, `IfcWindow_Overhead`).
- **MAI** mettere la geometria del block in coordinate world con INSERT a (0,0,0).

### Coordinate e scala DXF

- Sistema: metri reali 1:1. `$INSUNITS = 6` (Metres), `$MEASUREMENT = 1` (Metric).
- Il centro camera corrisponde a (0, 0) nello spazio disegno.
- Y crescente verso l'alto (convenzione DXF/matematica standard).
- `$LTSCALE`: impostato al fattore di scala numerico della vista (es. 0.01 per 1:100, 0.02 per 1:50). Letto da `EPset_Drawing.Scale` (formato `"1/100"`) tramite `fractions.Fraction`. Default: 0.01.

### Template DXF

Il file `ifc_dxf_template.dxf` (generato da `create_dxf_template.py`) contiene layer, dimstyle `BONSAI_DIM` e layout A1. All'export viene letto con `ezdxf.readfile()` e il modelspace viene svuotato (`msp.delete_all_entities()`), mantenendo tutti gli stili.

### Scala di annotazione

La scala di annotazione corrente viene scritta in `AcDbVariableDictionary` → `DictionaryVariables("CANNOSCALE", "1:100")`. **Non** va nell'header `$CANNOSCALE` (ignorato da BricsCAD/AutoCAD). ezdxf supporta questo tramite `dict.add_dict_var()`.

La lista scale (`ACAD_SCALELIST`) viene popolata con entità di tipo `SCALE` / `AcDbScale`. ezdxf non conosce questo tipo nativamente (group codes: `300`=nome, `140`=paper, `141`=drawing, `290`=flag 1:1 base). Workaround: `new_entity("SCALE")` + override `DXFTYPE` via `type()` + subclasses manuali.

---

## Classificazione a Bucket (A → D, priorità decrescente)

Per ogni elemento, il classificatore sceglie il bucket più alto applicabile.

### Bucket A — Rappresentazione 2D nativa ✓ implementato

**Condizione:** esiste una rappresentazione `Plan/Body/PLAN_VIEW` (o equivalente) nell'elemento o nel suo tipo IFC.

**Pre-filtro occlusione solaio:** prima di processare un elemento in Bucket A, verificare che non sia occultato da un solaio soprastante. Il filtro combina check Z e verifica del footprint XY del solaio in Shapely, così elementi in corti, atri o a quota diversa non vengono esclusi erroneamente.

```
Per ogni IfcSlab / IfcCovering(FLOOR) con Z_top ≤ cut_z:
  - estrai footprint 2D in world XY (IfcExtrudedAreaSolid → Shapely Polygon)
  - elemento escluso se: z_element_origin < z_top  AND  origin_XY ∈ footprint
```

**Overhead windows/doors:** elementi che riempiono aperture interamente sopra il piano di taglio (z_min_apertura > cut_z) vengono mostrati sul layer `<Classe>_Overhead` (es. `IfcWindow_Overhead`) con linetype tratteggiato. Poiché il frustum della camera ha Z_max = cut_z, questi elementi vengono re-aggiunti esplicitamente alla lista degli elementi da processare dopo il frustum culling.

**Sorgente geometria:** traversal Python diretto dell'albero IFC (`IfcMappedItem`, `IfcCompositeCurve`, `IfcTrimmedCurve`, `IfcIndexedPolyCurve`, ecc.). Fallback: `ifcopenshell.geom.create_shape` con `use-world-coords=False`.

**Entità DXF prodotte** (esatte, non tessellate):
- `IfcPolyline`, `IfcIndexedPolyCurve` (IfcLineIndex) → `LINE`
- `IfcIndexedPolyCurve` (IfcArcIndex) → `ARC`
- `IfcTrimmedCurve` (IfcCircle) → `ARC` o `CIRCLE`
- `IfcTrimmedCurve` (IfcEllipse, assi uguali) → `ARC`
- `IfcTrimmedCurve` (IfcEllipse, assi diversi) → `ELLIPSE`
- `IfcCircle` → `CIRCLE`

**Nota bug Bonsai:** gli archi di apertura delle porte vengono esportati come `IfcEllipse` invece di `IfcCircle`. Workaround attivo (`_trimmed_ellipse_spec`). Da segnalare come PR upstream.

**Principio di fedeltà IFC:** massima aderenza alla geometria come descritta in IFC. Non si fondono mai archi separati anche se contigui — due `IfcTrimmedCurve` → due `DXF ARC`.

**Output:** BLOCK + INSERT (uno per tipo/elemento, molti INSERT per le istanze).

**Limitazione:** richiede un modello IFC geometricamente corretto con quote Z proprie. Senza OCC, l'occlusione parziale (es. sedia parzialmente coperta da solaio) non è gestita — l'elemento è mostrato interamente o escluso interamente.

---

### Bucket B — Sezione generata dal 3D ✓ parzialmente implementato

**Condizione:** nessuna rappresentazione 2D nativa trovata dal Bucket A.

**Classi tipiche:** `IfcWall`, `IfcWallStandardCase`, (futuri: `IfcSlab`, `IfcColumn`, `IfcStairFlight`, …).

**Output:** entità direttamente in model space (no BLOCK/INSERT). Layer con suffisso semantico.

#### B-Approximate — Shapely ✓ implementato

Usato per elementi con `IfcExtrudedAreaSolid` e profilo 2D estraibile.

**Algoritmo per muri:**
1. Estrae il profilo 2D del muro (`IfcArbitraryClosedProfileDef`, `IfcRectangleProfileDef`).
2. Proietta il profilo in drawing space (camera matrix).
3. Sottrae i footprint delle aperture (`IfcOpeningElement`).
4. Raggruppa i poligoni per `(ifc_class, material)`.
5. Union Shapely con snap tolerance 0.5 mm → contorni puliti.
6. Scrive `LWPOLYLINE` chiusa → layer `IfcWall_Section` o `IfcWall_View`.
7. Scrive `HATCH` solid → layer `IfcWall_Hatches` (solo per elementi sezionati).

**Layers:**
- `IfcWall_Section`: taglio (cut) → linea grossa + hatch
- `IfcWall_View`: elementi visti sotto il piano di taglio → linea sottile, no hatch
- `IfcWall_Hatches`: campitura solid delle aree sezionate

**Nota:** B-Approximate richiede un modello IFC ben costruito. Non gestisce BRep, boolean complesse, muri non verticali.

#### B-Accurate — OCC/HLR ✗ future work

Usa `ifcopenshell.geom` con HLR (Hidden Line Removal) per generare linework preciso. Equivalente al path OCC di Bonsai SVG. Richiede OCC disponibile in ifcopenshell.

---

### Bucket C — Fallback ✗ skippato (future work)

**Condizione:** nessun bucket superiore applicabile.

Attualmente gli elementi in Bucket C vengono registrati ma non disegnati. In futuro: wireframe dal Body 3D proiettato (senza HLR).

---

### Bucket D — Annotazioni ✓ parzialmente implementato

Annotazioni 2D estratte dal gruppo `IfcRelAssignsToGroup` → `IfcGroup(ObjectType='DRAWING')` con lo stesso nome del disegno attivo. Le annotazioni figlie (`IfcAnnotation`) vengono classificate per tipo e scritte come entità DXF native.

**Come trovare le annotazioni di un disegno:**
```python
for rel in ifc.by_type("IfcRelAssignsToGroup"):
    group = rel.RelatingGroup
    if group.is_a("IfcGroup") and group.ObjectType == "DRAWING" and group.Name == drawing_name:
        for obj in rel.RelatedObjects:
            if obj.is_a("IfcAnnotation") and obj.id() != drawing.id():
                annotations.append(obj)
```

**Proiezione coordinate:** la geometria 3D delle annotazioni viene proiettata in 2D moltiplicando per la matrice inversa della camera (`cam_inv_np`).

#### D1 — DIMENSION ✓ implementato

Sorgente geometria: `IfcGeometricCurveSet` → `IfcIndexedPolyCurve` → `IfcCartesianPointList2D`. I due endpoint della linea di quota vengono proiettati e scritti come entità DXF `DIMENSION` con `distance=0` (Bonsai salva direttamente gli endpoint della linea di quota, non l'oggetto misurato + offset).

```python
dim = msp.add_aligned_dim(p1=p0, p2=p1, distance=0,
    text=text, dimstyle="BONSAI_DIM",
    dxfattribs={"layer": "IfcAnnotation_Dimension"})
dim.render()
```

Dimstyle `BONSAI_DIM`: tick obliqui (`dimtsz > 0`, standard europeo/italiano). Tutti i parametri (altezza testo, tick, gap) calcolati in model-space: `paper_mm * 0.001 / scale_factor`.

#### D2 — TEXT ✓ implementato

Testo MTEXT con altezze matchate agli stili CSS di Bonsai:

| Stile CSS | Altezza paper (mm) | Model-space (m) a 1:100 |
|---|---|---|
| `title` | 7.0 | 0.70 |
| `header` | 5.0 | 0.50 |
| `large` | 3.5 | 0.35 |
| `regular` | 2.5 | 0.25 |
| `small` | 1.8 | 0.18 |

Formula: `txt_height = paper_mm * 0.001 / scale_factor`

Allineamento: mappato da CSS box-align (`top-left`, `center`, ecc.) al codice `attachment_point` MTEXT (1–9).

#### D3 — Altri tipi ✗ future work

Simboli, retini, marker SVG di Bonsai.

---

## Layer naming

| Tipo | Formato | Esempio |
|---|---|---|
| INSERT elementi (Bucket A) | nome classe IFC | `IfcDoor`, `IfcFurniture` |
| INSERT overhead (Bucket A) | `<Classe>_Overhead` | `IfcWindow_Overhead` |
| Sezione muri (Bucket B) | `<Classe>_Section` | `IfcWall_Section` |
| Vista muri (Bucket B) | `<Classe>_View` | `IfcWall_View` |
| Hatch muri (Bucket B) | `<Classe>_Hatches` | `IfcWall_Hatches` |
| Geometria nei BLOCK | `"0"` con BYBLOCK | `0` (colore/linetype/lw dall'INSERT) |

Stili per layer definiti in `ifc_dxf_template.dxf` (color ACI, lineweight mm, linetype).

---

## Entità DXF prodotte

`LINE`, `ARC`, `CIRCLE`, `ELLIPSE`, `LWPOLYLINE`, `HATCH` (solid fill), `DIMENSION`, `MTEXT`.

---

## Tipo di vista (primo discriminante)

| Vista | Stato |
|---|---|
| Plan view (pianta) | ✓ implementato |
| Section view (sezione verticale) | future work |
| Elevation (prospetto) | future work |
| Reflected Ceiling Plan | future work |
| Axonometric | future work |

---

## Include / Exclude

ifc_dxf non itera autonomamente tutti gli elementi IFC. Legge la lista filtrata da Bonsai via `tool.Drawing.get_drawing_elements()`. In modalità standalone (`test_ifc_dxf.py`), la lista viene costruita tramite frustum Z + filtro classi skippate.

---

## TODO / Roadmap

1. **Estensione B-Approximate**: aggiungere `IfcSlab`, `IfcColumn`, `IfcStairFlight` al path Shapely.
2. **B-Accurate (OCC/HLR)**: linework preciso via ifcopenshell geom serializer.
3. **Bucket C**: wireframe fallback dal Body 3D.
4. **Bucket D - altri tipi**: simboli, marker, retini (D1 DIMENSION e D2 TEXT già implementati).
5. **Mirror operator.py**: le funzioni annotation di `test_ifc_dxf.py` vanno portate nell'operatore Blender.
6. **PR upstream ezdxf**: aggiungere `SCALE`/`AcDbScale` come entity type nativo (group codes 300/140/141/290). Workaround attivo con `type()` hack.
7. **PR upstream Bonsai**: fix esportazione archi porta come `IfcCircle` invece di `IfcEllipse`.
8. **Test data**: file IFC ridotto in `test_data/` per test riproducibili senza Dropbox.
9. **Section view / Elevation**: logica camera non-zenitale.

---

## Note implementative

### Proiezione camera

Ortografica. La matrice inversa della camera trasforma punti world → camera-local; X e Y camera = X e Y del disegno.

- `project()`: rotazione + traslazione — per origini INSERT (world-space).
- `project_local()`: solo rotazione (`_cam_R`) — per geometria BLOCK (coordinate locali).

### Rotazione INSERT

Calcolata nel world XY (`atan2(local_x_world.y, local_x_world.x)`), **non** in camera space. La geometria del BLOCK ha già `R_cam` baked in; usare `R_cam^T * R_elem` causerebbe doppia rotazione camera su tutti gli INSERT.

### Qualità del modello IFC richiesta

Il B-Approximate e il filtro di occlusione solaio assumono:
- Quote Z corrette per ogni elemento (ObjectPlacement accurato).
- Assegnazione storey corretta.
- Profili `IfcExtrudedAreaSolid` con estrusione verticale.
- Solai (`IfcSlab`) e pavimenti (`IfcCovering FLOOR`) con Z corretti.

Senza OCC, un modello IFC non preciso produce risultati errati senza messaggi di errore espliciti.
