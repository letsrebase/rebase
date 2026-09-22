# Mapping mastro's invoice-import parser onto PigroCRM's own import path

**Date:** 2026-09-23
**Scope:** whether mastro's neutral `Invoice` type and FatturaPA-1.2 format adapter port
as-is or get reworked for PigroCRM; how direction detection maps onto a per-space CRM;
whether a parsed-but-unwritten invoice becomes a review-before-write step; how the new
path interacts with PigroCRM's existing `importata_da: "esterno"` path so the two do not
diverge on what an imported invoice's row looks like.
**Depends on:** REB-344 (mastro's ledger spike) landing before day/rate-card
reconciliation of an imported invoice's lines is possible — §6 below is the only piece
this document leaves unimplementable until then.
**Gate:** this project's own description states "no implementation issue is filed until
Lorenzo signs off on each spike's entity mapping." §7 below is therefore a proposed
implementation order written *inside this document*, not a set of filed Linear issues.

---

## 1. What mastro's import pipeline actually is today

The issue's own **Observed** paragraph quotes mastro's README ("What works today" —
"parsing is real and tested; writing a parsed invoice into the ledger is the next piece
of work, not this one"). Reading the code directly (`~/projects/personal/mastro`) shows
the README is behind the tree it describes: the write path it says is "not this one" is
already built, tested, and considerably more complete than a parser alone. This matters
for the design below, because the safer, cheaper move is to copy an already-working
shape rather than to design a review/confirm split from a green field.

The pipeline, file by file:

- `src/lib/server/import/adapter.ts:9-19` — `ImportableFile` (filename + raw bytes).
  `:28-53` — `InvoiceFormatAdapter`: `detect(file)` must be cheap and total (never
  throw, even on garbage bytes), `parse(file)` returns one array entry per invoice the
  file actually carries (a FatturaPA "lotto" batch can carry several, per the doc
  comment at `:47-51` referencing issue #101), and is only ever called after `detect`
  returned `true`.
- `src/lib/server/import/invoice.ts:169-196` — the neutral `Invoice` shape (`number`,
  `issueDate`, `documentType`, `currency`, `supplier`/`customer` as `InvoiceParty`
  (`:40-63`), `lines`, `taxSummary`, `taxableAmount`/`taxAmount`/`total`, optional
  `stampDuty`, `socialSecurityCharges`, `paymentTerms`, `transmission`). Named after
  what an e-invoice standard generally carries (the module comment cites EN 16931),
  never after FatturaPA's own vocabulary, so a second format adapter is a translation
  into this shape rather than a change to it.
- `src/lib/server/import/registry.ts:11-23` — one `Map<string, InvoiceFormatAdapter>`;
  adding a format is a new file under `formats/` plus one registration line, never a
  change to the engine.
- `src/lib/server/import/importer.ts:19-49` — `resolveAdapter`/`importFile`: only tries
  adapters the *active jurisdiction pack* declares support for (so the generic pack,
  which declares none, never resolves one), and a file no adapter claims comes back
  `unclaimed` rather than throwing or being silently dropped.
- `src/lib/server/import/direction.ts:38-49` — `classifyDirection(supplierTaxId,
  accountHolderTaxId)`: equal (case/whitespace-insensitive) means the account holder
  issued it — `outgoing`, revenue; anything else is `incoming`, with the reason
  attached. `:63-71` classifies a full parsed `Invoice` the same way, always reading
  `invoice.supplier.taxId` and never `invoice.transmission` (the module comment at
  `:1-9` explains why: an invoicing service transmitting on the account holder's behalf
  appears as `transmission`, and the account holder is still the `supplier`).
  `:81-90` — `revenueEligibleInvoices` is the *only* function anything computing
  revenue should call, specifically to stop the mistake of re-deriving the
  `kind === 'outgoing'` filter at each call site.
- `src/lib/server/import/dedup.ts:41-48` — `naturalInvoiceKey` on
  `(normalizedTaxId(supplier), number, year)`, plus every content hash ever attached to
  that invoice (structured document and any PDF alike), so a same-batch repeat and a
  repeat of a past import are recognised by one comparator.
- `src/lib/server/import/client-match.ts:1-8,166-212` — exact tax-id match against
  clients on record; when nothing matches, `buildClientContractProposal` builds a
  proposal (never a write) from every invoice found for that one new customer in the
  batch, so three invoices from a new counterparty produce one proposal, not three.
- `src/lib/server/import/review.ts:1-14` — `buildReview` is **pure**: no database
  access. Existing clients, existing invoices and eligible days are all passed in, so
  it is unit-tested exactly like `importer.ts`, and the actual route handler is "a thin
  wrapper doing only the I/O". This is mastro's own review step, and it holds **no
  staging table** — the reviewed-but-unconfirmed state lives in the browser, between
  one HTTP round trip and the next, never in the database.
- `src/lib/server/import/confirm.ts:1-12,36-49` — the *only* place client/contract
  proposals are written, one transaction, "nothing reaches the ledger until a human
  calls this... per invariant 3" (agents propose, humans confirm).
- `src/lib/server/import/persist.ts:1-13` — the *only* place an invoice itself is
  written. Crucially, it **re-parses the file from its own bytes** rather than trusting
  whatever `review.ts` computed a moment ago: "the structured document wins... simpler
  than serialising the parsed `Invoice` back and forth and trusting the client not to
  have tampered with it". `:91-112` (`mapInvoiceToInput`) maps the neutral `Invoice`
  onto the `invoice` table's flat columns, folding `taxSummary`/`socialSecurityCharges`
  arrays down to single columns exactly the way a human filling in the manual invoice
  form already has to. `:132-261` (`persistImportedInvoice`) is one transaction: re-parse,
  re-classify direction (refusing to persist an `incoming` invoice as revenue), re-check
  the natural key/hash against current state (not what review computed), insert the
  invoice and its lines, store the document, and — `:218-232` — supersede any pending
  agent-proposed invoice for the same natural key, since a real structured document
  always outranks a lower-confidence PDF-derived guess (see `agent/invoice-producer.ts:10-13`).
- `src/lib/server/import/formats/fattura-pa/adapter.ts:1-51` — the concrete FPR12
  adapter: `detect` decodes UTF-8 and checks `@_versione`/`FormatoTrasmissione` both
  equal `"FPR12"`, wrapped in `try/catch` so a bug on unexpected bytes returns `false`
  rather than crashing a scan of many candidate files; `parse` maps the parsed XML tree
  through `mapFatturaPaToInvoices` (`map.ts`) onto the neutral `Invoice[]`.

None of this changes the issue's own conclusion that PigroCRM's existing
`importata_da: "esterno"` path is a different thing (confirmed below, §2), but it does
change the shape of what "porting" mastro's pipeline actually means: not writing a
parser from a blank page, but translating an already-tested four-stage pipeline
(detect/parse → classify/dedup/match → review, pure → confirm/persist, one transaction)
onto PigroCRM's own domain and conventions.

## 2. PigroCRM's existing import path, confirmed by reading it

- `packages/core/src/pigrocrm/core/invoices/schemas.py:340-395` — `InvoiceImport`: a
  fattura issued elsewhere, **declared field by field** (`anno`, `numero`,
  `data_emissione`, `righe`, `imponibile`/`imposta`/`bollo`/`totale`, ...), never parsed
  from a document. The docstring at `:343-352` is explicit: "the counter follows [the
  caller's numbers]... totals are declared **and** verified, never recomputed" — the
  caller types the numbers, the service only checks they are internally consistent.
  `:395` — `importata_da: Literal["esterno"] = "esterno"`.
- `packages/core/src/pigrocrm/core/invoices/models.py:127-133` — the `importata_da`
  column, `String(20)`, `NULL` for the ordinary (native) case: "a string rather than a
  boolean because the *kind* of provenance is the fact worth keeping: a second
  migration one day would not be 'imported = true' twice." No `CHECK` constraint ties
  its value to `xml_hash_sha256`/`xml_document_id` being null — that pairing is a
  service-level convention today (§5 below turns on exactly this point).
- `packages/core/src/pigrocrm/core/invoices/service.py:1059-1093` —
  `InvoiceService.import_issued`'s own docstring already states the shape mastro's
  `persist.ts` independently converged on: every read/validation happens *before* the
  register lock (`:1094-1181`), the write happens once, last, in one transaction
  (`:1182` onward), and "nothing is flushed before every refusal has had its chance."
  `:2039-2046` (`export_xml`) — the existing refusal, unconditional on **any** non-null
  `importata_da`: "fattura importata: l'XML è quello già trasmesso allo SdI dal sistema
  che l'ha emessa, questo CRM non ne produce un secondo."
- `packages/core/src/pigrocrm/core/invoices/fatturapa.py:646` —
  `FatturaPAExporter`: "`InvoiceForExport` in, `bytes` out." One direction only. Nothing
  in this 1238-line module reads a FatturaPA XML file; it is a generator, confirmed by
  its own imports (`:33-51`) and by the fact `test_invoice_import.py` (the existing test
  suite for the `esterno` path) exercises gap declaration, never XML parsing.
- The design record for the existing path,
  `docs/superpowers/specs/2026-09-04-slice-9-import-storico-e-google-drive-design.md`
  §3.1-3.6, confirms the same: a hand-typed migration of Ivan's previous system's
  already-issued invoices, with a badge on the invoice list ("importata dal gestionale
  precedente") and no XML/PDF the CRM itself produced.

**Confirmed: the two are genuinely different capabilities**, exactly as the issue
states. `import_issued` lets a human re-type a document that already exists elsewhere.
Nothing in PigroCRM today can take a FatturaPA XML file's bytes and derive a row from
them.

## 3. Direction detection on a per-space CRM

mastro's `classifyDirection` needs one external fact: the account holder's own tax id,
read from configuration (`config.ts`), because mastro is one deployment for one
consultant. PigroCRM is not: `docs/superpowers/specs/2026-09-08-spazi-un-database-per-tenant-design.md`
§1 states the whole principle this maps onto — "PigroCRM è single-tenant per
costruzione... nessun servizio, nessuna tabella e nessuna query imparano cosa sia un
tenant" (no service, table or query learns what a tenant is; each space is its own
Postgres database, reached over its own connection).

That means the "account holder tax id" mastro reads from a config file is, in PigroCRM,
simply **this space's own `EmitterProfile` row** — `emitter/models.py:7`,
`partita_iva`/`codice_fiscale`, "one row, ever," already the field
`fatturapa.py`'s exporter itself reads as `IdFiscaleIVA`/`CodiceFiscale` for the issuer
block (`fatturapa.py:924-943`). No new tenant-aware plumbing is needed: whichever
space's database the request opened is the only `EmitterProfile` row the classifier can
even see. This is the direct, concrete answer to "how does direction detection map onto
a per-space CRM": it doesn't need to, because the tenancy boundary already does the
scoping mastro's config file does by hand.

The harder half of the question — "where 'outgoing' already has a strong meaning" — is
also answered by reading the schema rather than assuming: every row in PigroCRM's
`invoices` table already means "this space issued this" (`customer_id` is a foreign key
to *whom the space billed*; there is no `supplier_id`, no `fornitore` tax id, nothing
representing money owed *to* someone else). So mastro's "outgoing" classification and
PigroCRM's whole-table meaning are the same fact stated twice, not two concepts to
reconcile. What genuinely needs a decision is the *other* branch: a parsed invoice
where the supplier is not this space's own `EmitterProfile` — an **incoming** invoice, a
supplier's bill received by the space, for which PigroCRM has **no representation at
all** today. Confirmed by reading, not assumed:

- `grep -rn "fornitore\|supplier\|passive" packages/core/src/pigrocrm/core` returns six
  hits: one cosmetic (a docstring example at `documents/service.py:562`) and five
  substantive ones, all naming the same single field from five different angles —
  `timetracking/schemas.py:195,210,224` (the Pydantic shape), `timetracking/costs.py:139`
  (the write), `timetracking/models.py:246` (the column) — `Cost.fornitore`, a
  free-text `String(200)` on the existing expense-tracking table.
- `timetracking/models.py:198-224` (`Cost`) and `timetracking/costs.py:35-49`
  (`CostService`, "Real money out, with a receipt") are PigroCRM's closest existing
  analogue to "a bill from someone else" — but it is a manual entry: `importo`,
  `descrizione`, `fornitore` (a plain string, not a tax id), `document_id` (an optional
  receipt), no invoice number, no VAT breakdown, no XML, and nothing derives it from a
  parsed document. REB-344's own "Checked 2026-09-22" note already flagged
  `Cost`/`CostCategory` as the reuse candidate for mastro's *rebillable-expense*
  concept generally — a separate question from invoice-import direction, and not this
  spike's to resolve.

**Decision: an incoming invoice is parsed, classified, reported, and never written.**
This mirrors mastro's own `incoming_skipped` outcome (`direction.ts:51-57`) exactly,
for the same reason mastro itself gives — nothing forces revenue and expense into the
same table just because both arrived as a structured document. The review step (§4)
surfaces an incoming invoice's supplier identity and total so a human can see it was
found and deliberately not filed anywhere, rather than silently discarding it. Whether
PigroCRM ever grows a real "incoming invoice → `Cost`" bridge is future work, named as
an open question in §7, not a decision this document makes.

## 4. The review-before-write step

The issue asks whether a parsed-but-unwritten invoice becomes a review-before-commit
step, "matching mastro's own... split and PigroCRM's own 'agents propose, humans
confirm'-adjacent activity/audit pattern (`pigrocrm.core.activities`)." Reading that
module directly settles what it actually is: `activities/service.py:38-68`
(`ActivityService.record`) and `activities/schemas.py:8-18` (`ActivityRead`) are an
**append-only audit trail** — a `kind`, a `payload`, an actor, a timestamp, attached to
an entity that already exists. It records what already happened; it holds no
"proposed, not yet confirmed" state and was never meant to. It is "adjacent" to a
propose/confirm pattern only in the sense that both are about writing down who did
what, not because either is a staging area.

Reading mastro's own `review.ts` shows it does not need one either: `buildReview` is
pure, with no database writes, and the "proposed" state lives nowhere more permanent
than the HTTP response body and the browser's own memory between the review call and
the confirm call. This is the shape to copy, not a new staging table: PigroCRM already
has an almost-identical two-phase discipline inside `import_issued` itself
(§2 — every check runs before the lock, one commit at the end). The new work is one
level up: a **read-only review call**, then a **confirm call that re-derives everything
from the original bytes** rather than trusting the review response.

Concretely, two new surfaces (REST + MCP, mirroring the existing pair
`import_issued_invoice`/`declare_invoice_register_gaps` in
`apps/mcp/src/pigrocrm_mcp/tools/privileged.py:176-228`, gated the same way behind
`mcp_full_access`). **Neither surface carries raw file bytes as a parameter.** MCP
never does, by an explicit, already-enforced project rule — "the download of bytes
never goes through MCP... every tool below returns an identifier -- the bytes are
fetched separately, over the REST API" (`apps/mcp/src/pigrocrm_mcp/tools/__init__.py:662-677`)
— and, symmetrically, this design never has it accept one either. So both tools take a
`document_id` (or a list of them, for a multi-file review): the same shape
`import_issued`'s own `pdf_sorgente` schema already uses for the PDF attachment slot
(`{document_id} | {drive_file_id}`, `invoices/schemas.py:300-337`) — applied here to
the primary structured document instead of only the secondary PDF. A file becomes a
`document_id` through one of two existing doors, never a new one:
`POST /api/documents/upload` (REST, slice 2) for a direct upload, or `import_drive_file`
(below) for a file already sitting on Drive. Both tools then read the row's own stored
bytes back from the document store — never a caller-supplied copy — before parsing.

- **`review_invoice_import`** — input: one or more `document_id`s. No database write.
  For each: read the stored bytes back, run every registered adapter's `detect`
  (initially, one: FPR12), `parse` into the neutral shape (§5), classify direction
  against this space's own `EmitterProfile` (§3), check the natural key
  (`anno`/`numero`) against the existing register via the same repository methods
  `import_issued` already calls (`InvoiceRepository.numbers_present`,
  `.declared_gaps`), and match the customer by `partita_iva`/`codice_fiscale`
  (`customers/repository.py:42-45` already runs this exact `ilike` lookup for its own
  fuzzy search; the review step reuses the same two columns for an exact match instead).
  Output: one row per parsed invoice, tagged `ready`, `needs_customer_confirmation`,
  `already_present`, `conflict` (same number with different bytes, or with no stored
  hash to compare against at all — see §7 item 2), or `incoming_skipped` — never a
  silent drop, mirroring `importer.ts:31-33`'s `unclaimed` outcome for a file no
  adapter recognises at all.
- **`confirm_invoice_import`** — input: the same `document_id`(s) plus the human's
  decisions (which customer to attach when the review proposed creating one; which
  lines to keep — PigroCRM's own `InvoiceLineImport` schema, `schemas.py:274-297`,
  already has exactly this "declared line" shape). **Re-reads and re-parses the
  document's own stored bytes**, never trusts the review response as authoritative
  (mastro's own stated reason at `persist.ts:6-13` — "the structured document wins" —
  applies here unchanged), re-checks direction and duplication against the database's
  current state (not review time's), and then converges onto `import_issued` itself
  (§5) for the actual write, inside one transaction.

**On the Drive question the project description raises explicitly** ("each of those
spikes should say... how a document already sitting in PigroCRM... becomes a
review-before-write proposal"): `apps/mcp/src/pigrocrm_mcp/tools/drive_privileged.py:246-254`
(`import_drive_file`) already archives XML bytes untouched into a `documents` row
("Archivia solo i tipi che il CRM conserva -- PDF, `.docx`, `.xlsx`, testo, PNG/JPEG,
**XML** e i documenti Google") — `application/xml` is already in `ALLOWED_CONTENT_TYPES`
(`documents/schemas.py:36-45`), and its result is exactly the `document_id` this
design's two tools already require. That is the correct door for a FatturaPA file, not
`read_drive_file` (`:197-223`): that tool extracts *text*, truncated at
`PIGROCRM_DRIVE_TEXT_MAX_BYTES`, and its own docstring lists only "PDF, documento
Google, `.docx`, `.md`/`.txt`" as formats it actually reads — no XML support, and text
extraction is lossy in exactly the way a byte-for-byte parser cannot tolerate. One
caveat, also confirmed by reading: `docs/superpowers/specs/2026-09-08-spazi-un-database-per-tenant-design.md`
§5 states "per gli spazi Gmail e Drive **non esistono**" (Drive is root-only until a
space connects its own Google account, itself future work) — so on a space,
`POST /api/documents/upload` is the only door to a `document_id` until that lands; that
is an existing, separately-tracked limitation, not something this spike changes.

## 5. Converging with `importata_da: "esterno"` instead of diverging from it

This is the issue's fourth, and most structurally important, question. The neutral
parsed shape must **not** become a second, independently-maintained write path with its
own register rules, its own counter handling, and its own chance to disagree with
`import_issued` about a gap or a chronological check. The concrete design:

1. **New Pydantic schemas, not a reuse of `InvoiceImport`.** `ParsedInvoice`,
   `ParsedInvoiceParty`, `ParsedInvoiceLine` (mirroring mastro's `invoice.ts:40-63,169-196`
   field-for-field, translated into PigroCRM's own vocabulary — `imponibile`/`imposta`
   where mastro says `taxableAmount`/`taxAmount`, etc.) are a distinct concern from
   `InvoiceImport`: one is "what an XML document states," the other is "what a caller
   declares." Collapsing them into one schema would force every future parser
   (a second format, one day) to also satisfy `InvoiceImport`'s hand-declaration
   contract (`extra="forbid"`, `Literal["esterno"]`), which has nothing to do with
   parsing.
2. **One write function.** `confirm_invoice_import` maps the confirmed `ParsedInvoice`
   onto `InvoiceImport`'s own fields — mirroring exactly what mastro's own
   `mapInvoiceToInput` (`persist.ts:91-112`) does for its target table — and calls
   `InvoiceService.import_issued` (or a thin private sibling sharing its exact
   validation/lock/write sequence) as the actual writer. This is the direct guarantee
   against divergence: both the hand-declared path and the newly-parsed path run the
   same six register rules from
   `2026-09-04-slice-9-import-storico-e-google-drive-design.md` §3.2 (uniqueness,
   chronological monotonicity, counter-never-backwards, declared gaps, only closed
   years or the current one to date, and no import above a number already issued
   natively — `service.py`'s own `first_native_number` check), the same
   declared-totals-verified-not-recomputed
   discipline, and the same `ActivityService.record` call, because they are, at the
   database-writing level, the same call.
3. **One addition to `import_issued`'s own contract, gated by the caller — reusing the
   source document, never duplicating it.** Unlike the hand-declared path, a
   FatturaPA-parsed import *has* the original transmitted XML bytes in hand, already
   stored under the `document_id` §4 requires as input. `import_issued`/its writer
   should therefore set `xml_document_id` **to that same `document_id`** and compute
   `xml_hash_sha256` from its already-stored bytes — never archive a second
   `documents` row for content already on file. A second row was the first instinct
   here and is wrong: `DocumentUpdate` has no `tipo` field (`documents/schemas.py:68-77`),
   so a document's `tipo` cannot be relabelled to `fattura_xml` after the fact, and
   creating a fresh row with the same bytes under a different id would leave two
   `documents` rows for one fact — exactly what this project's own "no duplication"
   principle (`projects/pigrocrm/AGENTS.md`) rules out. The referenced document's own
   `tipo` stays whatever it was archived as (`documento`, or `fattura` if the caller
   chose that on upload); `invoices.xml_document_id` is the fact that this document
   *is* the invoice's XML, and needs no relabelling of the document row itself to
   carry it. This sets `xml_document_id`/`xml_hash_sha256` on the invoice row, which
   the hand-declared path structurally cannot do (spec 2026-09-04 §3.1: "Cosa non ha,
   per costruzione: `xml_hash_sha256` resta NULL"). Nothing in the schema forces this
   choice — no `CHECK` constraint ties `importata_da` to the nullness of those two
   columns (confirmed in §2) — so this is additive, not a migration of the existing
   path's own guarantees. **A shared `document_id` across a `lotto` batch's several
   invoices does not fit PigroCRM's existing document-ownership rules, so this design
   does not attempt it.** Two facts, read directly, rule it out: `soft_delete`
   unconditionally soft-deletes whatever `xml_document_id` names (`service.py:601-609`,
   no check for another invoice still pointing at it), so a shared document would go
   dark for every sibling invoice the moment one of them is deleted; and the codebase
   already enforces the opposite of sharing for the PDF slot —
   `_validate_original_pdf`'s own "taken" check (`service.py:1550-1560`) refuses a
   `pdf_document_id` already linked to another invoice with `Conflict("questo PDF e'
   gia' collegato a un'altra fattura")`. Building a real shared-artifact lifecycle
   (reference counting, or a soft-delete that checks for other referrers first) is
   its own piece of work with no precedent in either codebase. So instead: **only a
   single-invoice document gets the reuse-as-`xml_document_id` treatment above.** A
   `lotto` batch's invoices are still parsed, classified and imported exactly like any
   other — their register rows exist, dates and totals are checked the same way — but
   none of them takes ownership of the shared source document: `xml_document_id`
   stays `NULL` on every invoice that came out of a batch, precisely like the existing
   `esterno` path today (§2). One direct consequence, worth stating rather than
   discovering later: a batch-sourced `"fatturapa"` row therefore has no stored hash
   either, so a future re-import of the same batch always reports `conflict`, never
   `already_present` (§7 item 2's `NULL`-hash rule) — the conservative default, never
   assuming identity it cannot prove, exactly as for an `esterno` row. Real per-invoice
   XML custody for a batch is named as an open question for a later spike, not solved
   here.
4. **`importata_da` gains a second value, not a second meaning.** `Literal["esterno",
   "fatturapa"]` on `models.py:133`'s `String(20)` column (a constant, not a migration,
   the same convention this file's own module docstring states for every closed set
   here). Both values keep meaning "this row's XML was not produced by this CRM's own
   `issue()`" — `export_xml`'s existing refusal at `service.py:2039-2046` is keyed on
   `importata_da is not None`, not on a specific value, so it keeps refusing
   regeneration for both, unchanged, with zero code delta beyond widening the
   `Literal`. What should change, named here as an explicit follow-up rather than built
   now (§7 item 6): today's refusal message ("questo CRM non ne produce un secondo") is
   equally true for both paths, but for `"fatturapa"` the CRM is no longer merely
   refusing — it is sitting on the actual original file and could *serve* it instead of
   only refusing to regenerate one. The list-invoices badge
   (spec 2026-09-04 §3.6, "importata dal gestionale precedente") likewise needs a
   second, honestly distinct label rather than one that erases the difference between
   "typed by a human" and "parsed from the document itself."

## 6. What is explicitly not here: day-rate reconciliation

The issue's own **Not here** is confirmed by reading both sides. mastro's
`day-mapping.ts:66-114` (`proposeDayMapping`) links an invoice line to already-recorded
`work_unit` rows by quantity/amount/date, and `persist.ts`'s own
`PersistInvoiceLineDecision.workUnitIds` (`:30-36`) is exactly the "propose, human
confirms" slot that reconciliation fills in. PigroCRM has no `work_unit`/day table at
all yet — `REB-344` is, as of this writing, still mapping which entity plays that role
(its own text: "the day lifecycle... has no home today"). So `review_invoice_import`
never attempts a day proposal: a parsed line becomes exactly an `InvoiceLineImport`
(`schemas.py:274-297`, which has no linkage field to anything day-shaped today), the
same shape the hand-declared path already produces. Once REB-344's spike names its
day/work-unit entity, a follow-up (§7 item 7, explicitly gated, not filed) extends the
review step's output to also propose linking lines the way mastro already does,
translating `day-mapping.ts`'s tested algorithm onto whatever entity REB-344 settles on.

## 7. Proposed follow-up work

Per this project's own description — "no implementation issue is filed until Lorenzo
signs off on each spike's entity mapping" — this is a proposed order stated inside this
document, not a set of created Linear issues. Each item names its own acceptance
criterion.

1. **Port the adapter interface and the FatturaPA-1.2 parser.** A Python
   `InvoiceFormatAdapter` protocol (`detect(bytes) -> bool`, total and side-effect
   free; `parse(bytes) -> list[ParsedInvoice]`, one entry per invoice a "lotto" batch
   carries) mirroring `adapter.ts:28-53`, plus one concrete FPR12 adapter and the
   `ParsedInvoice`/`ParsedInvoiceParty`/`ParsedInvoiceLine` schemas from §5.
   *Acceptance:* a real FPR12 XML file — including a multi-invoice batch — parses into
   one `ParsedInvoice` per invoice, with no database access, and a document that is not
   valid FatturaPA XML makes `detect` return `false` rather than raise.
2. **Direction detection and register-aware dedup, including the `NULL`-hash case the
   `esterno` path already produces.** The tax-id classifier from §3 against this
   space's own `EmitterProfile`, plus the natural-key/hash duplicate check against the
   existing `(anno, numero)` register and a stored XML hash. Every `esterno`-imported
   row today has `xml_hash_sha256 = NULL` by construction (§2), so a `(anno, numero)`
   match with no hash on record can never be verified byte-for-byte — mastro's own
   `persist.ts:178,181` answers exactly this case by treating an empty set of known
   hashes as never a match, always `conflict`, and this design adopts the same rule
   rather than guessing: a numbered invoice already on record with no stored hash is
   always `conflict`, never `already_present`, whatever the incoming bytes are.
   *Acceptance:* an outgoing invoice already on record **with a stored hash matching
   the incoming bytes** reports `already_present`; the same number with a different
   hash, or with no hash on record at all, reports `conflict`; a supplier's invoice
   reports `incoming_skipped` and is never inserted.
3. **The review endpoint, read-only.** `review_invoice_import` (REST + MCP), returning
   one row per parsed invoice with its outcome and proposed customer match, writing
   nothing. *Acceptance:* reviewing the same file twice never changes database state
   and always returns the same verdict.
4. **The confirm endpoint, converged onto `import_issued`.** `confirm_invoice_import`
   (§4-5): re-parses from the original bytes, maps onto `InvoiceImport`, calls
   `import_issued`'s own register rules and `ActivityService.record` invocation, and,
   for a single-invoice source document, additionally sets `xml_document_id`/
   `xml_hash_sha256` on the invoice row to point at the caller's own source
   `document_id`, never a second copy of it (§5 item 3; a batch-sourced invoice leaves
   both `NULL`, per that same item).
   *Acceptance:* confirming an invoice review already flagged `already_present` is a
   no-op reporting it as such, never a duplicate insert; confirming a fresh one
   produces exactly the row `import_issued` would have produced by hand, plus a
   hash-verified `xml_document_id` pointing at the one document row that was ever
   archived for it.
5. **Customer-match proposal and creation, without a premature commit.** When no
   `Customer` matches the parsed party's tax id, `confirm_invoice_import` accepts a
   decision to create one — but not by calling `CustomerService.create` as-is: that
   method commits on its own (`customers/service.py:167`), which would leave a
   customer committed with no invoice if the register write failed a moment later,
   exactly the orphan mastro's own `confirm.ts:6-12` names as the reason its
   `confirmClientContractProposal` takes an optional `tx: DbExecutor` instead of
   opening its own transaction. `CustomerService` needs the same shape: a
   non-committing insert (a private method the existing `create` calls too, passing
   its own session) that `confirm_invoice_import` calls inside the *same* transaction
   as the invoice write, committing once, at the end, exactly as `import_issued`
   already does for everything else in that call.
   *Acceptance:* importing a batch from a brand-new counterparty produces exactly one
   new `Customer` row, with every invoice in that batch attached to it, and a register
   failure after the customer insert leaves neither the customer nor any invoice
   committed.
6. **`importata_da: "fatturapa"` and its own badge; `export_xml` serves what is on
   file, for a single-invoice document only.** Widen the `Literal` (§5 item 4), give
   the invoice list a second, honest badge, and change `export_xml`'s refusal so a
   `"fatturapa"` row with a stored `xml_document_id` serves that original XML instead
   of only refusing. A batch-sourced row has no `xml_document_id` (§5 item 3), so it
   keeps hitting today's unconditional refusal exactly like an `esterno` row — no
   special case needed for it here, and no claim of file custody this design cannot
   back. *Acceptance:* `list_invoices` on a space with both import kinds shows two
   distinct badges; `export_xml` on a `"fatturapa"` row from a single-invoice document
   returns that file's bytes (hash-verified); on a batch-sourced row it refuses
   exactly as it does today for any other non-null `importata_da`.
7. **Day-linkage on imported lines, once REB-344 lands.** Extend the review step to
   propose linking an imported line to already-recorded day/work-unit rows, the way
   mastro's `day-mapping.ts:66-114` already does, translated onto whichever entity
   REB-344's spike settles on. *Blocked on REB-344's sign-off; not started before it.*
