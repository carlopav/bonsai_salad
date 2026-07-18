// Report di dimensionamento linea impianto — bonsai_salad / mep
//
// Uso:
//   typst compile sizing_report.typ out.pdf --input data=path/al/dati.json
//
// Il JSON atteso ha forma:
// {
//   "progetto": "...",
//   "linea": "...",
//   "sistema": "geberit_pe" | "pvc_sn4_sn8" | ...,
//   "data": "2026-07-10",
//   "tratti": [
//     {
//       "id": "T1",
//       "da": "utilizzatore_01",
//       "a": "collettore_A",
//       "ud_cumulate": 2.5,
//       "dn_mm": 110,
//       "portata_ls": 4.0,
//       "pendenza_percento": 1.5,
//       "riempimento_hd": 0.5,
//       "riferimenti": [
//         {"norma": "UNI EN 12056-2:2001", "voce": "Tabella 6", "per": "DN minimo"},
//         {"norma": "UNI EN 12056-2:2001", "voce": "Tabella 8", "per": "pendenza"}
//       ]
//     }
//   ]
// }

#let dati = json(sys.inputs.data)

#set document(title: "Dimensionamento — " + dati.linea)
#set page(paper: "a4", margin: 2.5cm, numbering: "1")
#set text(font: "Source Sans Pro", size: 10pt, lang: "it")

#align(center)[
  #text(size: 16pt, weight: "bold")[Report di dimensionamento]
  #v(0.3em)
  #text(size: 11pt)[#dati.progetto — linea #dati.linea]
  #v(0.2em)
  #text(size: 9pt, fill: gray)[Sistema: #dati.sistema · Generato il #dati.data]
]

#v(1em)
#line(length: 100%, stroke: 0.5pt)
#v(1em)

#for tratto in dati.tratti [
  == Tratto #tratto.id — da #tratto.da a #tratto.a

  #table(
    columns: (auto, auto),
    stroke: 0.4pt,
    [*UD cumulate*], [#tratto.ud_cumulate],
    [*DN*], [#tratto.dn_mm mm],
    [*Portata*], [#tratto.portata_ls l/s],
    [*Pendenza*], [#tratto.pendenza_percento %],
    [*Grado di riempimento h/D*], [#tratto.riempimento_hd],
  )

  #v(0.5em)
  #text(size: 9pt, style: "italic")[Riferimenti normativi:]
  #for r in tratto.riferimenti [
    - #r.norma, #r.voce — #r.per
  ]
  #v(1em)
]
