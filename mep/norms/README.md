# norms/

Dati normativi usati dal motore di dimensionamento, organizzati per norma.

## Struttura per norma

- `raw/` — copia personale della norma, **solo locale, mai in git** (vedi
  `.gitignore` del modulo). Riferimento per la trascrizione, non distribuita.
- `extracted/` — tabelle numeriche trascritte in json, con citazione di
  norma/prospetto. Unico contenuto versionato: dati numerici derivati, non
  testo o immagini della norma. Fonti, schema e note di trascrizione sono nel
  README.md accanto ai json.

## Norme presenti

- `en_12056/extracted/` — UNI EN 12056-2:2001: DU per apparecchio, coefficiente
  K, diametri interni minimi, diramazioni, colonne, collettori, pendenze
  (un json per prospetto — vedi il suo README.md)
- `en_1401/extracted/` — UNI EN 1401-1:2019+A1:2023: diametri esterni e
  spessori SN4/SN8 (vedi il suo README.md)

Il loader è `mep/core/norms.py`; ogni valore porta con sé la citazione, usata
nel report Typst (`mep/reports/`) accanto a ogni risultato.
