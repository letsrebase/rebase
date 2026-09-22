# Mapping mastro's day-approval ledger onto PigroCRM's own entities

Date: 2026-09-23. Status: proposed, awaiting Lorenzo's sign-off on the entity mapping
below — the project this spike belongs to ("Bring mastro's ledger, invoice import and
forecasting into PigroCRM") states its own gate plainly: "no implementation issue is
filed until Lorenzo signs off on each spike's entity mapping." Tracker: REB-344. In
English, per the repository rule; the two specs this document matches the shape of
(`2026-09-08-spazi-un-database-per-tenant-design.md`,
`2026-09-17-inviti-e-ruoli-di-uno-spazio-design.md`) are Italian only because they
predate that rule and are grandfathered records — this one is new.

---

## 0. What this spike is answering, and what it is not

REB-344 asks, for every concept in mastro's ledger (`~/projects/personal/mastro`,
`AGENTS.md` "The domain, in one paragraph"), whether PigroCRM already has an entity
that plays the same role, and — where none does — what the new table looks like, with
the reason written down rather than assumed. Lorenzo's own instruction on the card:
«cercherei di riutilizzare il più possibile le strutture esistenti, aggiungiamo solo se
strettamente necessario» (reuse existing structures as much as possible, add only what
is strictly necessary). Four hard constraints carry over unconditionally, cited from
mastro's `AGENTS.md`:

- **No country-specific logic outside a jurisdiction pack** (invariant 1, lines 72-80).
- **Pack rules follow the money, contract rules follow the counterparty** (invariant 2,
  lines 81-84) — this is the test this document applies whenever a fact could plausibly
  live on either side.
- **The state machine is enforced by the database, not by application checks**
  (lines 117-121), and **an approval is immutable and carries its proof** (line 58).
- **Agents propose, humans confirm** (invariant 3, lines 85-103) and **a derived datum
  never outlives its source document** (invariant 4, lines 105-107) — both bear directly
  on how a contract or a day gets its first draft of fields from a document already
  sitting in PigroCRM.

What this spike does **not** do: it does not implement anything, it does not file
implementation issues (see § 15), and it does not touch invoice emission, FatturaPA
export, or anything already working — `FiscalProfile`, `EmitterProfile` and the
`Invoice`/`InvoiceLine` emission path stay exactly as they are except where a new,
additive line type (the rivalsa charge, § 9) is appended to what an invoice can
already carry.

---

## 1. The mapping, at a glance

| mastro concept | PigroCRM's candidate | Decision |
|---|---|---|
| `client` (`db/schema/client.ts`) | `Customer` (`customers/models.py:10`) | **Reuse directly**, no schema change (§ 2). |
| `client_contact` (`client.ts:103-114`) | `Person` (`people/models.py:11`) | **Reuse directly**, no schema change (§ 2). |
| `contract` (`db/schema/contract.ts:109-262`) | nothing — `Deal` is pre-sale, no validity period, no renewal clause | **New table**, `contracts` (§ 3). |
| `rate_card` (`rate-card.ts:39-60`) | nothing | **New table**, `rate_cards` (§ 4). |
| `work_unit` + `work_unit_transition` (`work-unit.ts`) | nothing — `TimeEntry` has no state machine and is hours-only | **New tables**, `work_units` + `work_unit_transitions`, DB-trigger enforced (§ 5). |
| `approval` (`approval.ts`) | nothing | **New table**, `approvals`, immutable (§ 6). |
| `expense` (`expense.ts`) | `Cost`/`CostCategory` (`timetracking/models.py:198-248`) — **checked and rejected**, see § 7 | **New table**, `contract_expenses` (§ 7). |
| jurisdiction pack (`fiscal/pack.ts`) — ceilings, statutory charges | `FiscalProfile` (`fiscal/models.py:9`) — **checked and rejected as the pack itself**, see § 8 | **New module**, `pigrocrm.core.fiscal.pack` (data, no table); `FiscalProfile` stays as the issuer's live emission defaults, unchanged (§ 8). |
| rivalsa INPS election (`contract.ts:228-242`) | nothing | **New column** on `contracts` plus a new invoice-line-construction step, no `InvoiceLine`/`Invoice` schema change (§ 9). |
| `proposal` (`proposal.ts`) — contract intake and day intake | nothing — no review-before-write concept exists anywhere in PigroCRM today | **New table**, `proposals` (§ 10). |

---

## 2. Client and contact — `Customer` and `Person`, reused with no schema change

mastro's `client` (`client.ts:31-95`) carries legal identity for invoicing (`legalName`,
`taxId`, `vatId`), a registered address in separate columns, `sdiCode`/`pecAddress` for
FatturaPA routing, and a `country`. `Customer` (`customers/models.py:83-112`) already
carries every one of these under its own names — `ragione_sociale`, `partita_iva`,
`codice_fiscale`, `codice_sdi`, `pec`, `indirizzo`/`cap`/`comune`/`provincia`, `nazione`
— because PigroCRM's own FatturaPA export needed exactly the same facts first. No new
column, no new table. Two small, deliberate gaps, left alone rather than "fixed" here
because REB-344 does not ask for them:

- mastro's `taxId` is `UNIQUE` (nullable-safe, `client.ts:37-48`); `Customer.partita_iva`
  carries only a plain index (`customers/models.py:84`). Tightening it is a separate,
  narrow issue if it is ever wanted — this spike does not widen its own scope to fix a
  pre-existing constraint gap nobody asked about.
- mastro's `client.noticeChannel` (which legal-notice channel carries weight for this
  client) has no PigroCRM equivalent, and is not added: REB-344 asks this spike to
  record an approval's evidence (§ 6), not to validate who was authorised to send it or
  to send legal notices — mastro's own comment on that column says as much: "a
  notice-sending surface does not exist yet" even there (`client.ts:58-67`).

mastro's `client_contact` (`client.ts:97-114`), a person at the client with a
`canApprove` flag, maps onto `Person` (`people/models.py:11-89`), which already carries
`customer_id`, `nome`/`cognome`/`email`/`telefono`/`ruolo`. `canApprove` is **not**
ported: it exists in mastro to validate that an approval came from someone with the
authority to give it (`approval-conformance.ts`'s clause checks), which REB-344 does not
ask for — this spike only asks for an approval's evidence to be recorded (§ 6), not for
who-may-approve to be enforced. `approval.sender` (§ 6) is free text regardless, exactly
as mastro's own `approval` table stores it (`approval.ts:74`) independent of whether
`client_contact.canApprove` exists for that address.

---

## 3. Contract — genuinely new, `contracts`

Confirmed absent, as the card's own "Checked" paragraph states: no renewal-clause or
notice-deadline concept anywhere in `packages/core`. `Deal` (`deals/models.py:13-103`)
is the closest existing entity and is the wrong shape on every axis that matters: it is
a single row with no validity periods, no renewal type, and its own docstring frames it
as "a sales opportunity" that becomes `chiuso_il` when it leaves the pipeline — a
contract in mastro's sense outlives any one sales cycle and can renew into a new term
with its own rate card. Building the ledger inside `Deal` would mean overloading a
pre-sale pipeline object with a post-sale lifecycle it was never designed to hold.

New table `contracts`:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | `PrimaryKeyMixin` (`db/base.py:18-19`), like every other entity. |
| `customer_id` | UUID, FK → `customers.id`, NOT NULL, indexed | Mirrors `deals.customer_id` (`deals/models.py:66-68`) exactly, including no explicit `ondelete` (the schema-wide default). |
| `titolo` | `String(255)`, NOT NULL | One customer can hold several concurrent engagements; this is what tells them apart in a list, the same role `documents.titolo` plays for a document. |
| `inizio` | `Date`, NOT NULL | mastro's `startsOn` (`contract.ts:118`). |
| `fine` | `Date`, nullable | mastro's `endsOn`; null is open-ended. |
| `tipo_rinnovo` | `String(20)`, NOT NULL, CHECK IN `('nessuno','esplicito','opzione_controparte','tacito')` | mastro's `contractRenewalType` (`contract.ts:18-24`), translated one for one; a `String` + CHECK, matching how every closed set in this schema is already spelled (`documents.tipo`, `invoices.tipo`) rather than a Postgres `ENUM`. |
| `preavviso_rinnovo_giorni` | `Integer`, nullable | Required by a CHECK whenever `tipo_rinnovo <> 'nessuno'`, mirroring mastro's own comment that this column is "applicable, and required, for every renewal type except 'none'" (`contract.ts:133-135`). |
| `preavviso_disdetta_giorni` | `Integer`, NOT NULL | Termination notice, independent of renewal type (`contract.ts:136-138`) — feeds a forecast's irrevocability window later (REB-352's own concern, not this spike's). |
| `giorni_pagamento` | `Integer`, nullable | **Reuses `Customer.giorni_pagamento`'s exact shape** (`customers/models.py:101-108`, REB-326) rather than mastro's `PaymentTerms` JSONB discriminated union (`contract.ts:87-88`) — PigroCRM already solved "days after the invoice, or a day-of-month slide" as two flat columns at the customer level, and a contract needs the identical shape, not a second representation of the same fact. Null cascades to the customer's own term, the same way `Customer.giorni_pagamento` null already cascades to `FiscalProfile.giorni_scadenza` (`customers/models.py:103-104`). |
| `pagamento_fine_mese` | `Boolean`, **nullable** | Reuses `Customer.pagamento_fine_mese` (`customers/models.py:109-111`), but **nullable here where `Customer`'s own copy is not**, and tied to `giorni_pagamento` by a `CHECK (giorni_pagamento IS NULL) = (pagamento_fine_mese IS NULL)` — the same "together" shape this schema already uses for `ck_invoices_anno_numero_together`/`ck_invoices_snapshot_together`/`ck_invoices_competenza_together` (`invoices/models.py:144-146,156-159,172-179`). The override is therefore atomic: a contract either states both of its own payment-term facts or neither, and inherits the customer's whole term as a pair — never `giorni_pagamento` from the contract paired with a `pagamento_fine_mese` the contract never set, which would silently drop a customer's own end-of-month slide the moment any other override existed. |
| `cadenza_fatturazione` | `String(20)`, NOT NULL | mastro's `invoicingCadence` (`contract.ts:63-70`); exact value set (e.g. `mensile`/`trimestrale`/`annuale`/`a_consuntivo`) is the implementing issue's to pin against real engagements, not invented here. |
| `divisa` | `String(3)`, NOT NULL, default `'EUR'` | Matches `Invoice.divisa`'s own name and width (`invoices/models.py:89`) rather than mastro's `currency` — same fact, PigroCRM's own spelling. |
| `requires_prior_approval` | `Boolean`, NOT NULL, default `false` | mastro's `requiresPriorApproval` (`contract.ts:161`) — read directly by the work-unit state-machine trigger (§ 5). |
| `applies_social_charge` | `Boolean`, NOT NULL, default `false` | The rivalsa INPS election (§ 9), mastro's `appliesSocialCharge` (`contract.ts:228-242`). |
| `politica_spese` | `JSONB`, NOT NULL | mastro's `ExpensePolicy` (`contract.ts:90-99`), kept as a tagged JSONB union rather than flattened: unlike payment terms, this one genuinely needs a payload (the cap amount) only on one of its three variants, which two flat columns would not express as cleanly. |
| `stato` | `String(20)`, NOT NULL, default `'bozza'` | mastro's `contractStatus` (`contract.ts:73-78`), Italian values `bozza`/`attivo`/`terminato`/`scaduto`, matching `invoices.stato`'s own style. |
| `contratto_precedente_id` | UUID, FK → `contracts.id`, nullable, UNIQUE | The renewal chain (`contract.ts:244-261`), never edited by hand once set; the unique index is the other half of "a predecessor has at most one successor." **Left unset by this spike**: mastro sets it from a `contract_renewal`-targeted proposal, a fourth `proposal` target type § 10 deliberately does not model (its own scope-down to `contratto`/`giornata` only) — renewal automation (turning a signed renewal document into a new contract row on its own) is out of scope here; the column exists so a later, dedicated renewal-automation issue has somewhere to write, not because this spike wires anything up to it. |
| `note` | `Text`, nullable | Matches `Customer.note`/`Deal.note`. |
| `custom_fields` | `JSONB`, NOT NULL, default `{}` | Matches every other entity's shape and GIN index; extends `fields/schemas.py:17`'s `EntityType`, `schema_registry.py:25-35`'s `ENTITY_TYPES`/`CREATE_MODELS`, and `apps/web/src/lib/schema.ts`'s `EntityType` — the four-place edit `test_entity_types.py` already holds every prior entity to, no migration needed for that part. |

Deliberately **not** ported: mastro's `taxTreatment` (a per-contract VAT-treatment
assertion resolved against a jurisdiction pack's own treatment table, `contract.ts:139-160`)
and the four `approvalRequires*` clause-strictness flags (`contract.ts:161-197`,
mastro's own #703). Neither is asked for by REB-344's "Needed" — the tax treatment a
PigroCRM invoice line carries today already comes from `FiscalProfile.natura_default`/
`aliquota_iva_default`, which this spike leaves untouched (§ 8), and clause-strictness
validation is a materially larger feature (evaluating whether a given approval actually
satisfies a contract's own clause) that nothing in the card's text asks this spike to
design. `autoSendMail`/`templateLanguage` (`contract.ts:198-208`) are not ported either:
PigroCRM has no `email_template` concept of any kind yet, so there is nothing for these
flags to gate.

---

## 4. Rate card — genuinely new, `rate_cards`

mastro's own reasoning for why this is a separate table, not a column on `contract`,
applies unchanged: "a contract's price is not one number... a rate change at renewal is
a new card, not a new contract" (`rate-card.ts:28-37`). Nothing in PigroCRM plays this
role — `Deal.tariffa_oraria` (`deals/models.py:91`) and `TimeEntry.tariffa_applicata`
(`timetracking/models.py:157`) are both single numbers with no validity period and no
`kind` (daily vs. hourly vs. a lump sum), which is exactly the gap.

New table `rate_cards`:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | |
| `contract_id` | UUID, FK → `contracts.id`, NOT NULL, indexed | |
| `valido_da` | `Date`, NOT NULL | |
| `valido_a` | `Date`, nullable | Null is the open (current) card, mastro's own convention (`rate-card.ts:45-46`). |
| `tipo` | `String(20)`, NOT NULL, CHECK IN `('ricorrente_fisso','giornaliero','orario','una_tantum')` | mastro's `rateCardKind` (`rate-card.ts:6-11`). |
| `importo` | `Numeric(12,2)`, NOT NULL | Same precision as every other money column on this schema (`Invoice.totale`, `deals.valore_previsto`). |
| `unita` | `String(10)`, NOT NULL, CHECK IN `('ora','giorno','mese','anno','forfait')` | mastro's `rateUnit` (`rate-card.ts:14-16`). |
| `frazioni_ammesse` | `Numeric(4,2)[]`, NOT NULL, default `ARRAY[1]` | mastro's `allowedFractions` (`rate-card.ts:50-54`) — Postgres array, same precision. |
| `ore_minime` | `Numeric(6,2)`, nullable | mastro's `minimumHours` (`rate-card.ts:55-57`); meaningful, and CHECK-enforceable, only for `tipo = 'orario'`. |
| `periodo_erogazione` | `String(20)`, nullable, CHECK IN `('mensile','trimestrale','annuale','una_tantum')` | mastro's `disbursementPeriod` (`rate-card.ts:18-26`); meaningful, and CHECK-enforceable, only for `tipo = 'ricorrente_fisso'`. |

Non-overlapping validity is a database rule, not an application check, matching mastro's
own admission that this "resolves unambiguously" only because of "the exclusion
constraint in the accompanying custom migration" (`rate-card.ts:33-37`) — the same
reasoning as the state machine (§ 5). Postgres has no exclusion constraint over a plain
`date` range without `btree_gist`; the migration that adds this table also runs
`CREATE EXTENSION IF NOT EXISTS btree_gist`, the same unconditional style
`0021_pg_trgm_search_indexes.py:56` already uses for `pg_trgm` (no capability check —
"the alternative is an application that starts and scans sequentially in silence,"
`0021...py:14-18`, and the same argument holds for a rate card silently allowing two
overlapping cards instead).

---

## 5. The day lifecycle — `work_units` and `work_unit_transitions`, database-trigger enforced

`TimeEntry` (`timetracking/models.py:58-165`) is the closest existing entity and is
still the wrong shape: it has no state machine (only `invoice_line_id IS NULL` versus
not, `fatturabile` true or false), no `approval_id`, and it is priced per entry rather
than resolved against a contract's own rate card at pricing time. Building the ledger
on top of `TimeEntry` would mean bolting a ten-state graph onto a table designed for a
two-state fact ("billed" or "not yet"). New tables, translating mastro's ten states
(`work-unit.ts:39-50`) one for one into the Italian-value convention every other closed
set on this schema already uses (`invoices.stato`, `documents.stato`):

`work_units`:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | |
| `contract_id` | UUID, FK → `contracts.id`, NOT NULL, indexed | |
| `data` | `Date`, NOT NULL | Matches `TimeEntry.data`'s own name and type. |
| `quantita` | `Numeric(6,2)`, NOT NULL | 1 (a full day), 0.5 (half a day), or an hours figure on an hourly card — "which one applies is resolved against the contract's rate card at pricing time, not validated here" (`work-unit.ts:54-56`), unchanged. |
| `descrizione` | `Text`, NOT NULL | Matches `TimeEntry.descrizione`'s own name and type; mastro calls the equivalent column `scope`. |
| `stato` | `String(24)`, NOT NULL, default `'proposto'` | `proposto`/`approvato`/`lavorato`/`lavorato_senza_approvazione`/`fatturato`/`pagato`/`contestato`/`revocato`/`rifiutato`/`non_fatturabile`, translating `work-unit.ts:39-50` one for one. |
| `approval_id` | UUID, FK → `approvals.id`, nullable, `ondelete='RESTRICT'` (the default) | mastro's own reasoning applies unchanged: a day already relying on an approval cannot have it pulled out from under it (`work-unit.ts:59-61`). |
| `invoice_line_id` | UUID, FK → `invoice_lines.id`, nullable, `ondelete='SET NULL'` | **Diverges from mastro's own `RESTRICT` choice on purpose** — mastro rejects deleting a line under an invoiced/paid day (`work-unit.ts:59-61`); PigroCRM's own `TimeEntry.invoice_line_id` already uses `SET NULL` for the same relationship (`timetracking/models.py:161-163`) precisely so `InvoiceService.replace_lines` can rebuild a draft's line list wholesale (`invoices/service.py:494-497`) without leaving orphaned rows. A `work_unit` follows its sibling `TimeEntry`'s own proven mechanism rather than mastro's, so both billable-unit tables behave identically under a line rebuild — the implementing issue must still check, as `TimeEntry`'s own `ck_time_entries_billed_not_deleted` does, that `replace_lines` never operates on an invoice that has already left `bozza` (spec 4 §3's own lock), which is what keeps an `'invoiced'`/`'pagato'` `work_unit` from actually losing its line in practice. |
| `note` | `Text`, nullable | |
| `created_at`/`updated_at` | via `TimestampMixin` | |

`work_unit_transitions` (append-only, one row per state change):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | |
| `work_unit_id` | UUID, FK → `work_units.id`, NOT NULL, indexed with `seq` | Mirrors `work-unit.ts:97-134`'s own composite index. |
| `stato_precedente` | `String(24)`, nullable | Null on the row created at INSERT. |
| `stato_nuovo` | `String(24)`, NOT NULL | |
| `attore` | `JSONB`, NOT NULL | Mirrors mastro's `TransitionActor` (`work-unit.ts:84-87`): `{"kind":"human","email":...}` / `{"kind":"agent","proposal_id":...}` / `{"kind":"system"}` — PigroCRM already has an `Actor` model (`actor.py:135`) carrying `type`/`id`, so this is a small, closed JSON shape derived from it, not a new authentication concept. |
| `motivo` | `Text`, NOT NULL | |
| `created_at` | `TIMESTAMPTZ`, default `clock_timestamp()` | mastro's own reasoning for overriding the usual `now()` default applies unchanged: this is a log, and every row `TimestampMixin`'s frozen-per-transaction `now()` would give the same insertion order could still display backwards (`work-unit.ts:109-118`). |
| `seq` | `bigserial` | The real ordering column, for the identical reason mastro states: `clock_timestamp()` can still tie within one statement, `nextval()` cannot (`work-unit.ts:122-131`). |

**Both tables are enforced by the database**, matching mastro's own migration shape
almost line for line (`drizzle/0012_work_unit_state_machine.sql`,
`drizzle/0013_worked_without_approval.sql`, cited in full in § 12): a `BEFORE INSERT OR UPDATE`
trigger function rejects any transition edge not in the allowed list, redirects a
`'lavorato'` write with no `approval_id` on a `requires_prior_approval` contract into
`'lavorato_senza_approvazione'` automatically, and recovers it back the moment an
`approval_id` is linked — never as an application-layer decision a future write path
could skip. A second, `AFTER`, trigger inserts the `work_unit_transitions` row itself,
so no write path can produce a state change this table does not see, including a future
importer or a direct `psql` session. `attore`/`motivo` reach the trigger the same way
mastro's do: as session-local Postgres settings (`set_config('pigrocrm.actor', ..., true)`,
scoped to the transaction) set immediately before the write by the repository layer,
falling back to a system actor and a generic reason when unset rather than failing —
mastro's own reasoning (`0012_work_unit_state_machine.sql:97-105`) for why the log must never go silently
incomplete.

A partial unique index — `(contract_id, data)` `WHERE stato NOT IN ('rifiutato','revocato')`
— keeps at most one live day per contract per date, mirroring `0012_work_unit_state_machine.sql:40-42`.

---

## 6. Approval evidence — `approvals`, immutable

Nothing in PigroCRM records "a human approved this in writing, and here is the proof."
New table `approvals`:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | |
| `contract_id` | UUID, FK → `contracts.id`, NOT NULL, indexed | |
| `canale` | `String(20)`, NOT NULL, CHECK IN `('email','posta_certificata','raccomandata','corriere','altro')` | mastro's `noticeChannel` (`client.ts:11-17`). |
| `mittente` | `Text`, NOT NULL | mastro's `sender` — free text, an email address or a name, never validated against `Person`/`clientContact` (§ 2). |
| `ricevuto_il` | `TIMESTAMPTZ`, NOT NULL | mastro's `receivedAt`. |
| `message_id` | `Text`, nullable | Present for email, absent for a channel with no equivalent. |
| `document_id` | UUID, FK → `documents.id`, NOT NULL, `ondelete='RESTRICT'` | The archived original this approval interprets — see § 11 for how `documents`' ownership widens to make this legal. |
| `estratto` | `Text`, NOT NULL | The verbatim text the approval rests on, mastro invariant 4 in one column. |
| `origine` | `JSONB`, NOT NULL | `{"kind":"manuale"}` / `{"kind":"agente","proposal_id":...}`, mirroring `approval.ts:45-53` trimmed to the two kinds REB-344 actually needs — mastro's third kind, `carried_forward` (a renewed term reusing a predecessor's own evidence, `approval.ts:20-43`), is a renewal-automation feature this spike does not design, per the same out-of-scope call as `contracts.contratto_precedente_id`'s own note above. |
| `created_at`/`updated_at` | via `TimestampMixin` | |

Immutable by a `BEFORE UPDATE` trigger that unconditionally raises, the same one-line
function mastro uses (`raise_immutable_violation()`, cited in § 12) — a correction is
always a new `approval` row, never an edit of an existing one. Deletion is left to the
ordinary FK: `work_units.approval_id` already references it with the default
`RESTRICT`, so an approval something relies on cannot be deleted either, without a
second, redundant trigger.

---

## 7. Rebillable expenses — `Cost` does not fit; new `contract_expenses`

This was the one candidate REB-344's own "Checked" paragraph flagged as worth
verifying before assuming a new table, and it does not survive the check. mastro's
`expense` (`expense.ts:34-49`) is a **client-rebillable** cost: it belongs to a
contract, carries `preAuthorised`/`reimbursable` (computed by its own trigger against
the contract's expense policy, `expense.ts:20-25`), and has an `invoiceLineId` — it is
designed to become an invoice line the client pays.

`Cost` (`timetracking/models.py:198-248`) is structurally the opposite concept, by its
own docstring: "money that actually left, towards somebody else, with a receipt to
prove it" is the freelancer's **own** P&L cost, and "any apportionment key... has one
precise and unacceptable consequence: a deal's margin would move when a different deal
was invoiced" (`timetracking/models.py:210-213`). Concretely, `Cost` has **no
`invoice_line_id` column at all** — there is no path from a `Cost` row onto an invoice,
because none was ever meant to exist. Reusing it for a rebillable expense would mean
adding the one column (`invoice_line_id`) and the one behaviour (becomes revenue rather
than reduces margin) that its own design deliberately excludes, which is not "reusing an
existing structure," it is quietly inverting what the structure means. `CostCategory`
(`timetracking/models.py:26-55`) is the categorisation half and is genuinely reusable
for a rebillable expense's own category, since categorising an outflow is the same
problem either way — see the table below.

New table `contract_expenses`:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | |
| `contract_id` | UUID, FK → `contracts.id`, NOT NULL, indexed | |
| `category_id` | UUID, FK → `cost_categories.id`, NOT NULL, indexed | **Reuses `CostCategory` directly** — same table, same admin UI, same code list a freelancer already maintains. |
| `data` | `Date`, NOT NULL | |
| `importo` | `Numeric(12,2)`, NOT NULL | |
| `descrizione` | `Text`, NOT NULL | |
| `pre_autorizzata` | `Boolean`, NOT NULL, default `false` | mastro's `preAuthorised`. |
| `riferimento_autorizzazione` | `Text`, nullable | mastro's `authorisationReference` — freeform evidence, not a foreign key, the same treatment `contracts.contratto_precedente_id`-adjacent evidence gets nowhere near as strict as `approvals.document_id`, because this is a lighter-weight fact than a day's own approval. |
| `rimborsabile` | `Boolean`, NOT NULL, default `true` | **Never set by application code** — a trigger computes it from `pre_autorizzata` against the owning contract's `politica_spese`/expense-pre-authorisation flag on every insert or update, mirroring `expense.ts:20-25`: an expense that fails the check is still recorded, flagged, never silently accepted or rejected outright, the same non-blocking philosophy as `lavorato_senza_approvazione` (§ 5). |
| `invoice_line_id` | UUID, FK → `invoice_lines.id`, nullable, `ondelete='SET NULL'` | Same divergence from mastro's own `RESTRICT`, and the same reason, as `work_units.invoice_line_id` (§ 5). |
| `document_id` | UUID, FK → `documents.id`, nullable | The receipt — reuses PigroCRM's own document store exactly as `Cost.document_id` already does (`timetracking/models.py:247`), rather than mastro's polymorphic `document.ownerType` widening for this case. |
| `created_at`/`updated_at` | via `TimestampMixin` | |

---

## 8. Ceilings and the jurisdiction pack — a new, small module; `FiscalProfile` stays as it is

This is the question REB-344 asks explicitly: does `FiscalProfile` become the pack, or
stay beside a new one. **It stays beside a new one**, for a reason grounded in what
`FiscalProfile` actually is today, not merely in resisting a large-looking migration.

`FiscalProfile` (`fiscal/models.py:9-74`) is deliberately **not historicised** — its own
docstring states the reason: "a regime changes on 1 January, but spec 6.2 forbids
back-dating an invoice past the start of the current year, so no emission ever needs a
previous period's parameters" (`fiscal/models.py:19-23`). It is a **singleton** read
live, at issue time, by `InvoiceService.issue` and `FatturaPAExporter` to fill in
`codice_regime`, `aliquota_iva_default`, `natura_default`, the bollo thresholds, and the
FatturaPA payment-mode codes — every one of these is an **emission-time default for the
issuer's own document**, not a fact about a ceiling or a jurisdiction's charge schedule.

mastro's `fiscal_profile` (`fiscal.ts:35-52`) is the opposite shape on purpose: it
**is** historicised (`validFrom`/`validTo`, with an exclusion constraint forbidding two
profiles covering the same instant), because its only job is to say which
`FiscalPack` — `pack_id`/`pack_version` — governed revenue at a given moment, so that
`fetchLedgerRows` can walk several fiscal years, each possibly under a different regime,
and get each one's ceilings and charges right. PigroCRM has never needed this: Lorenzo
runs one regime, has run one regime the whole time this product has existed, and nothing
in REB-344 or the project asks for multi-jurisdiction support. Turning `FiscalProfile`
into a versioned pack pointer would touch `InvoiceService.issue` and
`FatturaPAExporter` — the one part of this codebase REB-344 explicitly does not ask this
spike to touch — for a capability nobody has asked for.

So: **a new, small, pure-data module**, `pigrocrm.core.fiscal.pack`, ported from
mastro's own `FiscalPack`/`Ceiling`/`StatutoryCharge` interfaces (`fiscal/pack.ts:79-221,
306-350`) but trimmed to exactly what a ceiling engine and a rivalsa line need — no
`TaxTreatment`/`resolveTaxTreatment` machinery, since `FiscalProfile.natura_default`/
`aliquota_iva_default` already solve that problem for the one regime this product
supports, and duplicating it would be the "two-mechanism problem" this repository's own
`DECISIONS.md` already warns against (cited by the invitations spec, `2026-09-17...md:149`).
One shipped pack, `IT_FLAT_RATE_PACK`, a direct, verified port of `packs/it-flat-rate.ts:104-284`
(the currently-in-force version, `effectiveFrom: '2023-01-01'`, not the pre-2023
`itFlatRatePackV0`, since PigroCRM has no history to reconcile against an older regime):

- Two ceilings, both `basis: cash_received_calendar_year`, `perimeter: all_clients`:
  €85,000 (`it-flat-rate.ts:127-129`, loses the regime from the following year) and
  €100,000 (`it-flat-rate.ts:159-162`, loses it immediately, same year).
- One statutory charge relevant here: the rivalsa (§ 9). mastro's other charge, virtual
  stamp duty (`it-flat-rate.ts:224-253`), is **not ported** — PigroCRM's own bollo is
  never recharged to the client (confirmed by the card's own "Checked" paragraph,
  quoting `render/assets/template-invoice.md:87` and `invoices/pdf.py:213-218`), so
  there is no analogous charge to compute here; `FiscalProfile.applica_bollo`/
  `soglia_bollo`/`importo_bollo` (`fiscal/models.py:42-48`) already govern the
  issuer-borne case and are untouched.

No new DB row for the pack itself — it is a Python constant the ceiling service imports,
the same way mastro's own engine "never names a concrete pack" (`pack.ts:1-6`) and a
future second pack (a standard-regime Italian pack, or a first non-Italian one) is a new
module, never a branch on `FiscalProfile.codice_regime` inside the ceiling logic. What
**does** need a database fact is *which* pack governs — one pair of columns on
the existing `FiscalProfile` row, `pack_id`/`pack_version` (`String`, no historicisation,
matching `FiscalProfile`'s own un-historicised shape rather than mastro's), read once by
the ceiling service at evaluation time. **`NOT NULL`, not nullable**, with a
server-side default of `'it-flat-rate'`/`'1'` and a migration `UPDATE` backfilling
every existing `fiscal_profile` row to the same pair — every space this product has
ever provisioned is on that one regime today, so there is no real "which pack" question
for an existing row to leave unanswered, and a nullable pair would only invent one (an
undefined ceiling evaluation the day someone reads it before someone else sets it).
`FiscalProfile` itself has no seeding step to piggyback on — it is created lazily, on
the first admin `upsert`, not at space provisioning (`fiscal/service.py:67-70`:
`if profile is None: profile = self.repo.add(FiscalProfile(**payload))`, no caller in
`TenantService.provision` or `ensure_space_defaults` ever constructs one) — so a brand
new space's first `FiscalProfile` row picks up `pack_id`/`pack_version` from the
column's own server-side default at that same `INSERT`, exactly as `applica_bollo`,
`condizioni_pagamento` and every other `NOT NULL`-with-a-default column on this table
already does today for a space that has never touched Impostazioni's fiscal panel. This
is the one place `FiscalProfile` gains a column for this spike, and it is a pointer,
not a duplication of pack data.

Revenue for the ceiling's `cash_received_calendar_year` basis reads straight off
`Invoice`'s own existing columns — `stato_pagamento = 'incassato'` and `data_incasso`
(`invoices/models.py:107-108`) already are the cash-basis facts a ceiling needs, with no
new column: `evaluate_ceiling(pack, session)` sums `imponibile` (plus any future charge
slot the pack declares `countsTowardsRevenuePerimeter` for, mirroring `pack.ts:582-598`)
across paid invoices in the calendar year, grouped by the ceiling's own perimeter.

---

## 9. The rivalsa INPS election and its invoice line

Confirmed absent everywhere (`grep -ri rivalsa packages/core apps/` returns nothing but
an unrelated FatturaPA doc-comment, per the card's own check). mastro's pack declares
the rate and its one statutory effect; **the election is the contract's**, per invariant
2 — "electing it is an invoicing-time decision" tied to the counterparty, not the
regime (`it-flat-rate.ts:264-266`, `contract.ts:236-241`). `contracts.applies_social_charge`
(§ 3) is that election, ported verbatim.

The computation itself needs no new invoice schema at all. `InvoiceLine` already
supports exactly the shape a VAT-exempt statutory line needs: `aliquota_iva = 0` paired
with a `natura` code, enforced together by `ck_invoice_lines_natura_agrees_with_rate`
(`invoices/models.py:260-263`) — precisely the FatturaPA pairing rivalsa needs, since it
carries the same Natura N2.2 treatment as every other forfettario line
(`it-flat-rate.ts:255-259`, `278`). A rivalsa line is therefore an ordinary
`InvoiceLine` row: `descrizione` naming the surcharge and its legal basis (art. 1, comma
212, legge 662/1996), `natura = 'N2.2'`, `aliquota_iva = 0`, `prezzo_totale` computed as
4% (`basisPoints: 400`, `it-flat-rate.ts:278`) of the base the pack names.

The base matters and is where the spec has to be explicit, per the card's own flag:
mastro computes rivalsa on the fee **plus any recharged stamp duty**
(`it-flat-rate.ts:270-278`, `chargeSlotOrder`, `pack.ts:181-193`), because a recharged
bollo is itself compenso. Confirmed separately (§ 0, and the card's own check): PigroCRM
never recharges its bollo to the client (`render/assets/template-invoice.md:87`), so
today the base collapses to the fee alone — `imponibile` of the lines the rivalsa
line itself does not include. This is stated here explicitly, not left implicit, exactly
because it is a silent trap the moment either fact changes: **if a future PigroCRM
invoice ever does recharge stamp duty to a client on the same invoice as a rivalsa line,
the rivalsa base must widen to include it**, in mastro's own `chargeSlotOrder` sense —
the pack module (§ 8) is where that ordering rule belongs when it is needed, not
reinvented ad hoc in the invoice-assembly code the day it comes up.

Line construction itself is a new step in whatever assembles `InvoiceLineIn` rows for a
contract-linked invoice (the same call site that will gather unbilled `work_units`,
mirroring how `TimeEntry.invoice_line_id IS NULL AND fatturabile IS TRUE`
(`timetracking/repository.py:42-46`) already gathers unbilled hours today): when the
invoice's underlying contract has `applies_social_charge = true`, append one rivalsa
line after the fee lines, computed from the pack's declared rate against the fee
subtotal already assembled. No change to `InvoiceService.replace_lines` itself
(`invoices/service.py:494-497`) — it still receives "the whole list, never a partial
patch," exactly as today; only the caller that builds that list for a contract-linked
invoice grows a new step.

---

## 10. Contract and day intake — review before write, `proposals`

The project description assigns this spike two, not one, "how does a document become a
proposal a human confirms" questions: a **contract**'s first draft of fields (REB-344's
own text) and a **day**'s proposal from an approval email (the project description's
"a coding-agent session... writing to a 'propose a day' PigroCRM endpoint the ledger
spike will define"). Both are the same shape — invariant 3, "agents propose, humans
confirm," with invariant 4's evidence discipline — so one table serves both, mirroring
mastro's own `proposal` (`proposal.ts:124-173`) trimmed from its four target types down
to the two this spike actually needs.

New table `proposals`:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK | |
| `document_id` | UUID, FK → `documents.id`, NOT NULL, `ondelete='RESTRICT'` | The archived original the extraction read — never optional, mastro invariant 4. |
| `contract_id` | UUID, FK → `contracts.id`, nullable | Required by a CHECK unless `target_type = 'contratto'` (first intake has no contract row to point at yet), mirroring `proposal.ts:167-172`'s own CHECK exactly. |
| `target_type` | `String(20)`, NOT NULL, CHECK IN `('contratto','giornata')` | Trimmed from mastro's four (`proposal.ts:64`) to the two REB-344 asks for; `'fattura'` is REB-351's own concern, and `'rinnovo_contratto'` is the renewal-automation feature this spike leaves out (see `contracts.contratto_precedente_id`'s own note, § 3). |
| `campi_proposti` | `JSONB`, NOT NULL | Shape depends on `target_type`: for `'contratto'`, a candidate `contracts` row (plus its first `rate_cards` row); for `'giornata'`, `{contract_id, data, quantita, descrizione}`. |
| `estratto` | `Text`, NOT NULL | The verbatim span the proposed fields rest on — shown next to them at review, exactly as `approval.excerpt` sits next to the days it covers (§ 6). |
| `tipo_estratto` | `String(12)`, NOT NULL, default `'citato'`, CHECK IN `('citato','trascritto')` | mastro's `excerptKind` (`proposal.ts:21-43`): `citato` is a span the application located in the document's own text (verifiable, searchable); `trascritto` exists only for a scanned page with no text layer, and is never rendered as a quotation for the same reason mastro's own comment gives — nothing here can verify a string against a page image. |
| `confidenza` | `Numeric(3,2)`, NOT NULL | 0 to 1, the producer's own declared confidence, never computed after the fact (`proposal.ts:90-91`). |
| `motivo_confidenza` | `Text`, nullable | |
| `stato` | `String(12)`, NOT NULL, default `'in_attesa'`, CHECK IN `('in_attesa','accettata','rifiutata')` | mastro's `proposalStatus` (`proposal.ts:18`). |
| `campi_accettati` | `JSONB`, nullable | Set once, alongside `stato`, never edited afterwards — kept **separately** from `campi_proposti` rather than as a computed diff, for the identical reason mastro states: "the diff between the two is the whole point... it stays correct forever only if neither side is ever overwritten" (`proposal.ts:114-122`). |
| `id_risultato` | UUID, nullable | The row the accepted proposal produced (a `contracts.id` or a `work_units.id`) — not a foreign key, the same polymorphic-reference reasoning `documents`' own `ownerId`-style columns already use elsewhere in this schema, and only ever set by the accept path itself in the same transaction as the row it points at. |
| `deciso_da` | `Text`, nullable | |
| `deciso_il` | `TIMESTAMPTZ`, nullable | |
| `created_at`/`updated_at` | via `TimestampMixin` | |

**Accepting a `'giornata'` proposal never leaves a day at `'proposto'`**, mirroring
mastro's own rule verbatim ("a proposal exists only because a human wrote something
approving it," `AGENTS.md:62-64`): the accept path creates the `approval` row (or reuses
one already created from the same source document) and writes the `work_unit` straight
to `'approvato'`, both in one transaction — never as two separate calls a caller could
interrupt between.

**How a document already in PigroCRM feeds this table**, regardless of which door it
came in through — the three doors named in the project description all converge on the
same `documents` row before a proposal is ever created:

1. **`import_drive_file`** (`apps/mcp/src/pigrocrm_mcp/tools/drive_privileged.py:227-262`) already
   archives a contract PDF from a configured Drive root as a `documents` row typed
   `contratto`, with full Drive provenance. Today that is where it stops — "only as an
   attachment, no field ever leaves the PDF" (the card's own words). This spike's answer:
   a new step, run on demand by whoever is driving the intake session (an agent reading
   the document through `read_drive_file` or the CRM's own document text extraction,
   same as any other document), writes a `proposals` row pointing at that same
   `document_id`, with `campi_proposti` and a verbatim `estratto` located in the text
   `import_drive_file` already archived.
2. **A direct upload** through the CRM's own document upload path produces a `documents`
   row the identical way, `tipo = 'contratto'`, and the same proposal-creation step runs
   against it.
3. **A mail attachment read through `gws-personal`** — the same "no standing worker,
   an on-demand agent session" pattern the project description already settled for a
   day's approval email — first archives the attachment as a `documents` row (through
   the same import path as (1) or (2), so there is exactly one way a PDF becomes a
   `documents` row regardless of where it was read from), then produces the proposal the
   identical way.

None of the three needs its own proposal-creation code path: the proposal step reads a
`documents` row and a target type, never a source. This is the concrete meaning of
"the source of the document does not change the shape of that review step" the project
description asks for.

---

## 11. Documents: widening ownership to include a contract

`Document` (`documents/models.py:22-33`) today enforces "belongs to a customer **or**
to a deal — never both, never neither," a single `CHECK`,
`ck_documents_customer_xor_deal` (`documents/models.py:58-61`):
`(customer_id IS NOT NULL) <> (deal_id IS NOT NULL)`. Once `contracts` exists, a
contract's own signed document (and any later addendum) should be discoverable **from
the contract**, not only from the proposal row that produced it (a proposal is a
disposable audit trail once decided, not where a reader goes looking for "this
contract's own paperwork").

This widens the exclusivity rule to a three-way exclusive-or — a new nullable
`contract_id` foreign key joins the existing nullable `customer_id`/`deal_id` as a
third, still-mutually-exclusive owner, with `ck_documents_customer_xor_deal`
(`documents/models.py:58-61`) widened from its current two-way `<>` into a three-way
exactly-one-of-three check. This is the identical situation mastro already solved
once, even though the column shape differs: `document.ownerType`, originally
`CHECK (owner_type IN ('contract'))`, widened to `IN ('contract', 'approval')` the
moment `approval` needed to own a document too (`0011_approval_constraints.sql:50-57`)
— a metadata-only migration precisely because `ownerType` is `text` plus a `CHECK`,
never a Postgres `ENUM`. PigroCRM's own `documents.tipo`/`stato` are spelled the
identical way for the identical reason (`documents/models.py:25-28`, "a future value
costs a schema constant rather than an `ALTER TYPE` migration") even though PigroCRM
tracks ownership through separate nullable foreign keys rather than mastro's single
discriminator column — the `CHECK` that ties them together is exactly as cheap to
widen either way.

`DocumentListQuery`'s own filter set (`documents/repository.py:168-176`) widens with the
column: `customer_id`/`deal_id` are already optional filters there, and `contract_id`
joins them the same way, in the same `list()` method, exposed through the existing
`GET /api/documents` and whatever MCP resource already lists a customer's documents —
without this, a re-owned document would silently drop out of every existing
customer-scoped list the moment it moved, discoverable only by opening the contract
that now owns it. `import_drive_file`'s own contract-archiving call keeps working
unchanged during first intake (there is no contract row yet, so it still owns the
document by `customer_id`, as it does today); the accept path for a `'contratto'`
proposal is what re-points the document's ownership at the newly created contract —
through this same widened `list()`, never a second, parallel query — the same "widen at
accept time, not at archive time" order mastro's own `createApproval` follows for
`approval`'s ownership (`0011_approval_constraints.sql`'s own comment, cited in § 6).

---

## 12. Migration and the trigger infrastructure PigroCRM does not have yet

**Checked**: zero `CREATE TRIGGER`/`CREATE FUNCTION` statements exist anywhere in
`packages/core/migrations/versions` today (`grep` across every migration file, empty).
Every invariant this schema currently enforces at write time — the invoice register's
gap-free numbering, `InvoiceLine`'s Natura/rate pairing, `Document`'s ownership
exclusivity — is a plain `CHECK`/`UniqueConstraint`/partial `Index`, all expressible
without procedural SQL. **The day lifecycle's DB-trigger enforcement is therefore new
infrastructure for this codebase, not an extension of an existing pattern** — this is
stated plainly here so the implementing issue does not go looking for a trigger to
follow and conclude one was missed.

The mechanism itself is not novel to build: Alembic already runs arbitrary SQL through
`op.execute()`, exactly as `0021_pg_trgm_search_indexes.py:56` already does for
`CREATE EXTENSION`, and `Base.metadata.create_all()` (the test schema's own builder,
`tests/conftest.py:27-41`) runs a migration's raw SQL exactly as production does only if
that SQL is also reachable outside the migration object — mastro's own answer to this
(`0012_work_unit_state_machine.sql`'s header comment, "installed for schema-convention
consistency" trigger aside) is to keep every trigger function and its `CREATE TRIGGER`
call in the migration itself and have the test fixture run migrations rather than only
`create_all` for any table a trigger touches — the implementing issue must decide
whether PigroCRM's own `tests/conftest.py:27-41` (currently `create_all`-only) needs an
`alembic upgrade head` alternative fixture for the tables this spike adds, or whether
running the trigger DDL as a second `connection.execute()` block alongside its own
`CREATE EXTENSION IF NOT EXISTS pg_trgm` call (mirroring line 41 of that file) is
enough — a decision this spec leaves open because it is a test-infrastructure choice,
not an entity-mapping one.

The three trigger functions to port, verified against mastro's own migrations:

- **`work_unit_enforce_state_machine`** (`0012_work_unit_state_machine.sql:50-92`,
  widened by `0013_worked_without_approval.sql:8-68` for the automatic
  `worked_without_approval` redirect and recovery) — a `BEFORE INSERT OR UPDATE`
  trigger reading `contracts.requires_prior_approval` for the row's own `contract_id`
  (impossible from a plain `CHECK`, since a `CHECK` cannot subquery another table),
  rejecting any `state` transition outside the fixed edge list, and rejecting an INSERT
  starting anywhere but the three legal entry states.
- **`work_unit_log_transition`** (`0012...sql:106-135`) — an `AFTER INSERT OR UPDATE`
  trigger writing exactly one `work_unit_transitions` row per real state change, reading
  actor/reason from session-local settings the repository layer sets immediately before
  the write.
- **`raise_immutable_violation`** (`0011_approval_constraints.sql:41-48`) — the one
  generic function both `approvals` (`BEFORE UPDATE`) and `work_unit_transitions`
  (`BEFORE UPDATE OR DELETE`, append-only) attach to, unconditionally rejecting the
  operation named in the exception.

Each ports as an Alembic revision whose `upgrade()` is a sequence of `op.execute(...)`
calls carrying the translated SQL (table/column names per §§ 3-7, state values per § 5's
Italian translation table), the next open revision after `0038_users_last_login_at.py`
(`0039` as of this writing — whoever implements the foundations issue must recheck
against `origin/main` first, the same rule the invitations spec states for its own
migration number). `downgrade()` drops the triggers, functions and tables in reverse
dependency order, the ordinary Alembic discipline this schema already follows
everywhere else.

---

## 13. Per-space compatibility

Nothing in §§ 2-12 needs to know what a space is. Every new table lives in the same
per-space Postgres database as `customers`/`deals`/`invoices` (spec
`2026-09-08-spazi-un-database-per-tenant-design.md` § 1: "no service, no table and no
query learns what a tenant is"), migrated by the same `alembic upgrade head` every space
already receives at boot (`AGENTS.md`'s own "Migrations" section, "a new migration
reaches the spaces at the first boot after the deploy"). The one place this spike
touches something space-aware at all is `FiscalProfile`'s new `pack_id`/`pack_version`
pointer (§ 8) — already a per-space singleton row today, so it costs nothing new: each
space simply points at its own pack, and a future non-Italian space would point at a
different one without any code in the ceiling engine learning that fact.

---

## 14. What the implementing issues must test

Each bullet names the concern, not a full test list — the implementing issue writes the
actual tests, matching this schema's own conventions (real Postgres, per
`tests/conftest.py:27-30`: "JSONB, GIN and pg_trgm do not exist in SQLite, so there is no
shortcut" — the same is now true of triggers).

- **Foundations** (`contracts`, `rate_cards`): the renewal-notice CHECK, the
  non-overlapping-validity exclusion constraint (two overlapping cards on one contract
  refused, two adjacent ones accepted), the `giorni_pagamento`/`pagamento_fine_mese`
  together-CHECK refusing a half-set override and a fully-null pair correctly inheriting
  both of the customer's own facts, the four-place `EntityType` widening
  (`test_entity_types.py`'s own pattern, extended), and a document re-owned by a
  contract still appearing in `DocumentListQuery.contract_id`'s results.
- **Day lifecycle** (`work_units`, `work_unit_transitions`, `approvals`): every legal
  edge accepted, every illegal edge rejected (mirroring `worked-without-approval.test.ts`'s
  own exhaustive table), the automatic redirect and recovery, the append-only log
  surviving a bulk update without losing an intermediate state, approval immutability
  (`UPDATE` refused outright), the one-active-day-per-contract-per-date partial index.
- **Rebillable expenses**: `rimborsabile` computed correctly against every combination
  of `pre_autorizzata` and the contract's own `politica_spese`/pre-authorisation flag,
  never rejecting the write itself.
- **Ceilings**: a freshly-created `FiscalProfile` row (no explicit `pack_id`/
  `pack_version` supplied) carrying the server default rather than a null, the pack's
  two thresholds against a synthetic set of paid invoices crossing each one, the
  cash-basis perimeter reading `stato_pagamento`/`data_incasso` correctly, a per-client
  perimeter (if a contract-origin ceiling is ever added) staying independent of the
  pack-origin ones.
- **Rivalsa**: the 4% line's `natura`/`aliquota_iva` pairing passing the existing
  `ck_invoice_lines_natura_agrees_with_rate`, the base collapsing to the fee alone while
  stamp duty stays unrecharged, and an explicit test asserting the base **would** widen
  the day stamp duty recharging is ever turned on (a documented trap, not just a
  passing case).
- **Proposals**: the CHECK tying `contract_id` nullability to `target_type`, a
  `'giornata'` accept creating both the `approval` and the `work_unit` in one
  transaction, `campi_proposti`/`campi_accettati` never overwritten, `estratto`
  verifiable against the source document's own text for `tipo_estratto = 'citato'`.

---

## 15. Proposed follow-up work

Per this project's own gate ("no implementation issue is filed until Lorenzo signs off
on each spike's entity mapping"), the items below are **not** filed as Linear issues.
They are the implementation order this spec's own "Done when" asks it to name, recorded
here for whoever opens them once the mapping above is approved.

1. **Foundations** — `contracts`, `rate_cards`, the `EntityType` four-place widening for
   `contracts`, `Document`'s ownership widening and its `DocumentListQuery.contract_id`
   filter (§ 11), and the `btree_gist` + non-overlap exclusion constraint. *Acceptance:
   a contract with a validity-checked sequence of rate cards can be created, read and
   listed through the API, a second rate card overlapping an existing one on the same
   contract is refused by the database, not by a service-level check, and a document
   re-owned by a contract is findable through a contract-scoped document list, never
   only through the contract's own detail page.*
2. **The day lifecycle** — `work_units`, `work_unit_transitions`, `approvals`, the three
   trigger functions (§ 12), and the unbilled-`work_unit`-to-`InvoiceLine` assembly step
   (§ 9's own call site). *Acceptance: every edge in § 5's state graph is enforced by
   the database (a direct SQL `UPDATE` outside the service layer obeys the same rules
   as the API), a day recorded worked with no approval on a contract that requires one
   lands in `lavorato_senza_approvazione` automatically, and an approved day becomes an
   ordinary `InvoiceLine` through the existing `replace_lines` path with no schema
   change to `Invoice`/`InvoiceLine`.*
3. **Rebillable expenses** — `contract_expenses`, reusing `CostCategory` (§ 7), and the
   trigger that computes `rimborsabile` from a contract's own `politica_spese`.
   *Acceptance: an expense pre-authorised per the owning contract's policy is flagged
   `rimborsabile = true` and one that is not is flagged `false` without the write itself
   being rejected either way, and a reimbursable expense becomes an ordinary
   `InvoiceLine` through the same `replace_lines` path as item 2.*
4. **Ceilings** — the `pigrocrm.core.fiscal.pack` module, `IT_FLAT_RATE_PACK`,
   `FiscalProfile.pack_id`/`pack_version`, and the ceiling evaluation service reading
   cash-basis revenue off `Invoice`. *Acceptance: a space's cumulative paid revenue for
   the calendar year, read against the pack in force, reports the correct alert level at
   each of the two thresholds' own ratios, with no jurisdiction-specific literal outside
   the pack module itself.*
5. **The rivalsa election and its invoice line** — `contracts.applies_social_charge`,
   the invoice-line-construction step from § 9. *Acceptance: a contract-linked invoice
   for a contract with the election on carries an additional `InvoiceLine` at 4% of the
   fee subtotal, `natura = 'N2.2'`, `aliquota_iva = 0`, passing FatturaPA export
   unchanged; a contract without the election produces no such line.*
6. **Contract and day intake** — `proposals`, the accept/reject paths for both target
   types, and the three-door document-to-proposal step (§ 10). *Acceptance: a `documents`
   row already archived by `import_drive_file`, an upload, or a mail import produces a
   reviewable `proposals` row with a verbatim excerpt, and accepting a `'contratto'`
   proposal creates a `contracts` row (plus its first `rate_cards` row) with the
   originating document re-owned by it (§ 11); accepting a `'giornata'` proposal creates
   an `approval` and a `work_unit` at `'approvato'` in one transaction.*

Depends on nothing outside this project except REB-345's session/cookie model if a
future increment ever exposes contract/rate-card data across more than one space owner
— not a concern for any of the six items above, all of which are single-space by
construction (§ 13).
