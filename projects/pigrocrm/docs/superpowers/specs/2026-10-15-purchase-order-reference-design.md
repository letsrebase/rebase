# An invoice carries its purchase order and CIG (REB-693)

Date: 2026-10-15. Status: design approved in chat by Ivan on 2026-10-15, written down
here before the plan.

## 1. Why

A customer whose own client is a public body sends a purchase order («ordine
fornitore») that carries a CIG, the tender code that makes the payment traceable under
L. 136/2010. The customer needs the order and the CIG on our invoice, in the place the
FatturaPA format has for them: the `DatiOrdineAcquisto` block of `DatiGenerali`.

Today `FatturaPAExporter._dati_generali` (`packages/core/src/pigrocrm/core/invoices/
fatturapa.py`) writes only `DatiGeneraliDocumento`, and the only way to carry a CIG is
free text in `Causale` or in a line's `Descrizione`. The slice 3 spec (2026-08-20, out of
scope table) left CIG and CUP out together with FPA12. That exclusion was about the
format for the public administration. `DatiOrdineAcquisto` is part of FPR12 too, and
it is what a business-to-business invoice uses when the order is on the customer's
side.

## 2. What the design is

**One purchase order per invoice, for the whole document.** An invoice may carry one
order reference: its number, and optionally its date, its CIG and its CUP. The
reference covers every line, so `RiferimentoNumeroLinea` is never written. Several
orders on one invoice, and an order per line, are not part of this design (§ 9).

The reference belongs to the invoice, not to the customer or the deal: the same
customer sends a new order, with its own number and often its own CIG, for each job.

It follows the accrual period (ORB-61, migration 0033) in every respect that matters:
four columns on the row, editable while the document is a draft or a proforma, frozen
when it is issued, copied from a proforma onto the fattura it becomes, read off the
row by the exporter and by the PDF.

## 3. Data

Migration `0050_invoice_purchase_order` adds four nullable columns to `invoices`:

| Column | Type | XML element | Rule |
|---|---|---|---|
| `ordine_numero` | `VARCHAR(20)` | `IdDocumento` | 1 to 20 characters, Basic Latin |
| `ordine_data` | `DATE` | `Data` | a date |
| `ordine_cig` | `VARCHAR(15)` | `CodiceCIG` | exactly 10 letters or digits |
| `ordine_cup` | `VARCHAR(15)` | `CodiceCUP` | exactly 15 letters or digits |

One table CHECK, `ck_invoices_ordine_needs_numero`: `ordine_numero IS NOT NULL OR
(ordine_data IS NULL AND ordine_cig IS NULL AND ordine_cup IS NULL)`. A date, a CIG or a
CUP without the order they belong to is a fact with nothing to hang on, and
`IdDocumento` is the one mandatory child of the block.

No backfill and no index. Existing rows keep four nulls, which is "no order", and their
XML does not change. The columns reach every space at the first boot after the deploy,
through `ensure-space-defaults`, like every migration.

The CIG and CUP widths are the schema's (`String15Type`); the exact lengths are ANAC's
and the CUP's own format, enforced by the application so a missing character is caught
when it is typed rather than by the customer's accounts office.

## 4. Validation

In `invoices/schemas.py`, on `InvoiceCreate`, `InvoiceUpdate` and `InvoiceRead`, each
of the four fields optional:

- `ordine_numero`: stripped; empty after stripping is refused (the web sends `null`
  for an empty input); at most 20 characters; Basic Latin only (`String20Type` is
  `\p{IsBasicLatin}{1,20}`). An accented letter is refused with a message that says
  why, not transliterated: an order number is an identifier the customer will match
  character by character.
- `ordine_cig`: stripped and upper-cased, then exactly 10 characters `[A-Z0-9]`.
- `ordine_cup`: stripped and upper-cased, then exactly 15 characters `[A-Z0-9]`.
- `ordine_data`: a `date`. No rule against its position relative to the invoice date:
  an order is normally earlier, and nothing is gained by refusing the rare case.

The Pydantic widths mirror the columns (20, 15, 15), as every other field of these
schemas does; the rules above are the service's (§ 5), applied to the stripped value,
through a small pure module (`invoices/ordine.py`) so they are testable with no session.
Messages are Italian, in the shape the module already uses (`ValidationFailed(entity,
field, message, expected=...)`): «il CIG ha 10 caratteri, lettere e cifre», «il CUP ha 15
caratteri, lettere e cifre», «il numero d'ordine ammette solo caratteri latini di base».

The "needs a number" rule is the service's, on the merged row (§ 5), because on
`InvoiceUpdate` the number may already be on the row and not in the request.

## 5. Service

`InvoiceService` (`invoices/service.py`):

- `create` writes the four fields and checks the needs-a-number rule before the insert,
  raising `ValidationFailed` on `ordine_numero` («indica il numero dell'ordine a cui si
  riferiscono data, CIG e CUP»). The table CHECK is the second line.
- `update` treats the four like the accrual period: they are not in
  `MUTABLE_AFTER_ISSUE`, so after emission, and on a consumed proforma, the existing
  guard raises `ImmutableField` with no new code. While editable, an explicit `null`
  clears a field, and the needs-a-number rule is checked on the merged row: clearing the
  number while a CIG stays is refused, clearing all four together is allowed.
- `issue` from a proforma copies the four onto the fattura it creates, next to
  `competenza_da` and `competenza_a`. `issue` of a draft fattura keeps its own.
- The issue pre-check, which runs before the register number is spent, adds the order
  number, the CIG and the CUP to what `check_document_text_exportable` already measures
  (causale, lines), with the tag, width and pattern the writer will apply, so a value the
  exporter would refuse never costs a number, whichever path wrote it.
- `_for_export` and `_for_export_proforma` pass the four to `InvoiceForExport`, which
  gains them as optional fields.
- `import_issued` does not read them: an imported invoice keeps four nulls (§ 9).
- `describe_schema` needs no change: `schema_registry.native_fields` derives the native
  fields of `invoice` from `InvoiceCreate`, so the four appear as soon as the schema
  carries them.

## 6. XML

`FatturaPAExporter._dati_generali` writes, right after `DatiGeneraliDocumento` and only
when `ordine_numero` is set:

```xml
<DatiOrdineAcquisto>
  <IdDocumento>ODA-14</IdDocumento>
  <Data>2026-10-09</Data>          <!-- when ordine_data is set -->
  <CodiceCUP>...</CodiceCUP>       <!-- when ordine_cup is set -->
  <CodiceCIG>Z0A1B2C3D4</CodiceCIG> <!-- when ordine_cig is set -->
</DatiOrdineAcquisto>
```

The order is the schema's `DatiDocumentiCorrelatiType` sequence: `RiferimentoNumeroLinea*`
(never written), `IdDocumento`, `Data?`, `NumItem?` (never written),
`CodiceCommessaConvenzione?` (never written), `CodiceCUP?`, `CodiceCIG?`. CUP before CIG is
the schema's order, not a choice. Each value goes through `_text` with its width and the
Basic Latin pattern, the same single path every other text node takes.

## 7. PDF

`pdf.py` builds `fattura.riferimento_ordine` when `ordine_numero` is set, for example
«n. ODA-14 del 09-10-2026, CIG Z0A1B2C3D4, CUP …», leaving out the parts that are absent
and spelling the date with `format_data`, like every other date on the document.
`template-invoice.md`, the one template both the fattura and the proforma render
through, prints it under the accrual period: `Riferimento ordine:
{{fattura.riferimento_ordine}}`, inside an `{{#if}}` like the period.

## 8. Surfaces

- **API**: the four fields travel on `POST /api/invoices` and `PATCH /api/invoices/{id}`
  and come back on every `InvoiceRead`. `apps/web/src/lib/api-types.ts` is regenerated.
- **MCP**: `create_proforma` and `update_proforma` gain `ordine_numero`, `ordine_data`,
  `ordine_cig`, `ordine_cup`, described in Italian; `get_invoice` returns them through
  `InvoiceRead`. On `update_proforma`, as for every other field there, `None` means "not
  supplied", so an agent sets or changes the order but cannot clear it; clearing is the
  application's, where the four are one control.
- **Web**: a new `PurchaseOrderEditor`, rendered under `InvoiceHeaderEditor` while the
  document is editable, with a «Riferimento ordine» heading, four inputs (Numero ordine,
  Data ordine, CIG, CUP) and its own «Salva ordine» button. A component of its own rather
  than four more inputs in `InvoiceHeaderEditor`, whose section is «Date del documento»
  and whose button is «Salva date». It sends all four on save, `null` for an empty
  input, and refuses locally a date, CIG or CUP without a number («Indica il numero
  dell'ordine.»), so the common mistake is caught before the request; the server's
  answer is shown as it comes for everything else. When the document is no longer
  editable, the detail page shows a read-only «Riferimento ordine» row with the same text
  the PDF prints.

## 9. Not here

- FPA12 to the public administration (`CodiceUnivocoUfficio`, split payment): still out,
  for the slice 3 reasons.
- `DatiContratto`, `DatiConvenzione`, `DatiRicezione`: the same complex type, and a
  later card can add a `tipo` once one is needed. Nothing here blocks it.
- Several orders on one invoice, `RiferimentoNumeroLinea`, `NumItem`,
  `CodiceCommessaConvenzione`. Going from one order to several means a child table and a
  migration that moves these four columns into it; that cost is accepted.
- Reading `DatiOrdineAcquisto` back from an imported XML.
- Searching invoices by CIG or order number in `search_everything`.
- The order fields on `NewProformaDialog`: a proforma is created, then its header is
  edited, which is where the period and the due date already live.

## 10. Tests

- **Migration**: the 0050 upgrade on rows planted at 0049 leaves them with four nulls;
  the CHECK refuses a CIG without a number; the models-and-migrations agreement test
  passes.
- **Schemas**: each rule of § 4, accepted and refused, with the message.
- **Service**: create with and without a number; update while editable (set, change,
  clear all four, refuse clearing the number alone); `ImmutableField` after issue and on
  a consumed proforma; issue from a proforma copies the four; a draft fattura issues with
  its own.
- **XML**: an invoice with all four, with the number only, and with none; each output
  validated against `tests/fpr12/Schema_VFPR12_v1.2.3.xsd`; the block sits between
  `DatiGeneraliDocumento` and `DatiBeniServizi`, CUP before CIG.
- **PDF**: the line is present with the parts that exist and absent without a number, on
  an issued fattura and on a proforma.
- **API**: POST and PATCH round-trip, 422 for a bad CIG, 409 on an issued invoice.
- **MCP**: `create_proforma` and `update_proforma` set the fields; `get_invoice` shows
  them.
- **Web**: the header editor sends the four, `null` for empty, refuses a CIG without a
  number, and the detail page shows the read-only row on an issued invoice.

## 11. Rollout

The PR merges to `main` and reaches the preview. Production moves at the next
`pigrocrm-v*` tag, when Ivan asks for it, after the usual per-space `alembic_version`
check. Once live, the proforma that prompted this card gets its order through
`update_proforma`.
