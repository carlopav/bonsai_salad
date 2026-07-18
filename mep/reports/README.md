# reports/

Report di dimensionamento generato al momento della creazione degli elementi
di una linea (tratto di impianto), con riferimenti normativi accanto a ogni
valore calcolato.

## Flusso

1. `core/sizing.py` calcola il dimensionamento di una linea e scrive i
   risultati in un JSON temporaneo (tratti, DN, UD, portata, pendenza, e per
   ciascun valore il riferimento normativo preso da `mep/norms/*/extracted/`)
2. `templates/sizing_report.typ` legge quel JSON e compone il report
3. Compilazione: `typst compile templates/sizing_report.typ generated/<nome>.pdf
   --input data=<path_al_json>`

## File

- `templates/sizing_report.typ` — template, uno per tutte le linee (i dati
  variano, il layout no)
- `generated/` — output, non versionato (vedi `.gitignore` a root)
