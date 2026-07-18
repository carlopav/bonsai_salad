# en_12056/extracted/ — tabelle trascritte da UNI EN 12056-2:2001

Dati numerici derivati dalla norma (con citazione del prospetto), letti da
`mep/core/norms.py`. I commenti che nei sorgenti yaml accompagnavano i dati
sono raccolti qui, file per file.

## ud_apparecchi.json — prospetto 2

Unità di scarico (DU) per apparecchio sanitario, in l/s, per sistema (I–IV,
vedi §4.2; in Italia si adotta normalmente il Sistema I).

- `du` con valore `null` = non utilizzata o dati mancanti ("-" nella norma)
- WC con cassetta da 4,0 l: non ammessi nei sistemi I, III e IV ("\*\*")
- `orinatoio_a_parete`: valore **per persona** ("\*")
- WC nel sistema III: la norma dà un intervallo secondo il tipo di cassetta
  ("\*\*\*", valido solo per WC a cacciata con cassetta e sifone); è trascritto
  il massimo (6,0 l: 1,2–1,7 → 1.7; 7,5 l: 1,4–1,8 → 1.8; 9,0 l: 1,6–2,0 → 2.0)

## coefficiente_k.json — prospetto 3

Coefficiente di frequenza K, usato in Qww = K·√ΣDU (§6.3.1).

## diametri_minimi.json — prospetto 1

DN → diametro interno minimo (mm). Le capacità di scarico della norma sono
basate su questi diametri interni minimi (§6.2.1).

## diramazioni.json — prospetti 4 e 5

Diramazioni di scarico senza ventilazione: `capacita` (prospetto 4, Qmax → DN
per sistema) e `limiti` (prospetto 5); oltre i limiti la diramazione va
ventilata (§6.4.1). Il sistema III usa i prospetti 6 e 7 (non trascritti:
in Italia si adotta il sistema I).

- `dn` con valore `null` = non ammesso ("\*")
- `max_wc`: numero massimo di WC raccordabili (assente = nessun limite;
  0 = senza WC). Riga 2,25 l/s: sistema I max due WC e cambiamenti di
  direzione max 90° totali ("\*\*\*"); sistemi II/IV max un WC ("\*\*\*\*")
- `limiti.curve_90_max`: senza contare la curva di raccordo ("\*")

## colonne.json — prospetti 11 e 12

Colonne di scarico: capacità idraulica Qmax (l/s) per DN, sistemi I–IV.
`ventilazione_primaria` (prospetto 11) e `ventilazione_secondaria`
(prospetto 12, con DN della colonna di ventilazione).

- `min_dn_con_wc`: DN 80 minimo con WC raccordati secondo il sistema II ("\*");
  DN 100 minimo con WC nei sistemi I, III, IV ("\*\*")

## collettori.json — appendice B (informativa), prospetti B.1 e B.2

Capacità di collettori di scarico Qmax (l/s) e velocità v (m/s), calcolate con
Colebrook-White (kb = 1,0 mm; viscosità 1,31e-6 m²/s), per pendenza (cm/m) e DN.
`riempimento_50` = h/d 0,5 (B.1); `riempimento_70` = h/d 0,7 (B.2).
Per ogni DN il valore è `[Qmax l/s, v m/s]`.

**Refuso della norma**: nel prospetto B.1, DN 225 / pendenza 3,00 la norma
stampa "389,2"; trascritto **39.2** (coerente con i valori adiacenti 35,7–42,3
e con v = 2,1). Il prospetto B.3 (Qww) non è trascritto: è derivato dalla
formula del §6.3.1.

## pendenze.json — prospetto 5 e appendice B

Pendenze per la generazione geometrica dei tratti orizzontali. La norma non dà
una tabella esplicita pendenza min/max per DN: per le diramazioni senza
ventilazione vale la pendenza minima del prospetto 5 (per sistema); per i
collettori il range coperto dai prospetti B.1/B.2 è 0,5–5,0 cm/m e la pendenza
minima effettiva è quella per cui Qmax(DN, pendenza) ≥ Q di progetto.
