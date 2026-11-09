# Due items, bank statements and the cash schedule

Status: approved by Lorenzo on 2026-11-09. Card: REB-962. Project:
«Show when money comes in and goes out, week by week» (P-REB-83). Frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` (the «frame»), § A3, A4,
A5, A8, B5 and C4. Siblings: the inbox (`2026-11-09-incoming-documents-inbox-design.md`, C2)
and the purchases (`2026-11-09-suppliers-purchases-and-iva-design.md`, C3).

## Why

Lorenzo, 2026-11-09: «una ui comoda per capire quando pagare chi e quando incassare da chi»,
and no missed payment or cash gap. Today only an issued invoice has a date it falls due and a
paid flag (`invoices/models.py`, `due_date`, `payment_status` `unpaid`/`paid`, with the check
that a `payment_date` needs `paid`); the receivables schedule groups them in six buckets
(`invoices/receivables.py`, REB-329). Nothing else PigroCRM knows has a due date: no
purchase, tax or salary. There is no bank account, statement or movement. The economic tab's
`CashOverview` carries revenue, costs and drafts by month; its `projected` figure sums
invoices paid, unpaid and drafted and leaves costs out (`analytics/cash.py`), and the
contract projection is kept apart from it on purpose (`contracts/projection.py`, its
docstring).

## Decisions taken (Lorenzo, 2026-11-09)

1. Statements arrive from email or by upload first, with an alert when something that should
   be there is missing; banking APIs come later, on the standards that cover the most accounts.
2. The banks to cover first: Banco BPM, Revolut Business and Qonto.
3. What a freelancer is paid, and when, depends on each freelancer's contract: a fixed term,
   or when the client pays (C5 sets the rule; this design gives it the link it needs).

## Scope

**Core and module.** Due items, their settlements and the rules that date them are **core**:
the table and the service are always on, because an invoice needs them whatever the module
state. The module `cash` (frame § A2) owns bank accounts, statements, movements, matching,
«Cassa» and the projected balance. With `cash` off, payments are still recorded (by hand, as
`set_invoice_payment_state` does today) and the overdue reminders still run; only the bank
side is hidden and its jobs and reminders skipped.

**Row security.** Every new table follows the `legal_entity_scope` policy pattern of
revisions 0068, 0070 and 0073 (`scope_all()` first), and its statements are added to the
policy loader in `conftest.py` (`_legal_entity_scope_statements`, which reads only those named
revisions; the test database otherwise tests nothing):

- a bank account always has a legal entity (not null);
- a settlement's due item and its movement must share the legal entity, enforced in the
  service and by a constraint where the schema allows;
- a counterparty of a new type (an employee, the tax office) is handled explicitly in
  `entity_visible`, never by its `ELSE` branch;
- plain `STABLE` functions only: a new definer function needs a DECISIONS row first
  (2026-11-02);
- a scoped-member test per table: a member limited to one entity sees none of another's
  accounts, movements, statements, due items or settlements.

**Not here.** Paying from PigroCRM; banking APIs until verified; forecasting from deals by
default; the rules that produce taxes (C6), payroll (C7) and the freelancer's payment rule
(C5), which only write due items. See § 8.

## 1. Due items

A **due item** is one amount that falls due, in or out, the shape the frame's § A3 fixed:

- `direction`: `in` or `out`;
- the **counterparty**: an organisation (customer, supplier, freelancer, C3), the tax office
  with the tax it is for (C3, C6), an employee or director (C7);
- the legal entity, the amount and currency, the due date (or none, «senza scadenza»);
- the **state**: `forecast` (computed from a rule, no document yet), `expected` (a document
  fixes it), `settled` (fully paid), `dropped` (no longer owed); a partly paid item stays
  `expected` with what remains;
- the **source**: its kind (sales invoice, purchase, integration VAT, IVA settlement,
  withholding, tax instalment, payroll, contract occurrence, recurring expense, cash-basis
  VAT, engagement) and id, and for a forecast the rule that produced it. The kind is
  validated in code, with a test that lists every kind and no CHECK constraint, so a later
  project (C5, C6, C7) adds a kind without a migration;
- an optional **due rule** (§ 1.3) that dates it from another item.

A due item is in its **document's currency**. **Settlements** are rows of their own: the
amount in the account's currency and in the due item's, the settlement date (the movement's
booking date, with its value date stored too), and the bank movement or the person that
recorded it. A payment in instalments, a transfer that pays two invoices, or an F24 that pays
several taxes are all one shape.

A forecast is **replaced** by its document, never added to it (§ 1.2).

### 1.1 The existing receivables fold in

Every issued invoice gets its due item, `expected` or `settled` from its `payment_status` and
`payment_date`. From then on the due item is the truth and the invoice's `payment_status`
and `payment_date` are a projection that one writer keeps. `payment_status` is `paid` when the
remaining amount is zero; `payment_date` is the date of the settlement that completed it.

**The single writer** is a `DueItemService` in core. It is called by emission, import,
`set_invoice_payment_state` (which keeps its name and records a settlement), annulment and
credit notes (§ 1.4). A test fails when an invoice's two columns and its due item disagree.

**Writers of `payment_status` today**, all moved behind that service: `invoices/emission.py`
(the invoice is born `unpaid`, line 303), `invoices/issued_import.py` (imports with `paid` and
a date, lines 315-316), annulment (the due item drops), `invoices/drafting.py`
(`set_payment_state`, line 337; the draft row, line 119).

**Readers**, and which amount and date each uses once a settlement can be partial:

| Reader | Where | Amount | Date |
|---|---|---|---|
| «Da incassare» cards and the six receivables buckets | `invoices/repository.py` (`sum_receivable`, `sum_expired`, the buckets in `invoices/receivables.py`) | the due items' remaining amount, one query for card and buckets | the due date |
| The invoice list filter on `payment_status` | `invoices/repository.py:737-746` | the projected column | n/a |
| Payment reminders, the `invoice_overdue` detector and the automation | `gmail/payment_reminders.py`; `reminders/detectors/billing.py:120`, `billing_contracts.py:471`; `automations/daily.py:390` | the remaining amount | the due date |
| The forfettario ceiling | `fiscal/ceiling.py` | each settlement's amount | the settlement date |
| The tax estimate, collection view; `monthly_paid` | `analytics/repository.py` | each settlement's amount (`monthly_paid`: the net receivable, C3) | the settlement date |
| The digest | `digest/service.py`, `mail/digest.py:93` | each settlement's amount | the settlement date |
| The calendar, the hub door | `calendar/repository.py:113`, `engagements/service.py:369` | the projected status | the due date |
| The year CSV and overview | `analytics/year_csv.py`, `year_overview.py` | the invoice, from the projected columns | the projected `payment_date` |

The ceiling, the tax estimate and `monthly_paid` change to read per-settlement amounts by
settlement date, so instalments across 31 December are attributed to the right year (this
changes `fiscal/ceiling.py` and the analytics repository). A test keeps the card totals and
the bucket totals equal, including an invoice with withholding or split payment and a partly
paid one. An invoice's receivable is its net (`net_receivable`, C3: total minus withholding
minus split-payment VAT); the invoice's gross `total` stays what the document prints.

**A sales due item's date never moves.** The invoice's `due_date` is frozen after issue
(`MUTABLE_AFTER_ISSUE` in `invoices/checks.py`), so the item's date stays equal to it. Only
outgoing items and forecasts can be rescheduled.

**Rollout.** `migrate` runs before the new API is up and the old API keeps serving
(DECISIONS 2026-11-02), so invoices written by the previous release have no due item. A
reconcile step in `pigrocrm ensure-space-defaults`, after migrations, gives every issued
invoice without a due item its due item and marks the settled ones, idempotently. It is tested
with an invoice written by the old code path.

**Order.** Due items and this fold-in are this project's first milestone, and they ship before
the cards of C3 that write purchase, withholding and IVA due items.

### 1.2 Where forecasts come from

Forecasts are **stored rows**, materialised by a daily job (with the other crons) and on
changes to contracts, rate cards and work units. The key is a uuid5 of (source kind, rate card
or contract, period), unique among live forecasts by a partial unique index, so a race with
issuing cannot double one. Invoice lines created from a contract (`bind_work_units`, fee
billing) carry the rate card and the period they bill, so issuing drops the matching forecasts
in the same transaction. A forecast whose date passed with no invoice is dropped by the job,
and the existing unbilled reminders say so.

A forecast's due date uses **the same function as emission**: `InvoiceContext.due_date_from_terms`
(`invoices/context.py`): the customer's days, else the profile's `due_days` (`emission.py:181`).
It does not use `Contract.payment_days`, which nothing reads, so the date does not jump when
the invoice replaces the forecast.

| Source | Becomes | Replaced by |
|---|---|---|
| an active contract's recurring fee (`contracts/projection.py`, `fixed_recurring` rate cards) | `forecast` in, on the occurrence date plus the customer's terms | the issued invoice carrying that rate card and period |
| `worked` work units with no invoice line | `forecast` in, at the contract's next billing date plus terms | the draft invoice line they are bound to, then the issued invoice |
| a draft invoice, a draft or confirmed proforma | `forecast` in (as `CashOverview` already counts drafts) | the issued invoice |
| a recurring expense: a **recurrence rule owned here** (cadence, amount, supplier, category) | `forecast` out | the purchase of that period |
| the IVA settlement of the open period | `forecast` out, computed from C3's running register figures | the closed period's due item (C3) |
| tax instalments (C6), payroll (C7), freelancer payments (C5, kind `engagement`) | `forecast` out | their documents |

Only active contracts are forecast: not draft, terminated or expired ones. The renewal half of
`contracts/projection.py` (`from_renewal`) is excluded, since that projection is kept apart on
purpose. Deals are not forecast. A scenario switch on the schedule may add open deals
weighted by probability, drawn apart and never summed into the balance by default.

### 1.3 The due rule

`depends_on` becomes a **due rule** with four kinds:

- `fixed`: the item's own date;
- `after_settlement`: another item, plus days; the date is the later of that and the item's own
  date, and never later than an optional `not_after` date the rule carries (a freelancer paid
  when the client pays, capped by the legal backstop of 60 days from receipt, C5 § 4);
- `month_after_settlement_on_day`: the 16th of the month after the settlement (withholding, C3);
- `on_settlement`: due when the other item settles (the VAT of a cash-basis invoice, C3).

An undated dependent is **projected** on its dependency's due date plus its offset and drawn as
estimated, so a freelancer paid when the client pays is in the 13-week balance before the
client pays. The label follows the dependency's direction: «in attesa dell'incasso» when it
waits for money in, «in attesa del pagamento» when it waits for money out.

### 1.4 Credit notes

A sales TD04 (credit note) reduces the remaining amount of the invoice it references, or
becomes an outgoing refund item when that invoice is already settled. A supplier TD04
likewise reduces the payable. An unreferenced credit note stands alone until a person links
it. A credit note never gets an `in` due item of its own by the rule that gives every issued
invoice one.

## 2. Bank accounts and statements

A **bank account**: its legal entity, name, bank, IBAN, currency, opening balance and its
date, how statements arrive (upload, a space mailbox with the purpose `statements`, later an
API) and how often (monthly, quarterly), and the sender addresses statements come from.
`bank_accounts.iban` is ours. `FiscalProfile.iban` (`fiscal/models.py`) stays the account
printed on invoices and should name one of our bank accounts.

**Counterparty IBANs.** An organisation gains its bank accounts (IBANs, a list like contact
points). They are learned from supplier XML (`DatiPagamento`) and proposed from matched
movements for a person to accept.

**Statement.** An entity of its own: the account, the period (from the file, or set by a person
on import), the opening and closing balances, the format, the inbox item it came from, and a
state: `proposed` (a PDF reading awaiting a person), `imported`, `reverted`. **Undo** is
reverting a statement: it removes its movements and their automatic matches. A PDF proposal
shows computed totals against the stated ones before a person accepts it.

**The `statements` purpose** (moved here from the inbox design). It is allowed on space IMAP
mailboxes (C2) and, when its owner switches it on, on a personal Gmail.

- Gmail: a `from:` sender clause already satisfies the existing address guard
  (`gmail/query.py`, `_HAS_ADDRESS_CLAUSE`).
- IMAP: `SEARCH FROM <sender>` for each of the account's senders, reading headers and
  BODYSTRUCTURE only, with an AST-style test that no list query lacks a sender clause.
- Trust: when the message carries an authentication result, it must pass; and a statement
  delivered by mail never settles anything automatically until a person has confirmed the
  first statement of that account from that sender.

The attachments become inbox items (C2) of kind `bank_statement`.

**Reading a statement** turns a file into movements: booking date, value date, amount,
currency, counterparty name and IBAN, description, the bank's reference, the balance when the
bank gives it. One reader per format, each «to verify» against a real export before it is
built (the XLS reader is a C4 dependency named by C2):

| Bank | First format | Later |
|---|---|---|
| Qonto | CSV export (to verify) | its own API (to verify) |
| Revolut Business | CSV export (to verify) | its own API (to verify) |
| Banco BPM | CSV or XLS export from its business banking, which may be «YouBusiness Web», not «YouWeb» (to verify) | PSD2 access through CBI Globe (to verify) |
| any | CAMT.053, availability per bank (to verify) | |

**Dedup key** for a movement: the bank's reference when present, otherwise (account, booking
date, amount, description hash, occurrence index within the file), so two genuine identical
same-day payments are both kept while the same movement in two statements is kept once.

A structured file from an account the space has is certain data (frame § A4). A PDF statement
is read by the model or the user's agent (C2's two readers) into a proposal of its movements,
which a person accepts.

**Balance model.** A statement's stated closing balance when present, otherwise the opening
balance plus the movements. When both exist, a reconcile check alerts on the difference.
Today's balance is the last stated closing balance plus later movements, or the computed one.

**Checks**, each an alert through the reminders catalogue:

- **missing**: an account with no statement covering a period its cadence says has ended,
  plus a grace (default 7 days); the covered period is the one in the file or the one set on
  import;
- **gap**: a statement whose opening balance is not the previous one's closing balance, only
  where stated balances exist;
- **reconcile**: stated against computed balance, as above;
- **overlap**: handled by the dedup key.

**Movement states:** `matched`; `transfer` (paired between two of the space's accounts, and
proposed when the amount is the same, the sign opposite and the dates within 3 days);
`ignored` (with a reason, for example personal use of a solo's account); `unmatched`. Only
`unmatched` raises the alert.

**Currency.** Automatic matching only within one currency; a cross-currency match (a EUR
account paying a USD supplier) is proposed. Euro totals use the document's stored rate (C3)
and, for balances, the latest ECB rate (to verify).

## 3. Matching

A movement is matched to one or more due items, which settles them:

- **automatically**, only on an exact reference: exactly one open due item with the same
  amount, currency and direction **and** its document number in the movement's description,
  from an account whose statements a person has already confirmed (§ 2); recorded with its
  reason and undoable. A known counterparty IBAN alone never settles anything: it ranks the
  proposals. A settlement drives the flat-rate ceiling and the tax estimate (`fiscal/ceiling.py`,
  paid revenue), so anything short of an exact reference is a proposal (frame § A4);
- **proposed** otherwise: the best candidates, for a person (or an agent, see § 5) to pick, a
  split across several items, a `transfer` or `ignored` mark, or a new purchase of kind
  `expense` for a movement with no document (a bank fee). That purchase is born settled by the
  movement; if the movement is dated in a closed month it is refused as a cost is, with the
  same message;
- a movement left unmatched for more than N days (default 14) is an alert.

Settlements are cash facts and the month lock does not apply to them.

Marking a due item paid by hand stays possible (cash, a payment the bank file has not shown
yet); the movement, when it arrives, matches the settlement instead of making a second one.

## 4. The cash schedule

**One view, two doors.** The dashboard tab «Scadenziario» becomes the two-way schedule (frame
B5), and «Cassa» in the sidebar opens the same view. The six receivables buckets stay for
receivables and get their mirror for payables.

«Cassa» answers **when money moves**. `CashOverview` stays the economic tab's view of revenue
and costs by month; the two are cross-linked and never summed, as the contract projection
already is not. A given month can read differently on them by design: one is by due date and
movement, the other by period.

- **today's balance**: per account, by the balance model of § 2, with the date it is true as
  of;
- **week by week**, 13 weeks ahead by default: what comes in and what goes out, by state
  (forecast and estimated drawn lighter than expected), and the **projected balance** at the
  end of each week;
- **overdue** both ways: what we have not received and what we have not paid;
- filters by legal entity (frame § A1: money is summed across entities, with a filter); an
  account in another currency is shown in it, and converted for the total as in § 2.

**Profiles.** The company profile sees both directions and the projected balance. The solo
profile sees today's balance, money in and its own taxes; no projected balance and no outgoing
column (frame § A3 unchanged).

Alerts: the projected balance goes below zero (or below a floor the space sets) within the
horizon; an outgoing due item becomes overdue; the existing overdue-invoice reminders read due
items.

## 5. Module and permissions

The module `cash` (frame § A2), over MCP and REST (frame § A8). Classification is by **source
kind** of the due item, then by action. Each new action key needs a row in
`AGENT_ALLOWED_ACTIONS` or `FULL_ACCESS_ACTIONS` (`core/agent_actions.py`), the `MIN_ROLE` map
in `apps/web/src/lib/permissions.ts`, and the surface-coverage test.

| Action (tool) | Who |
|---|---|
| `list_due_items`, `get_cash_schedule`, for sales invoices, purchases and recurring expenses | any member who may read money today; agents read |
| the same two tools, for items of tax, IVA, withholding and payroll source kinds | admin; `full_access` for agents (the source's permission, C3 «fiscal position») |
| `list_bank_accounts`, `list_statements`, `list_bank_movements` | admin; `full_access` for agents (the schedule's balance discloses the same fact) |
| `create_bank_account`, `update_bank_account`, `archive_bank_account` | admin; agent allowed |
| `import_statement` (a structured file, or from the inbox), `revert_statement` | collaborator; agent allowed |
| accept a proposed statement read from a PDF | a person (frame § A4); not a tool |
| `match_movement`, `unmatch_movement`, `mark_movement` (transfer or ignored) | collaborator; agent allowed |
| `settle_due_item` on an **incoming** item (a client paying), as `set_invoice_payment_state` is today | collaborator; agent allowed |
| `settle_due_item` on an **outgoing** item (a supplier payment marked paid) | admin; `full_access` for agents (frame § A8 candidate; C3: marked paid by a person) |
| set the alert thresholds and the balance floor | admin |
| connect a bank API (later) | a person: a credential |

`set_invoice_payment_state` keeps its collaborator, agent-allowed row. Movement descriptions
and counterparty names are free text written by others: `list_bank_movements` and the
statement reads are in `FREE_TEXT_TOOLS` (registered tools only; the test fails otherwise).
The MCP tool budget is 94,000 bytes (`test_mcp_tool_annotations.py:48`) and C2 and C3 also add
tools, so the card that adds these tools keeps the descriptions short and folds list tools
into filters where it can.

## 6. Surfaces

The dashboard tab «Scadenziario» and «Cassa» in the sidebar's «Amministrazione» section show
the same schedule (§ 4). On the phone, the Home's figures read due items (the 2026-11-09 row
on the phone shell: figures first). The account page, a statement's movements and the matching
panel. New surfaces: brief and mockups before markup (frame § A7).

## 7. What is tested

The reconcile of invoices written by the old code path; every invoice with the projection
columns agreeing with its due item; card totals equal to bucket totals, with withholding,
split payment and a partial settlement; a ceiling and a tax estimate attributing instalments
across 31 December by settlement date; a forecast replaced, not doubled, when its invoice is
issued, and the unique key under a race; a forecast dated as the invoice will be; a transfer
split across two invoices; a freelancer due item waiting for its client's and projected in
the balance meanwhile; a credit note reducing an open invoice and refunding a settled one;
each statement reader on a real anonymised export; the missing, gap and reconcile alerts; a
duplicate movement across two statements kept once and two identical same-day payments both
kept; a statement reverted; automatic matching only on a unique fit and undone; a statement
mail with a failing authentication result, and a first statement from a new sender, settling
nothing; the projected balance crossing zero raising its alert; a solo space seeing no
projected balance and no outgoing column; a scoped member seeing nothing of another entity;
a test listing every due item source kind; the `statements` query guards (Gmail and IMAP);
the tool budget.

## 8. Not in this design

Paying from PigroCRM (a SEPA file or a payment API); banking APIs until verified; forecasting
from deals by default; the rules that produce taxes (C6), payroll (C7) and the freelancer's
payment rule (C5), which only write due items here.

## Cards

Filed once the design is approved, in this order: due items with settlements, the
`DueItemService`, the reader changes and the fold-in of receivables with its reconcile (first,
before C3's due-item cards); forecasts from contracts, days, drafts and proformas, and
recurring expenses; bank accounts and counterparty IBANs; the `statements` purpose; the CSV
and CAMT.053 readers (Qonto, Revolut Business, Banco BPM, CAMT.053); statement checks and
alerts; matching; the brief for «Cassa»; the schedule page and the phone figures; the scenario
switch for deals.
