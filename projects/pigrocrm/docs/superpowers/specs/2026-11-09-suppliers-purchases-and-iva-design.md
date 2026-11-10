# Suppliers, supplier invoices and the IVA cycle

Status: approved by Lorenzo on 2026-11-09. Card: REB-961. Project:
«Track suppliers, their invoices and the IVA cycle» (P-REB-82). Frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` (the «frame»), § A1 to A8
and C3. Fed by the inbox of `2026-11-09-incoming-documents-inbox-design.md` (the «inbox»).
Written alongside the cash design (C4), whose `due_items` table this design writes to (§ 5).

Every Italian fiscal rule below is sourced in the frame's A5 register or marked «to verify»
there; this design adds its rows to that register.

## Why

PigroCRM knows who pays it and nothing about whom it pays. A cost is one row with a
free-text `supplier`, a VAT-inclusive `amount`, no due date and no payment state
(`timetracking/models.py:199-253`). The `invoices` table holds sales only, paid or unpaid
(`invoices/schemas.py:38`). The fiscal code names withholding, the professional fund, split
payment, reverse charge and deferred IVA as out of scope (`fiscal/regime.py:10-12`), and the
document types TD16 to TD19 exist nowhere. The importer already reads a supplier's invoice
and drops it (`invoices/import_direction.py`).

## Decisions taken (Lorenzo, 2026-11-09)

1. **One registry with roles.** The same company can be a customer and a supplier, so there
   is one record per organisation with its roles, «niente doppioni», even though «Clienti»
   becomes «Anagrafiche» and much code moves (see «Rename blast radius»).
2. The **full IVA cycle** and **foreign invoices both ways** are in scope (frame decision 7).
3. The accountant keeps the books; PigroCRM gives the real-time picture (frame § A6).

## 1. One registry with roles

- The `customers` table becomes **`organizations`** (glossary: «anagrafica» → `organization`,
  added in the same PR), with a set of **roles**: `customer`, `supplier`, and `freelancer`
  (used by C5; not `collaborator`, which is a permission role). Every existing row gets
  `customer`. People keep belonging to an organisation; contact points are unchanged.
- A relation keeps the name of the role it plays: `invoices.customer_id` and
  `deals.customer_id` still say who is billed and point at an organisation; a purchase has
  `supplier_id`. The entity, its service, its REST routes (`/api/organizations`) and its MCP
  tools (`create_organization`, `search_organizations`, ...) are renamed in one release, with
  no alias (frame § A8, the clean cutover REB-897 applies), except the browser-route stubs
  named in the next section. The template variables people type (`{{cliente.*}}`) keep their
  keys.
- **Storage of roles** is the builder's choice (a join table or an array column), on one
  condition: a role filter is served by an index.
- **Scope.** An organisation is scoped by `legal_entity_id` as a customer is today; `NULL`
  means shared across the legal entities of the space, as costs are today. The row-security
  policy follows (next section).
- **Duplicates.** Before the migration a report lists the rows that share `(country,
  normalised VAT number)`, and a person resolves them. Then a partial unique index on
  `(country, normalised VAT number)` over live rows keeps them from coming back. Matching
  an incoming supplier by VAT number or tax code that hits two rows is refused, as
  `match_legal_entity` refuses it. `vat_number` is widened (it is `String(11)` today,
  `customers/models.py:99`) so a foreign number such as `NL123456789B01` fits. A **merge**
  operation (rewriting the seven FK columns and every polymorphic row) is designed as its own
  card; until it exists, two rows for one company are resolved by hand and the index stops a
  third.
- **Role-blind reads.** The model is imported by 37 modules. Rule: every existing read of
  customers filters the `customer` role unless this document names an exception. The
  exceptions are the search across roles (`search_everything`, «Anagrafiche» with no role
  filter), the registry page, and the purchase's supplier picker (filters `supplier`). The
  Gmail roster (`gmail/roster.py`, which builds the sync address book from customer email
  points and matches `website` as a domain) stays customers only, so an `activities` mailbox
  does not start syncing supplier threads; a test guards it.
- **Payment terms** are per direction: `payment_days` and `payment_end_of_month` are what
  the organisation is given as a customer; `supplier_payment_days` and
  `supplier_payment_end_of_month` are what it grants us as a supplier.
- **Supplier fiscal kind**, which decides what a payment to it involves: `company`,
  `professional` (ordinary regime; withheld at 20% when the buyer is a withholding agent),
  `flat_rate` (not withheld, on its written declaration, kept as a document; named for the
  regime, since a craftsman or trader is in it too), `occasional` (a receipt; withheld 20%
  when the buyer is a withholding agent), `foreign` (carries EU or extra-EU, and goods or
  services; whether reverse charge applies depends on the place of supply, § 7). Read from
  the XML when it says so (`DatiRitenuta`, the supplier's `RegimeFiscale`), set by a person
  otherwise.
- **Customer fiscal facts**, on the organisation as a customer: `customer_kind` (business,
  consumer, public body), whether it is a withholding agent, and for a public body its
  6-character IPA code (`sdi_code` accepts exactly 7 characters today,
  `customers/service.py:60,94`). § 3 uses them.
- In the UI: «Anagrafiche», filtered by role, with «Clienti» and «Fornitori» as the two
  default views. Where the entries sit in the sidebar is the brief's.

## Rename blast radius

Measured on the tree this design starts from. The rename is large and mostly mechanical, so
it is this project's **first milestone, on its own**: a pure rename with no behaviour change,
merged and released before any card of § 2 onward starts, so the features are written once,
on the new names, and a failure in the rename is never tangled with a failure in a feature.
The smaller alternative (keep `customers` and its names, add the roles, change only the UI
label) was weighed and set aside: a supplier stored as a `customer` is a name that lies to
every later reader, which the 2026-11-02 rename row (REB-897) exists to prevent.

| Area | Measured |
|---|---|
| Core source | 180 files mention "customer"; 37 import the `Customer` model; 519 `Customer` and 1,169 `customer_id` occurrences across core, api, mcp and web source |
| API source | 17 files; router `routers/customers.py` plus 16 others that mention it |
| MCP source | 34 files; tools `create_customer`, `update_customer`, `get_customer`, `search_customers`, `archive_customer`, `restore_customer`, `suggest_customers_from_gmail`, `customer_*` prompts, `customer://` resources |
| Web source | 223 files; 34 reference `/app/customers`; 126 occurrences of `/api/customers` across source |
| Tests | core 133, api 38, mcp 38 files; web e2e 14 |
| Database | 7 FK columns, 19 migrations, RLS policy and 5 SQL functions, trigram and sort indexes named `ix_customers_*`, notes CHECK constraint, polymorphic `entity_type` rows |
| Hub | No use of `/api/customers` or customer MCP tools. Only the CRM-side doors (`engagements/service.py`, `leads.py`) use `CustomerService`; the wire contract (`/api/rebase/leads`, engagements) is unchanged unless response keys rename |
| Templates | `{{cliente.*}}` keys unchanged by design (`templates/scope.py` `CUSTOMER_KEYS`); only the Python scope builders rename |
| Frozen invoices | `snapshot.customer` stays, since `customer_id` keeps its name; no snapshot version bump needed |
| Outside the repo | Agents and skills that hold tool names, and PostHog funnels that key on `customer_created` |

The cost-to-purchase replacement is smaller but real: 90 core source files, 11 api, 18 mcp,
61 web, and 78 test files mention costs.

Decisions:

- **Persisted discriminator.** The string `'customer'` is migrated to `'organization'`
  everywhere it is stored (the notes `ck_notes_entity_type` CHECK, `activities.entity_type`,
  `gmail_message_links`, `email_drafts`, custom-field definitions in `fields/schemas.py`),
  in the same migration as the table rename.
- **Row security.** The policy functions `customer_visible`, `person_visible`,
  `customer_archived`, `entity_visible` and `active_deals_of_customer` hold text bodies that
  `ALTER TABLE RENAME` does not rewrite (`0068_english_names.py:2037-2110`); they are
  rewritten for `organizations` and `'organization'`, and the `POLICIES` map follows.
  Purchases get the policy costs have: a shared purchase is visible only under «tutte»
  (`0048:55`).
- **Columns.** FKs keep their column names (`customer_id`); constraint and index names that
  carry `customers` are renamed in the migration.
- **MCP.** Resources `customer://{id}` become `organization://{id}` and the prompts in
  `prompts/customer.py` follow (`resources/entities.py:197,218`).
- **Browser routes.** `/app/customers/...` keep redirect stubs, the REB-897 carve-out for
  links already in sent mail and reminders (`reminders/links.py:25`); REST routes and tools
  have no alias.
- **PostHog** keeps the event names `customer_created` and `customers_imported`, fired when
  the customer role is given: renaming an event splits its history (glossary rule).
- **Glossary** (`docs/glossary.md`), same PR: «anagrafica» → `organization`; «fornitore» →
  `supplier` as a role; and a note that FatturaPA's `supplier` (cedente) is the issuer of any
  invoice, ours included, as `ParsedInvoice.supplier` and `import_direction.py` use it. The
  two meanings share a word and never a table.

## 2. Purchases

A **purchase** is anything the space pays for, with its document:

| Kind | From | VAT |
|---|---|---|
| `invoice` | a supplier's FatturaPA or PDF invoice (TD01 to TD03, TD05, TD06, TD24, TD25) | split by rate and nature |
| `credit_note` | a supplier's credit note (TD04), linked to the invoice it corrects | split |
| `receipt` | a till receipt, an occasional worker's receipt, a ticket | total only; VAT on a retail receipt is not deductible without an invoice (A5, to verify) |
| `expense` | anything else paid (a bank fee, a subscription with no invoice) | total only |

Each purchase carries: the supplier (an organisation with the `supplier` role; optional on a
`receipt`, which names only a shop in `merchant_name`), the legal entity that bought it (or
none, for a shared expense, as costs today), the number and the dates (document, received),
the **`iva_period`** (§ 6), the currency with the amount in it and in euro, the euro rate and
its date, one line per VAT recap group (rate, nature, deductible share), withholding (rate,
base, amount), professional fund, stamp duty, the split-payment and reverse-charge flags,
the cost category, an optional deal (hidden when `deals` is off), its due dates (§ 5), the
inbox item and the documents it came from, the **buyer's pack and regime frozen at
registration** (as an invoice freezes its snapshot, since `FiscalProfile` is not
historicised), and a status (`registered`, `cancelled`).

### Migration of costs

Today's costs become purchases of kind `expense`, in one migration, keeping: `description`;
the free-text supplier, as `merchant_name` on an expense or receipt; `custom_fields` (the
definitions for entity `cost` become `purchase`); `created_at` (analytics counts late entries
by it, `analytics/repository.py:431-434`); soft-deleted rows; and the history rows, whose
entity type is migrated. A negative expense stays an expense with a negative amount, a
refund, as today; the `credit_note` kind is for a supplier's TD04. `Cost` and its service,
routes and tools are replaced by the purchase ones in the same release. `cost_categories`
keep their name. The monthly period lock (`timetracking/locks.py`) keeps governing the hours
and the cost side of purchases; the IVA lock is separate (§ 6).

### What a purchase costs

Frozen on the purchase from the buyer's pack:

- ordinary buyer: the taxable amount plus the VAT that is not deductible;
- flat-rate buyer: the total, since VAT is never recovered; on a reverse-charge purchase the
  total plus the integrated VAT it owes (§ 6, A5 row);
- a shared purchase, or a legal entity on the `non-it` pack (no VAT register): the total.

The P&L and the deal P&L read that number: they read revenue net of VAT
(`analytics/repository.py:64`) while `Cost.amount` is VAT-inclusive, so for an ordinary buyer
recoverable VAT is counted as a cost today. The cost side is `monthly_costs`
(`analytics/repository.py:519-530`), by the purchase date, not the payment date.

### Money net of withholding and split payment

`Invoice` gains a stored **`net_receivable`**: total minus withholding minus split-payment
VAT, computed at issue and at import. Cash reads the net on both sides: `monthly_paid`
(`analytics/repository.py:443-453`) sums `Invoice.total` today and moves to `net_receivable`;
a purchase settles at total minus withholding. Instalments and partial payment live in C4's
due items.

### Integration documents (TD16 to TD19)

They are **not `invoices` rows**: the exporter emits our entity as seller from a row with a
required `customer_id` and an `InvoiceCounter` number, and `revenue_filter()`
(`analytics/filters.py:14-30`) selects on `type` and `status` only, so a TD17 stored there
would count as revenue. A table `integration_documents`, owned by the purchase, holds: own
numbering per legal entity and year (the rule, separate sequence or not, to verify), the
XML, its hash and `transmitted_at`. Revenue is untouched. When the SdI or the Cassetto copy of
our own TD16 to TD19 comes back through the inbox, it is recognised (our legal entity as
buyer and a TD16 to TD19 type) and linked to its integration document, never filed as a
purchase.

### Filing from the inbox (inbox § 3)

- **Accepted types**: TD01 to TD06, TD24, TD25, and TD16 to TD19 as above. The parser
  (`invoices/fatturapa_import.py`) gains `DatiRitenuta`, the supplier's `RegimeFiscale`,
  `EsigibilitaIVA`, an optional `ImportoTotaleDocumento` (it raises without one today), and a
  `DettaglioPagamento` with no due date (it raises today).
- **Certain** requires the `CessionarioCommittente` to match exactly one legal entity. A
  supplier's XML that is certain becomes a purchase with its trace, creating the supplier
  organisation when its VAT number or tax code matches none. **Undo** archives an
  auto-created supplier if nothing else uses it.
- A PDF or a photo becomes a proposal of a purchase the person accepts. As in the inbox
  design, `proposals.document_id` becomes nullable and `inbox_item_id` is added, with an
  explicit dispatch per target type (the table requires a document and, outside `contract`,
  a contract today, `proposals/models.py:55,105`).
- A purchase's file becomes a `Document` owned by the supplier organisation: the owner set of
  documents becomes organisation, deal, contract (exactly one, `documents/models.py:23`).
  This replaces the inbox's note that supplier invoices have no owner.
- The XML's own `DatiPagamento` gives the due dates when it has them.

## 3. The sales side, completed

The regime a solo professional in the ordinary regime lives in needs things the exporter does
not write today (`invoices/fatturapa/exporter.py`, `fiscal/regime.py:10`):

- **withholding** (`DatiRitenuta`), only when the customer is a withholding agent (the
  customer's flag, § 1; a flat-rate or foreign customer is not one, A5): the receivable is
  the total minus the withholding, and the withholding is a tax credit the professional sees
  in C6;
- **the «rivalsa INPS» (the social security surcharge) or the professional fund**
  (`DatiCassaPrevidenziale`): **waits for the accountant** (REB-994, A5): whether the
  contribution follows the VAT of the service and how it reaches the withholding base is not
  settled by the sources, so nothing computes or exports it until REB-975 answers;
- **split payment** on an invoice to a public body (`EsigibilitaIVA` `S`): the VAT is not
  collected. Professionals whose fees are subject to withholding are excluded from split
  payment (A5, verified: art. 17-ter c. 1-sexies), so the two are never both on one invoice;
- **public bodies**: a customer of kind public body gets the format `FPA12`
  (shipped by REB-994; `constants.py` wrote `FPR12` only) and its 6-character IPA code as
  recipient code;
- **foreign customers**: a business abroad is a non-taxable service (art. 7-ter DPR 633/72,
  A5, verified), as `_flat_rate` already assumed and `_ordinary` now writes (`N2.1`) ("every foreign customer is a company",
  `fiscal/regime.py`). A foreign consumer has its own branch (taxation and nature to verify,
  A5), told apart by `customer_kind`.

The receivables and the due dates read `net_receivable`.

**Imported credit notes.** An externally issued TD04 imported through the sales importer
enters the sales register as a credit note; `issued_import.py:306` writes TD01 for every
file today and is fixed to take the document type from the file, so the register stays exact
(frame A6).

## 4. Foreign currency

Today `CURRENCY="EUR"` is hardcoded on every invoice (`invoices/drafting.py:109`,
`invoices/issued_import.py:307`); `Contract.currency` is not carried to the invoice, and
analytics groups by currency and never converts (REB-776). Foreign currency is therefore
**new work**, not a described behaviour: an invoice and a purchase keep the currency with the
euro equivalent at the ECB rate stored beside it (the rate's reference date is to verify,
A5). The totals, the ceiling, the year overview and the concentration paths in
`analytics/` read the euro amount; each is named in the card and tested.

## 5. Due dates for what we pay

**Dependency on C4.** C4 defines the `due_items` table (direction in or out, counterparty,
legal entity, amount and currency, due date, state `forecast`, `expected`, `settled`,
`dropped`, source kind and id, an optional `depends_on` due item, partial settlements) and
folds the existing receivables into it as its **first milestone**. C3 writes due items through
C4's service. The C3 cards that write due items (purchase due dates, withholding, IVA
settlement, cash-basis IVA) are **blocked by that C4 milestone**. C3 creates no due-item
table and stores no payment state on the purchase.

Each purchase writes its due items, direction out, state `expected`:

- from the document's own due dates when it has them (`DataScadenzaPagamento`, several
  instalments allowed);
- otherwise from the supplier's terms, computed by `invoices/due_date.py::due_date_from_terms`,
  the one place a due date is computed (the XML parser and `emission.py` call it too),
  counted from the document date (or the receipt date for an occasional worker);
- a withheld amount (only when the buyer is a withholding agent: the buyer's pack, so a
  flat-rate legal entity withholds nothing, A5) writes a second due item, to the tax office,
  on the 16th of the month after the payment (A5, to verify), `forecast` until the payment
  is recorded; the rule that lets small withholdings wait to December (the 2024 decree) is to
  verify;
- a collaborator's payment rule (fixed term or paid when the client pays) is C5's and
  overrides these.

Paying is C4: a due item is settled by a matched bank movement or marked paid by a person.

## 6. The IVA cycle

For every legal entity on the ordinary pack (`it-standard`):

- **Registers.** The sales register is the issued invoices (core invoicing, whatever the
  state of the `purchases` module); the purchase register is the registered purchases with
  VAT, at their deductible share. Both are views over the documents, never a second copy,
  per period and legal entity.
- **Which period.** Each document is placed in a period by a date stored on it as
  **`iva_period`**. Which date decides is to verify per type: for sales, the issue or the
  operation date; for purchases, the receipt and registration date, a person choosing within
  the legal window (A5).
- **The IVA period lock.** A new table `iva_period_locks (legal_entity_id, kind
  monthly|quarterly, period)`. Closing blocks any write that changes a register: issuing,
  annulling, registering, cancelling, changing VAT lines, for a document whose `iva_period`
  is in it. Marking an issued invoice paid is not blocked (it changes no register). The
  existing monthly `PeriodLock` (`timetracking/locks.py`, global per `(year, month)`, which
  does not freeze invoices) stays as it is for hours and the cost side of purchases. New
  actions `close_iva_period` and `reopen_iva_period`: admin, unscoped, in
  `FULL_ACCESS_ACTIONS`, as `close_period` is.
- **Settlement.** Monthly or quarterly, a setting of the legal entity. Each period's figure is
  sales VAT due in the period minus purchase VAT deductible in it, plus the credit carried
  from the period before, plus the quarterly 1% where it applies (A5, to verify, with the
  minimum below which a periodic payment is carried forward and the quarterly eligibility
  thresholds); a positive result writes a due item to the tax office, a negative one is
  carried forward. The deadlines, the quarterly calendar and the December advance are in
  the A5 register as to verify, and are data in the fiscal pack, not code.
- **Exigibility.** Immediate by default; deferred on an invoice to a public body that is not
  under split payment; split payment as § 3; cash-basis IVA as an option of the legal entity
  (conditions in A5, to verify: eligibility ceiling, one-year backstop, buyer-side deduction
  timing, the invoice annotation), under which the VAT of an invoice falls due when it is
  collected or paid, moving the due item with the collection.
- **Deductibility.** A share per purchase line (100, 40 for a car's running costs, 0; A5, art.
  19-bis1 DPR 633/72, to verify), from the cost category's default and editable. Pro-rata is
  not designed here.
- **What PigroCRM does not do**: it does not transmit the «LIPE» (the quarterly communication
  of periodic settlements) or file the annual return. It shows the figures each needs, for
  the accountant (C8).

A flat-rate legal entity has no periodic settlement, but it owes the VAT on a reverse-charge
purchase from abroad (A5, to verify): those purchases write a due item for that VAT, and the
cost counts it (§ 2).

## 7. Foreign invoices

- **Sales abroad.** An invoice to a foreign customer already carries the recipient code
  `XXXXXXX` and the foreign postal code convention (`invoices/fatturapa/constants.py:24,30`)
  and, on the flat-rate pack, the nature N2.1 (`fiscal/regime.py`). The ordinary pack gains
  the same natures for a non-taxable service to a business abroad; B2C is § 3. Intrastat for
  services sold in the EU is to verify, and designed only once verified.
- **Purchases from abroad.** A foreign supplier sends a PDF, which becomes a proposed
  purchase (inbox § 3). A foreign supplier is not automatically reverse charge: when the place
  of supply is abroad no integration happens (A5, to verify), so a person confirms it on
  acceptance. Once registered as reverse charge, PigroCRM produces the integration document
  (§ 2): TD17 for services, TD18 for EU goods, TD19 for goods under art. 17 c. 2, and TD16 for
  a domestic reverse charge (A5; TD16 to verify), chosen from the supplier's EU or extra-EU
  flag and goods or services, with the VAT computed, in both registers. The XML is produced by
  an extension of the existing exporter and delivered the way sales invoices are today:
  exported and transmitted through the space's intermediary, then marked transmitted. The
  legal deadline is to verify (A5); a reminder fires before it.
- **Withholding on a foreign professional** is designed only when a space needs it: the 30%
  final withholding applies to work done in Italy (verified, A5) and depends on the treaty
  with the supplier's country.

## 8. Modules and permissions

- The module `purchases` replaces `costs` in the registry (frame § A2), with the same
  defaults; a space where `costs` was off gets `purchases` off. `module_costs` rows are
  rewritten to `module_purchases` in every space database and `OVERRIDABLE_KEYS` updated
  (frame B1).
- With `purchases` off, the `supplier` role's views and fields are hidden (the role itself
  stays on the row); the IVA cycle reads core sales invoices, so a space with it off still
  sees its sales register; a purchase's deal is hidden with `deals` off.
- The IVA cycle is part of `purchases`, shown only for legal entities on the ordinary pack.
- Over MCP and REST (frame § A8): organisations and their roles; purchases; filing an inbox
  item as a purchase; the registers and the settlement of a period. Accepting a proposed
  purchase is a person's act (frame § A4). Classification, tool by tool:

| Action | Class | Reason |
|---|---|---|
| register, update, cancel a purchase | collaborator; agent allowed (`AGENT_ALLOWED_ACTIONS`) | as `create_cost` today |
| produce and export an integration XML; mark it transmitted | full access (`FULL_ACCESS_ACTIONS`) | as `export_invoice_xml` and `mark_transmitted_externally` |
| read the registers and the settlements | full access | fiscal position, as `get_fiscal_estimate` |
| `close_iva_period`, `reopen_iva_period` | full access, unscoped admin | as `close_period` and `reopen_period` (`agent_actions.py:142-143`) |
| organisations and roles; reading purchases | collaborator | as customers today |

## 9. Surfaces

«Anagrafiche» (replacing «Clienti»), «Acquisti» (the purchases, with filters by supplier,
category, period and payment state from C4), and «IVA» (registers and settlements per period
and legal entity). New surfaces: brief and mockups before markup (frame § A7).

## 10. What is tested

The migration of customers into organisations and of costs into purchases with every
reference intact, including notes, activities, Gmail links, drafts and custom fields under
`'organization'`, and the rewritten RLS functions; the duplicate report and the unique index;
a Gmail roster that excludes suppliers; a supplier XML filed with its supplier created, a
buyer that matches no legal entity left as a proposal, and undo of an auto-created supplier;
a PDF proposal accepted into the same shape; a credit note reducing what is owed; an imported
TD04 entering the sales register as a credit note; the cost read as total for a flat-rate
buyer (plus integrated VAT on reverse charge), as taxable plus undeductible VAT for an
ordinary one, and as total for a shared purchase; withholding only when the buyer is a
withholding agent, and its second due item; an issued invoice with withholding whose
`net_receivable` is the net, and cash reading it; split payment collecting no VAT; a public
body invoice exported as FPA12; a quarterly settlement with a credit carried forward;
cash-basis IVA moving the due date to the collection; a TD17 produced for an EU service with
its VAT in both registers and never in revenue, and its SdI copy linked, not filed as a
purchase; closing an IVA period refused to a token without full access and blocking every
register-changing write dated in it; every old cost route and tool gone.

## 11. Not in this design

Paying and matching (C4, including partial settlement); the collaborators' payment rules
(C5); income tax, INPS and the tax due items other than IVA and withholding (C6); payroll
(C7); the accountant's export (C8); transmitting the «LIPE» or the annual return; IVA
pro-rata; Intrastat until verified; withholding on foreign professionals; the merge of
two organisations beyond its own card.

## Cards

Filed once the design is approved. Not blocked by C4: organisations with roles and the
rename; the duplicate report and unique index; the merge; supplier terms and fiscal kind;
customer fiscal facts; the purchase record and the migration of costs; filing purchases
from the inbox; `net_receivable`, withholding, fund and split payment on sales (FPA12); the
imported TD04 fix; the IVA registers and the IVA period lock; TD16 to TD19 integration
documents; foreign currency at the ECB rate; the brief for «Anagrafiche», «Acquisti» and
«IVA»; those pages.

**Blocked by the C4 milestone that creates `due_items` and folds the receivables in:**
purchase due items; the withholding's due item; the IVA settlement's due item; cash-basis
IVA.
