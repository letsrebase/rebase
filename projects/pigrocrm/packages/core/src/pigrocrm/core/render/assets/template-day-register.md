<!-- packages/core/src/pigrocrm/core/render/assets/template-day-register.md

The register of a contract's days a client asks for next to an invoice (REB-752). Laid out
like the timesheet beside it (`template-time-report.md`): the same title block, the same
thin dividers, the issuer's identity drawn once by `header.typ.template` and not here.

Every figure arrives finished from `ContractRegisterService.variables_for`, the same
function the CSV reads, so the two files cannot disagree about a total. The approval cell
is the proof behind the day (channel, sender, the day it arrived and the message id the
client can look up in their own mailbox), printed verbatim and never summarised.
-->

```{=typst}
#v(0.4cm)
#text(size: 16pt, weight: "bold")[Registro giornate]
#v(0.15cm)
#text(size: 9pt)[Periodo: {{periodo}} · Data emissione: {{oggi}}]
#v(0.35cm)
#line(length: 100%, stroke: 0.6pt + luma(180))
#v(0.35cm)
#grid(
  columns: (1fr, 1fr),
  column-gutter: 0.6cm,
  [
    #text(size: 8pt, fill: luma(110))[CLIENTE] \
    #text(size: 10pt, weight: "bold")[{{cliente.ragione_sociale}}] \
    #text(size: 9pt)[{{cliente.partita_iva}}]
  ],
  [
    #text(size: 8pt, fill: luma(110))[CONTRATTO] \
    #text(size: 10pt, weight: "bold")[{{contratto.titolo}}]
  ],
)
#v(0.5cm)
#table(
  columns: (0.14fr, 0.1fr, 0.3fr, 0.12fr, 0.34fr),
  align: (left, right, left, left, left),
  stroke: none,
  table.header(
    [#text(size: 8pt, fill: luma(110))[DATA]],
    [#text(size: 8pt, fill: luma(110))[QUANTITÀ]],
    [#text(size: 8pt, fill: luma(110))[DESCRIZIONE]],
    [#text(size: 8pt, fill: luma(110))[FATTURA]],
    [#text(size: 8pt, fill: luma(110))[APPROVAZIONE]],
  ),
  {{#each voci}}[{{data}}], [{{quantita}}], [{{descrizione}}], [{{fattura}}], [{{approvazione}}],{{/each}}
)
#v(0.35cm)
#line(length: 100%, stroke: 0.6pt + luma(180))
#v(0.25cm)
#grid(
  columns: (1fr, auto),
  [#text(size: 9pt, fill: luma(110))[{{numero_voci}} giornate]],
  [#text(size: 11pt, weight: "bold")[Totale: {{totale_quantita}}]],
)
```
