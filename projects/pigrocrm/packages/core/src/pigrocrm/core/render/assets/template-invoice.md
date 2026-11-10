```{=typst}
#let muted = rgb("#465362")
#let divider = rgb("#E2E2E2")
{{#if fattura.dichiarazione_proforma}}

#block(fill: rgb("#F9DC5C"), inset: 8pt, radius: 4pt, width: 100%)[
  #align(center)[#text(size: 11pt, weight: "bold")[{{fattura.dichiarazione_proforma}}]]
]

#v(12pt)
{{/if}}

#text(size: 9pt, fill: muted)[{{fattura.etichetta}} | Numero: {{fattura.numero}} | Data: {{fattura.data}}]
{{#if fattura.periodo_competenza}}

#text(size: 9pt, fill: muted)[Periodo di competenza: {{fattura.periodo_competenza}}]
{{/if}}
{{#if fattura.riferimento_ordine}}

#text(size: 9pt, fill: muted)[Riferimento ordine: {{fattura.riferimento_ordine}}]
{{/if}}

#v(14pt)
#text(size: 9pt, weight: "bold", fill: muted)[Committente]
#v(6pt)
#stack(
  spacing: 2.5pt,
  [#text(size: 9pt)[{{cliente.riga_identita}}]],
  [#text(size: 9pt)[{{cliente.riga_recapiti}}]],
)

#v(18pt)
#text(size: 9pt, weight: "bold", fill: muted)[Dettaglio]
#table(
  columns: (0.62fr, 0.14fr, 0.24fr),
  align: (left, center, right),
  inset: (x: 4pt, y: 6pt),
  stroke: none,
  table.header(
    [#text(size: 8pt, weight: "bold", fill: muted)[DESCRIZIONE]],
    [#text(size: 8pt, weight: "bold", fill: muted)[%IVA]],
    [#text(size: 8pt, weight: "bold", fill: muted)[PREZZO TOTALE]],
  ),
  {{#each righe}}[#text(size: 9pt)[{{descrizione}}]], [#text(size: 9pt)[{{iva}}]], [#text(size: 9pt)[{{prezzo_totale}}]],{{/each}}
)
{{#if fattura.mostra_totali}}

#v(6pt)
#align(right)[
  #table(
    columns: (auto, auto),
    align: (left, right),
    inset: (x: 4pt, y: 4pt),
    stroke: none,
    [#text(size: 9pt, fill: muted)[Imponibile]], [#text(size: 9pt)[{{fattura.imponibile}}]],
    [#text(size: 9pt, fill: muted)[Imposta]], [#text(size: 9pt)[{{fattura.imposta}}]],
    [#text(size: 10pt, weight: "bold")[Totale documento]], [#text(size: 10pt, weight: "bold")[{{fattura.totale}}]],
  )
]
{{/if}}

#v(18pt)
#text(size: 9pt, weight: "bold", fill: muted)[Modalita pagamento]
#table(
  columns: (0.2fr, 0.44fr, 0.16fr, 0.2fr),
  align: (left, left, center, right),
  inset: (x: 4pt, y: 6pt),
  stroke: none,
  table.header(
    [#text(size: 8pt, weight: "bold", fill: muted)[MODALITA PAGAMENTO]],
    [#text(size: 8pt, weight: "bold", fill: muted)[DETTAGLI]],
    [#text(size: 8pt, weight: "bold", fill: muted)[SCADENZE]],
    [#text(size: 8pt, weight: "bold", fill: muted)[IMPORTO]],
  ),
  [#text(size: 9pt)[{{pagamento.codice}}]],
  [#text(size: 9pt)[{{pagamento.dettagli}}]],
  [#text(size: 9pt)[{{fattura.data_scadenza}}]],
  [#text(size: 9pt)[{{fattura.totale}}]],
)

#v(18pt)
#line(length: 100%, stroke: 0.6pt + divider)
{{#if fattura.dichiarazione_regime}}

#v(8pt)
#text(size: 8pt, fill: muted)[{{fattura.dichiarazione_regime}}]
{{/if}}
{{#if fattura.dichiarazione_bollo}}

#v(4pt)
#text(size: 8pt, fill: muted)[{{fattura.dichiarazione_bollo}}]
{{/if}}

#v(16pt)
#grid(
  columns: (1fr, 1fr),
  [#text(size: 12pt, weight: "bold")[Thank you!]],
  [
    #align(right)[
      #stack(
        spacing: 2.5pt,
        {{#if emittente.email}}[#text(size: 9pt)[{{emittente.email}}]],{{/if}}
        {{#if emittente.telefono}}[#text(size: 9pt)[{{emittente.telefono}}]],{{/if}}
        {{#if emittente.sito_web}}[#text(size: 9pt)[{{emittente.sito_web}}]],{{/if}}
      )
    ]
  ],
)
```
