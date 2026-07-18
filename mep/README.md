# mep/ — modulo bonsai_salad

Generazione impianti MEP in Bonsai/Blender a partire da un grafo topologico, con
dimensionamento normativo, geometria tubi/raccordi e quantità per il computo.
Specifica completa: `mep_module_spec.md`. Nessuna dipendenza Python esterna:
le tabelle dati sono json (stdlib).

## Stato

- `core/graph.py` — rete ad albero (famiglia 1, gravità): validazione, ordinamento
  leaf-to-root, DU cumulate, posizioni 3D dei nodi. **Implementato e testato.**
- `core/norms.py` — loader delle tabelle normative trascritte, con citazione
  norma/prospetto per ogni valore. **Implementato e testato.**
- `core/sizing.py` — motore EN 12056-2: Qww = K·√ΣDU (§6.3.1), portata di progetto
  ≥ DU max (§6.3.4), DN da prospetto 4 (diramazioni, con limiti WC), prospetti 11/12
  (colonne, DN min 100 con WC), prospetti B.1/B.2 (collettori per pendenza e h/d),
  DN mai decrescente verso valle (§5.5). Output JSON per il report Typst.
  **Implementato e testato.**
- `core/routing.py` — abbozzo v1: percorsi ortogonali con pendenza di progetto,
  curve ai cambi di direzione, braghe in serie dove confluiscono più flussi;
  raccordi risolti contro il catalogo (warning se mancano le quote FxF).
- `core/ifc_export.py` — v1 standalone: un IfcPipeSegmentType/IfcPipeFittingType
  per DN come master, occorrenze, IfcDistributionSystem DRAINAGE, porte
  connesse tubo→raccordo→tubo (IfcDistributionPort/IfcRelConnectsPorts).
- `core/computo_export.py` — stub (dipende da ifc_export).
- `catalog/` — pvc_sn4_sn8 popolato dalle quote nominali EN 1401-1; geberit_pe da
  trascrivere dalle schede tecniche. Schema: `catalog/README.md`.
- `operator.py` / `ui.py` — pannello "MEP (WIP)": scelta catalogo/sistema/uso e
  verifica dati (`bim.mep_check_data`).

## Struttura

    mep/
      norms/      tabelle normative trascritte (json) — vedi norms/README.md
      catalog/    librerie parti per sistema — vedi catalog/README.md
      core/       grafo, tabelle, sizing, routing, export IFC, export computo (puro Python, no bpy)
      examples/   esempio eseguibile: python -m mep.examples.bathrooms
      reports/    template Typst + report generati per linea

Test: `python -m pytest tests/mep` dalla root del repo.

## Prossimi passi

1. Trascrivere quote FxF raccordi Geberit PE in `catalog/geberit_pe/data.json`
2. Completare il routing (deviazioni di colonna, accorciamento tubi per FxF)
3. Export IFC dentro il modello Bonsai caricato (ora file standalone)
4. UI di definizione del grafo in Blender (posizionamento utilizzatori/recapito)
5. Isolamenti delle tubazioni: IfcCovering INSULATION con spessori da catalogo
6. Generatori parametrici di utilizzatori (terminali):
   - termosifoni: n. elementi, n. colonne, altezza, larghezza, posizione delle porte
   - ventilconvettori: dimensioni, posizione porte
   - individuare produttori con dati tecnici pubblici per compilare in automatico
     i parametri, se richiesto
   - riferimenti dimensionali per corpi scaldanti antichi o vecchi
     (radiatori lamellari, in ghisa a colonne)

## Modello IFC ideale (nessun pset custom in input)

Il calcolo legge prima i dati standard; i pset custom sono solo override.
Un modello ben tipizzato non ne ha bisogno:

- **Utilizzatori** — `IfcSanitaryTerminalType` con:
  - `PredefinedType` corretto: WASHHANDBASIN→lavabo, BIDET→bide, SHOWER→doccia,
    BATH→vasca, SINK→lavello, TOILETPAN→WC, URINAL→orinatoio
  - WC: volume cassetta in `Pset_SanitaryTerminalTypeCistern.CisternCapacity`
    (→ classe DU 4/6/7,5/9 l del prospetto 2)
  - porta template `IfcDistributionPort` SOURCE nested nel type
    (`IfcRelNests`), offset locale dall'origine e **Z = direzione di innesto**
    del tubo; le occorrenze ricevono porte specchiate (placement relativo:
    seguono l'elemento)
  - lavatrice/lavastoviglie: `IfcElectricAppliance(Type)` con PredefinedType
    WASHINGMACHINE / DISHWASHER
- **Recapito** — elemento libero (es. `IfcDistributionChamberElement` MANHOLE
  per l'allaccio fognario, TRENCH/SUMP per la dispersione) con porta SINK
- **Tubi/raccordi generati** — type per DN con
  `Pset_PipeSegmentTypeCommon.NominalDiameter`; occorrenze con
  `Pset_PipeSegmentOccurrence` (Gradient, InvertElevation, scabrezza) e
  `Qto_PipeSegmentBaseQuantities.Length`; porte SINK/SOURCE connesse
  tubo→raccordo→tubo; linea = `IfcDistributionCircuit` nel sistema DRAINAGE

Dove il pset custom resta necessario:

- `EN12056_Utilizzatore.Apparecchio` — solo per varianti che lo schema IFC non
  esprime: doccia **con tappo**, orinatoio a **valvola/parete**, pozzetti a
  terra per DN ("Segna Utilizzatore" lo scrive solo in questi casi)
- `EN12056_Recapito.TipoRecapito` — la destinazione amministrativa dello
  scarico (fognatura / corpo superficiale / suolo, D.Lgs 152/2006 Parte III)
  non ha uno slot standard
- `EN12056_Dimensionamento` — è **output** di calcolo con le citazioni della
  norma nazionale, per natura fuori dallo schema standard
