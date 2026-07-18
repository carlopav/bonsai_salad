# catalog/ — librerie parti per sistema

Tre livelli separati per ogni sistema (vedi spec §5): `data.json` (quote
dimensionali), `articles.json` (mappatura articolo/prezzo, separata perché i
listini cambiano ogni anno), `geometry.py` (generatori parametrici per
famiglia di pezzo, non mesh statiche per SKU). Loader: `catalog/__init__.py`
(`available_systems()` / `load_system(name)`).

## Schema data.json

```json
{
  "tubi":     [{"dn": 110, "de_mm": 110, "spessore_mm": 4.3}],
  "raccordi": [{"tipo": "curva", "dn": 110, "angolo": 45, "fxf_mm": 60},
               {"tipo": "braga", "dn": 110, "dn_derivazione": 110,
                "angolo": 45, "fxf_mm": 120}]
}
```

`tipo`: curva | braga | riduzione | manicotto | tappo.

## Schema articles.json

```json
{
  "articoli": [{"tipo": "tubo", "dn": 110, "codice": "...", "prezzo_eur_m": null},
               {"tipo": "curva", "dn": 110, "angolo": 45, "codice": "...",
                "prezzo_eur": null}]
}
```

## Sistemi

- **geberit_pe** — scarico interno edificio, saldato, d32–d315. Da trascrivere
  dalle schede tecniche PDF Geberit (nessun export IFC nativo, vedi spec §5).
- **geberit_silent_pp** — scarico interno acustico, ad innesto. **Rimandato**
  (vedi spec §5). Stesso schema di geberit_pe.
- **pvc_sn4_sn8** — tratto esterno interrato, generico (nessun vendor fisso).
  Tubi popolati dalle quote nominali di norma
  (`norms/en_1401/extracted/dimensioni_sn4_sn8.json`), spessore = e_min della
  serie, range utile fognatura edificio DN 110–400. Mappatura dn_norma → misura commerciale (DN 100→110, 150→160, 300→315). Raccordi da popolare con
  quote Z vendor-specifiche se serve per un computo reale.
