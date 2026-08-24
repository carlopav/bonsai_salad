# ifc_spaces — numerazione degli IfcSpace

Un nuovo tool di Bonsai Salad che rinomina gli IfcSpace **selezionati** con una
numerazione progressiva `prefissoNN`, ordinata piano per piano e, dentro il
piano, per vicinanza. L'unico attributo scritto è `Name`.

## Collocazione

Sottopannello `IfcSpaces` dentro `Schedules`
(`bl_parent_id = "BONSAI_SALAD_PT_schedules"`, `DEFAULT_CLOSED`), in una cartella
autonoma `ifc_spaces/`, sulla forma di `daylight_ventilation`:

```
ifc_spaces/
    __init__.py
    core/
        __init__.py
        numbering.py    # puro ifcopenshell, nessun bpy
    operator.py
    ui.py
tests/ifc_spaces/       # + stub in tests/conftest.py
```

Nessun `common.py`: `_storey` e `_elevation` vengono **duplicati** da
`daylight_ventilation/core/ratios.py`, commento incluso. `get_container` non
risolve il piano di uno IfcSpace — non essendoci `ContainedInStructure` su un
elemento spaziale, risale oltre il piano e restituisce `None`; serve
`ifcopenshell.util.element.get_parent(space, ifc_class="IfcBuildingStorey")`,
più il fallback che scorre `IfcRelContainedInSpatialStructure` per i file
(schema-invalidi ma diffusi) che contengono lo space invece di aggregarlo.

## L'algoritmo

Tutto in `core/numbering.py`, senza `bpy`:

```python
plan(ifc_file, spaces, prefix, rename_named=True) -> Plan
```

1. **Raggruppamento.** I locali si raggruppano per piano. I piani si ordinano
   con la stessa chiave con cui Bonsai ordina il proprio albero dei container
   (`tool/spatial.py`): `(get_storey_elevation(storey), storey.Name)`.

   `ifcopenshell.util.placement.get_storey_elevation` legge la Z del placement
   e ripiega su `Elevation` solo per un piano che un placement non ce l'ha.
   Leggere `Elevation` per prima è quello che la prima versione faceva, e non
   regge sui file veri: l'attributo è opzionale in ogni schema (deprecato in
   IFC4X3 a favore del placement) e un progetto reale lo lascia nullo su
   **tutti** i piani, che così pareggiano fra loro e lasciano decidere l'ordine
   di selezione — la numerazione partiva dal piano primo o dal terra a seconda
   di come erano stati selezionati. Il nome chiude il pareggio fra due piani
   alla stessa quota, ancora come fa Bonsai; l'ordine dei piani in numerazione
   è quindi lo stesso che si vede nell'albero dello Spatial Decomposition.

2. **Misure.** Per ogni locale:
   - **area** — `NetFloorArea` da `Qto_SpaceBaseQuantities`
     (`should_inherit=False`) quando c'è, altrimenti l'area di footprint da
     `ifcopenshell.geom.create_shape`;
   - **baricentro** — centro del bounding box XY della shape, in coordinate
     mondo. Non la media dei vertici: una tassellazione più fitta su un lato la
     sposterebbe, e l'ordine della catena cambierebbe con la geometria e non
     con la pianta.

   L'area serve solo a **confrontare**: l'unità di progetto non cambia
   l'ordinamento, purché la stessa grandezza sia confrontata con sé stessa.

3. **Catena, dentro il piano.** Si parte dal locale di area maggiore. A ogni
   passo il successivo è il locale non ancora visitato col baricentro XY più
   vicino a quello dell'**ultimo numerato** (greedy nearest-neighbour: la
   numerazione percorre il piano come ci si cammina dentro). Pareggi risolti in
   modo deterministico — distanza, poi X, poi Y, poi `GlobalId` — così due
   lanci sullo stesso file danno lo stesso risultato.

4. **Contatore.** Uno solo, che attraversa i piani nell'ordine: il primo piano
   prende 01..07, il successivo riprende da 08. Cifre =
   `max(2, len(str(numero di locali effettivamente rinominati)))`: fino a 99
   due cifre, da 100 in su tre. `Name = f"{prefix}{n:0{pad}d}"`. Il prefisso può
   essere vuoto.

5. **Locali già nominati.** Un locale è "già nominato" quando `Name` non è
   `None` e non è vuoto una volta ripulito dagli spazi.
   `rename_named=True` (default) li rinumera come gli
   altri. Con `rename_named=False`, un locale già nominato resta
   **tappa della catena** — l'ordine continua a seguire la pianta — ma non
   consuma un numero: i rinominati prendono 01, 02, 03… senza buchi.

6. **Ritorno.** `Plan` porta le assegnazioni `(space, nuovo_nome)` nell'ordine,
   e separatamente i locali non collocabili.

## Blocco e scrittura

Prima di scrivere alcunché, l'operatore verifica che **ogni** IfcSpace
selezionato sia collocabile: ha un piano, e una geometria da cui `create_shape`
ricava un baricentro — non collocabile è quindi il locale senza
`Representation`, quello su cui `create_shape` solleva `RuntimeError`, e quello
la cui shape non ha vertici. Vale anche per i locali che `rename_named=False`
escluderebbe dalla rinomina — restano tappe della catena, quindi il baricentro e
il piano servono comunque.

Se anche uno solo non lo è: **nessuna scrittura**, `report({"ERROR"})` col
conteggio, e la **selezione viene sostituita con i soli locali problematici**
(il primo come attivo), così l'utente li ha davanti agli occhi. Gli oggetti
selezionati che non sono IfcSpace sono ignorati in silenzio: il pannello si
chiama IfcSpaces.

Scrittura, per locale:

```python
ifcopenshell.api.attribute.edit_attributes(ifc_file, product=space, attributes={"Name": nuovo})
tool.Root.set_object_name(obj, space)   # il nome nell'outliner non resta indietro
```

Nessun altro attributo, nessun pset, `LongName` intatto.

A fine corsa un `INFO`: *N locali rinominati su M piani*, più *K già nominati
lasciati intatti* quando l'opzione è spenta. Se un nome generato coincide col
`Name` di un locale **non toccato** in questa passata, un `WARNING` col
conteggio: segnala, non blocca — l'utente ha chiesto quella numerazione.

## UI

Una riga sola, tre elementi in fila:

- il campo del prefisso — `StringProperty`, default vuoto, senza etichetta
- l'interruttore `rename_named` — `BoolProperty`, default `True`, ridotto alla
  sola icona: su una riga non c'è spazio per "Rinumera anche i locali già
  nominati", che resta nel tooltip
- il bottone, anch'esso a sola icona, disabilitato quando non c'è nessun
  IfcSpace selezionato — solo il bottone aspetta una selezione, il prefisso si
  può scrivere prima

Nessuna anteprima: l'annullamento è Ctrl+Z, che in Blender copre anche la
scrittura IFC. Una lista di anteprima richiederebbe una cache da invalidare a
ogni cambio di selezione, prefisso od opzione.

## Test

In `tests/ifc_spaces/`, ifcopenshell puro, senza Blender:

- **catena** — pianta costruita a mano, con l'ordine atteso scritto per esteso
- **padding** — 99 locali danno due cifre, 100 ne danno tre
- **contatore fra piani** — due piani ordinati per quota, il secondo riprende
  dal numero dopo l'ultimo del primo
- **piani senza `Elevation`** — tre piani che dichiarano la quota solo nel
  placement (il caso del progetto vero) si numerano dal basso
- **piani alla stessa quota** — l'ordine cade sul nome, e non cambia
  invertendo la selezione
- **opzione spenta** — il locale già nominato fa da tappa, non consuma numero,
  e i rinominati restano contigui
- **blocco** — locale senza piano, e locale con geometria illeggibile: zero
  scritture e l'elenco esatto dei colpevoli
- **millimetri** — un file in mm produce lo stesso ordine di uno in metri
- **determinismo** — due locali equidistanti danno lo stesso ordine a ogni
  lancio

## Fuori perimetro

Rinumerare da un pannello di lista invece che dalla selezione; scrivere
`LongName` o un pset; una numerazione che incorpori il numero di piano nel
prefisso; un'anteprima confermabile.
