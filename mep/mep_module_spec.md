# MEP module — bonsai_salad

Modulo per generazione impianti MEP in Bonsai/Blender, a partire da un grafo topologico
(punti di generazione → collettori → utilizzatori/recapiti), con dimensionamento normativo,
generazione geometria tubi/raccordi e output quantità per il computo.

Sviluppato come sotto-modulo di `bonsai_salad` (repo Carlo), non come estensione del core
Bonsai — vedi sezione "Stato Bonsai/IfcOpenShell" per il perché.

---

## 1. Tipologie di impianto (typology plugin)

Core condiviso (grafo, routing, export computo) + plugin per famiglia, ciascuno con:
regola di sizing, tabella normativa, catalogo componenti.

1. **Idraulici a gravità** ← si parte da qui
   - Scarico acque nere/grigie, pluviali
   - Norma: UNI EN 12056 (parte 1: UD per apparecchio; parte 2: DN da UD, portata di
     punta, grado di riempimento h/D per collettori orizzontali)
   - Grafo ad albero, monodirezionale (no anelli) → topological sort leaf-to-root,
     UD cumulate risalendo
2. **Idraulici in pressione**
   - Adduzione idrosanitaria (UNI 9182), antincendio idranti/naspi (UNI 10779) o
     sprinkler (EN 12845), gas (UNI 7129)
   - Darcy-Weisbach/Colebrook, verifica velocità e pressione residua ai terminali
   - Antincendio: spesso anelli (ridondanza) → serve solver diverso (Hardy-Cross),
     non il semplice tree traversal della famiglia 1
   - Da affrontare **dopo** aver validato il core sul caso semplice (adduzione
     sanitaria, senza anelli), prima di arrivare all'antincendio
3. **Aeraulici** (canali aria, VMC, climatizzazione)
   - Sezioni spesso rettangolari, perdite di carico aerauliche (UNI 10339)
   - Catalogo raccordi tutto diverso, non riusabile dal modulo tubo circolare

Elettrico escluso per ora (dominio troppo diverso: caduta di tensione/portata in
corrente, canaline non tubi).

**Sequenza di sviluppo**: scarico (famiglia 1) → adduzione idrosanitaria senza anelli
(famiglia 2, caso semplice) → poi eventualmente antincendio/aeraulici.

---

## 2. Modello a grafo

Nodi tipizzati:
- **Utilizzatore** → `IfcFlowTerminal`, con UD di scarico (da tabella UNI EN 12056-1
  per tipo apparecchio)
- **Collettore/diramazione** → `IfcFlowFitting` (junction)
- **Colonna** → sequenza verticale di `IfcFlowSegment`
- **Recapito** → terminale a valle (pozzetto, fognatura pubblica)

Per lo scarico: albero puro, nessun anello.

---

## 3. Dimensionamento (famiglia 1 — scarico)

- Metodo UD (unità di scarico) → portata di punta con coefficiente di contemporaneità
  → DN minimo da tabella (UNI EN 12056-2)
- Collettori orizzontali: DN in funzione di UD, pendenza e grado di riempimento h/D
  ammesso
- Tabelle norma da codificare direttamente (non ricalcolare da Colebrook ogni volta)

**Da verificare/completare**:
- Tabella UD per apparecchio sanitario standard (UNI EN 12056-1)
- Grado di riempimento h/D ammesso per collettori (raccordi vs collettori orizzontali)
- Range pendenze minime/massime per DN (necessario per generare i dislivelli lungo i
  tratti orizzontali in fase di routing geometrico)

---

## 4. Stato Bonsai/IfcOpenShell (verificato luglio 2026)

- **Create Duct**: unico tool nativo, modellazione di base per singolo segmento, non
  pensato per topologia generata da grafo
- Pagina doc ufficiale "Services and Systems" segnata **Work in Progress / incomplete**
- `IfcDistributionPort` / `IfcRelConnectsPorts`: ancora in discussione nella community
  (OSArch), nessuna UI matura
- **Conseguenza**: il modulo lavora a basso livello con `ifcopenshell` diretto (come
  `ifc_dxf` e gli script assi tubazioni), non sopra il tool nativo. Se la
  strutturazione di porte/connessioni viene fatta bene, valutare una proposta upstream.

---

## 5. Libreria parti — architettura

Tre livelli separati:

1. **Dati dimensionali** — `mep/catalog/<sistema>/data.yaml`
   Per DN: diametro esterno/spessore (tubi dritti); quote FxF (faccia-a-faccia) e
   angolo (curve); quote braghe/riduzioni/tappi; codice articolo (dove applicabile)
2. **Generatori geometrici parametrici** — `geometry.py`
   Funzioni per famiglia di pezzo (estrusione circolare per i dritti, sweep lungo
   arco dedotto da FxF+raggio per le curve) — non mesh statiche per SKU
3. **Mappatura articolo/prezzo** — `articles.yaml`
   DN+tipo+angolo → codice → prezzo, separata dalla geometria (i listini cambiano ogni
   anno, non deve toccare la logica di generazione)

Struttura cartelle (per manufacturer/sistema, non tutto in un unico catalogo):
```
mep/catalog/
  geberit_pe/        # interno edificio, saldato, d32–d315
  geberit_silent_pp/ # interno, ad innesto, acustico — rimandato
  pvc_sn4_sn8/        # esterno interrato, EN 1401-1, generico (no vendor fisso)
```

IFC: `IfcPipeSegmentType`/`IfcPipeFittingType` una volta per combinazione DN(+angolo)
come master nel catalogo; il motore di routing crea solo occorrenze
(`IfcPipeSegment`/`IfcPipeFitting`) che li referenziano. Il computo diventa conteggio
di occorrenze per tipo.

### Fonti dati per sistema

- **Geberit PE**: contenuti BIM Geberit sono nativamente **Revit (RFA)**, geometria
  semplificata; **nessun export IFC nativo confermato** dal portale Geberit. BIMobject.com
  mostra "classificazione IFC" ma è metadato di categoria, non garanzia di geometria
  scaricabile. → Niente estrazione automatica via `ifcopenshell`: trascrizione manuale
  dalle schede tecniche PDF (dataset comunque contenuto: quote FxF per DN/angolo)
- **PVC SN4/SN8**: non è un prodotto Geberit — classe di rigidità anulare UNI EN 1401-1,
  prodotta da vari fornitori (REDI/Aliaxis, Resinplast, Lareter, Martoni...). Copre il
  tratto esterno interrato (dalla base colonna al recapito), sistema separato da Geberit
  interno. **Deciso: partire dalle quote nominali di norma** (DN/OD e spessore minimo per
  SDR41/SN4 e SDR34/SN8), non da un fornitore specifico — aggiornabile poi con dati
  vendor-specifici se serve per un computo reale

---

## 6. Norme — cartelle e report

Aggiunta cartella `mep/norms/`, parallela a `catalog/`, per i dati normativi:

```
mep/norms/
  en_12056/
    raw/          # copia personale della norma — SOLO locale, mai in git (copyright UNI/EN)
    extracted/    # tabelle numeriche trascritte, con riferimento norma/tabella per voce
  en_1401/
    raw/
    extracted/
```

`raw/` è in `.gitignore`: le norme UNI/EN sono standard a pagamento, non
distribuibili. Solo `extracted/` (dati numerici derivati, con citazione di
paragrafo/tabella) viene versionato nel repo.

### Report di dimensionamento per linea

Al momento della creazione degli elementi di una linea, `core/sizing.py` produce
un JSON con i risultati del dimensionamento (tratti, DN, UD, portata, pendenza,
grado di riempimento) e il riferimento normativo puntuale per ciascun valore
(preso da `norms/*/extracted/`). Un template Typst comune
(`mep/reports/templates/sizing_report.typ`) legge quel JSON e compone il PDF —
stesso pattern già usato in `ifc_hygrothermal`. Output in `mep/reports/generated/`
(non versionato).

## 7. Scaffold creato

Cartelle e file segnaposto (`mep/catalog/`, `mep/norms/`, `mep/core/`,
`mep/reports/`, `.gitignore`, template Typst) generati e consegnati come
`bonsai_salad_mep_scaffold.zip` — da scompattare nel repo `bonsai_salad`.

## 8. Flusso UI linea di scarico (concordato 2026-07-10)

1. Selezione utilizzatori e destinazione (recapito) nel modello
2. Pulsante "Genera tracciato" (opzione: ortogonale) → crea lo **scheletro**:
   una mesh Blender helper di soli edges (vertici saldati ai giunti), NON un
   elemento IFC. Verificato 2026-07-10: un elemento IFC a curve non è editabile
   in Bonsai (`auto_detect_curves` cammina loop continui, i grafi con
   diramazioni lo rompono — possibile proposta di fix upstream). Lo scheletro
   resta "sempre disponibile" perché rigenerabile dagli assi per-segmento
   (subcontext Axis/GRAPH_VIEW) presenti nell'IFC
3. L'utente modifica il tracciato in edit mode, ferme restando sorgenti e
   destinazione
4. Fine editing
5. Il tool **valida** il tracciato (albero, pendenze, vertici doppi;
   operatore "Applica pendenze" per normalizzare le z)
6. Calcolo (sizing EN 12056-2) e generazione di IfcPipeSegment/IfcPipeFitting
   con porte connesse; rigenerare = ricreare le occorrenze del circuito

**Porte degli utilizzatori** (pattern type-driven): il type (es.
IfcSanitaryTerminalType) porta una IfcDistributionPort template via
IfcRelNests, con offset locale dall'origine del type — mai usata nel calcolo.
Le occorrenze ricevono porte specchiate dal nostro script ("Segna
Utilizzatore" / "Sincronizza Porte"), con placement relativo all'elemento:
spostando l'elemento la porta segue; la sync ripara porte mancanti su nuove
istanze o offset divergenti dal template. L'apparecchio (DU) sta nel pset
EN12056_Utilizzatore sul type.

Ogni step deve avere **undo** disponibile: operatori con
`bl_options = {"REGISTER", "UNDO"}` e transazioni IFC secondo il pattern
Bonsai (`IfcStore.execute_ifc_operator`). L'eventuale sync scheletro↔tubi dopo l'editing
si affronta a parte, più avanti.

- [ ] TODO: tool per generare una **colonna di scarico** parametrica: n. piani,
  interasse di piano, braghe già pronte per il collegamento ai piani, con tipi
  standard di allaccio, testa (sfiato) e piede (curva al collettore)
- [ ] TODO: **isolamenti delle tubazioni**: IfcCovering (INSULATION) associato
  ai segmenti via IfcRelCoversBldgElements, spessori da catalogo/norma;
  acustico per lo scarico (dove richiesto), termico/anticondensa per la
  famiglia 2 (adduzione) — dati nel catalogo, voce separata nel computo

## 9. Aperti / da verificare prima di procedere

- [x] Tabella dimensionale EN 1401-1: trascritta da UNI EN 1401-1:2019+A1:2023
  (Table 5/6) in `norms/en_1401/extracted/dimensioni_sn4_sn8.yaml`
- [x] Tabella UD per apparecchio: è nella **parte 2** (prospetto 2, non parte 1 come
  ipotizzato) — trascritta in `norms/en_12056/extracted/ud_apparecchi.yaml`
- [x] Grado di riempimento e portate collettori: prospetti B.1 (h/d=0,5) e B.2
  (h/d=0,7) trascritti in `extracted/collettori.yaml`; Qww = K·√ΣDU (§6.3.1)
  implementata in `core/sizing.py` (il prospetto B.3 è derivato, non trascritto)
- [x] Pendenze: la norma non dà una tabella min/max per DN; pendenza min diramazioni
  da prospetto 5, range collettori 0,5–5,0 cm/m dai prospetti B — vedi
  `extracted/pendenze.yaml`
- [ ] Catalogo angoli/quote FxF raccordi Geberit PE (curve 15°/30°/45°/87°30',
  braghe, riduzioni, manicotti) — da schede tecniche, non ancora trascritto
- [ ] Come gestire `IfcDistributionPort`/connessioni date le limitazioni del core
  Bonsai — via `ifcopenshell` diretto, definire schema minimo prima di scrivere
  il motore di routing. **Prima proposta implementata** in `core/ifc_export.py`:
  porte SINK/SOURCE per estremità, una `IfcRelConnectsPorts` orientata per
  connessione, tubo→raccordo→tubo (vedi `mep/examples/bathrooms.py`)
- [ ] Generatori parametrici di utilizzatori (terminali):
  - termosifoni: n. di elementi, n. di colonne, altezza, larghezza, posizione
    delle porte
  - ventilconvettori: dimensioni, posizione porte
  - individuare produttori con dati tecnici pubblici per compilare in automatico
    i parametri, se richiesto
  - riferimenti dimensionali per corpi scaldanti antichi o vecchi (radiatori
    lamellari, in ghisa a colonne)
- [x] Collocazione nel repo: modulo `mep/` (coerente con ifc_dxf/dxf_ifc), registrato
  in `__init__.py` root con pannello proprio "MEP (WIP)"; core puro Python senza bpy,
  testato con pytest (`tests/mep/`). Target IfcOpenShell/Bonsai: 0.8.5
