# Collaborators in the CRM

Status: approved by Lorenzo on 2026-11-09; § 7 amended on 2026-11-13 (REB-1104), a one-off
migration in place of the import route. Card: REB-963. Project:
«Run the relationship with freelancers from the CRM» (P-REB-84). Frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` (the «frame»), § A2, A3,
A5, A8 and C5. Builds on the registry with roles of C3
(`2026-11-09-suppliers-purchases-and-iva-design.md`) and the due items of C4
(`2026-11-09-due-items-bank-and-cash-schedule-design.md`). Hub records it changes:
`projects/hub/docs/superpowers/specs/2026-09-23-matches-and-contract-signing-design.md`,
`2026-09-25-hours-report-per-match-design.md`, `2026-09-25-simpler-matches-and-mcp-design.md`,
`2026-10-12-prestazione-occasionale-design.md`, and the `docs/design/DECISIONS.md` row of
2026-10-30 that kept the supply side in the hub.

## Why

Lorenzo, 2026-11-09: «bisogna aprire l'hub per tutta la gestione del match e far firmare i
documenti, e poi in realtà anche per controllare che un freelance non sia già occupato su un
progetto prima di contattarlo per un altro». rebase's commercial relationship is in its CRM
space; the relationship with the freelancer it places is in the hub: the match
(`projects/hub/packages/core/src/rebase_core/models/matches.py:79`), the framework agreement
and the letter (`ContractDocument`, `:153`), their signature through Documenso, the tax data
(`FreelancerFiscal`, `:55`) and the hours report, read live from the freelancer's own CRM
space through the engagements door (`pigrocrm_api/routers/engagements.py`). Nothing tells
rebase when to pay a freelancer, and availability is reconstructed by hand.

## Decisions taken (Lorenzo, 2026-11-09)

1. **Only the relationship moves**: match, letters, signature, hours report, availability,
   payments. Talent search, the talent cloud, team requests, campaigns and referrals stay in
   the hub admin.
2. **The payment rule is per freelancer contract**: a fixed term (for example 30 days end of
   month from the invoice), or paid when the client pays.
3. **Every feature is over MCP with the user's permissions, and the MCP tools move with the
   feature**: the hub's are removed in the same release, with no alias (frame § A8).

## 1. What the CRM holds

The module `collaborators` (frame § A2; default on for a company that said so in the
onboarding, and written on for rebase's own space at the release, § 7).

### The freelancer

A freelancer is an organisation with the role `freelancer` (C3 § 1), with the person who works
linked to it as today's people are.

- **Identity.** The organisation stores the hub freelancer id in a unique column. The door's
  advisory lock (today `pg_advisory_lock(hashtext(:email))`, `engagements/service.py:74-76`)
  moves from the email to that id, so a freelancer who changes email is still one
  organisation and one lock. The freelancer's own tenant space stays linked through the
  registry row (`rebase_engagements`), as today. A freelancer who is already an organisation
  (a customer or a supplier) gains the role `freelancer` instead of a second row: C3's unique
  index on `(country, normalised VAT number)` finds it.
- **Fiscal kind.** The kind (C3: `professional`, `flat_rate`, `occasional`) decides the letter
  type (ordinary or occasional, `2026-10-12` design), the withholding and the net to pay. It
  does not simply replace the hub's `FreelancerFiscal`: that table records only whether a VAT
  number is present, so it cannot tell `professional` from `flat_rate`. The migration (§ 7)
  therefore leaves the kind unset and an admin classifies each freelancer; a `flat_rate`
  freelancer needs the written declaration kept as a document, since the declaration is what
  excuses the withholding (frame A5, L. 190/2014 art. 1 c. 67).

### The engagement

An engagement is the CRM's name for the hub's match. It holds:

- the freelancer, and the **client contract or deal** it serves in the space (optional and
  flagged when the match predates the 2026-10-30 link, § 7);
- the role, start and end;
- **`days_per_week`**, a structured value from 0.5 to 5 in steps of 0.5 (the range the hub's
  team proposals already use). It is required for a new engagement. The hub's match has no
  such field, only `expected_days` and the free-text `impegno`
  (`models/matches.py:79-152`, `contract_schemas.py:81`), so every migrated engagement lacks
  it until an admin fills it (§ 3);
- the fee with its unit (day, hour, flat) and the expected days; for an hourly letter the
  expected days are the hours at the hub's 8 hours per day (`engagement_report.py`,
  `HOURS_PER_DAY`), the only place hours become days;
- the letter type, the **payment rule** with its fields `payment_rule` and `collection_days`
  (§ 4), and the state (`draft`, `awaiting_signature`, `active`, `concluded`, `cancelled`, the
  hub's own names).
  An `active` engagement can still be cancelled while its start date has not come (a signed
  letter for work that never began); from the first day it is closed, never cancelled.

**Identity across the move.** A migrated engagement keeps the hub match UUID as its id, and a
migrated document keeps its hub document id. The match id is already the registry's
`rebase_engagements.match_id` and the `rebase:match=<id>` marker on the freelancer-space deal,
so both keep working; the document id is already Documenso's `externalId`
(`signing.py:295`). A new engagement takes an id minted by the hub at «Crea match» (§ 5). A
unique `hub_match_id` column records which hub match an engagement came from, since rebase's
space carries no marker for engagements (leads use `rebase:request=`).

**A notice of termination belongs to the framework agreement, not to the engagement**: it is
valid only on a framework agreement (`ck_contract_documents_notice_for_framework`,
`models/matches.py:230`), and an engagement ends by being closed or cancelled.

### The documents

The framework agreement belongs to the freelancer and hangs on no engagement; the letter
belongs to the engagement and carries its number `YYYY-NNN`. Both are `Document` rows owned by
the freelancer organisation (the owner set of C3 § 2, «Filing from the inbox», which becomes
organisation, deal or contract).

- **Two document types.** The template engine (`documents/templating.py`) gains
  `framework_agreement` and `engagement_letter`, added to `DocumentType`
  (`documents/schemas.py:19-28`) and to `TEMPLATE_TYPE` (`labels.py:82`). It is bound to a
  customer scope today; it gains a scope for engagements and freelancers.
- **Keys.** The hub's texts use flat hyphenated keys (`{{professionista-nome}}`); the CRM
  engine uses dotted ones (`{{cliente.*}}`). A one-time key map converts the four texts
  (`projects/hub/packages/core/src/rebase_core/contracts/texts/`, plus `rebase.json`) when they
  are seeded in rebase's space, and the map is kept beside the seeded templates.
- **Drafts.** The texts' front matter carries `version` and `status`; a text whose status is
  `draft` is refused for sending, as the hub refuses it today.
- **Who edits.** Templates of these two types are editable only by an admin person. The legal
  fields stay computed by the moved `checked()` (`contracts/fields.py:131`) and are never typed
  into a template.
- **Signature blanks.** The hub locates the `{{firma-*}}` blanks with Typst for Documenso
  (`contracts/render.py:233`, `signature_blanks`). That locator moves with the renderer: the
  CRM already renders through pandoc then Typst (`render/pdf.py`), the same stack.
- **The signer's side.** `SIGNER_FIELDS` (`contracts/fields.py:282-303`) lists the `rebase-*`
  fields that `REBASE_SIGNER_JSON` may fill and nothing else: a freelancer's or a client's data
  never come from a server setting. A multi-space CRM has no such setting; the `rebase-*`
  values come from the **legal entity of the space** (C3 legal entities already carry them).
- **The field rules move into the CRM's core**: the fee above zero, the payment term within
  the legal limit (60 days, 30 when counted from month end, law 81/2017 art. 3, as
  `contracts/fields.py:35-40` writes it today, «to verify» against the primary source, frame
  A5), the dates, and the two new fields of § 4, which `checked()` validates the same way.
- **What never reaches a freelancer's document**: the client's price and the company's daily
  budget (matches design § 1h); the lead's deal value lines never feed a letter.

## 2. Signature

Documenso moves with the letters: its client, the webhook route and the recovery sweep
(`projects/hub/.../signing.py`, `routers/documenso.py`, `rebase contracts-sweep`) become
PigroCRM's. The rules stay as they are: only the freelancer signs; the framework agreement
goes first and the letter is released when it is signed; a draft is never sent; a cancelled
document is cancelled on Documenso too.

- **One Documenso per installation.** The compose stack (`docker-compose.documenso.yml`) and
  its backups move from the hub project to the CRM's. It is configured by the installation's
  environment (URL, token, webhook secret), as the hub does with its `documenso_*` settings
  (`config.py:100-104`); configuring it is deploy work, not an admin screen. Without it, a
  letter is downloaded and signed by hand and its signed PDF uploaded. The preview keeps its
  own Documenso instance.
- **Routing the webhook.** The hub sets an envelope's `externalId` to the document id
  (`signing.py:295`), which names no space. A new envelope's `externalId` is
  `<tenant id>:<document id>`, so the root webhook reads the space from it and finds the
  document there by its envelope id. A hub-sent envelope carries the bare document
  id, which resolves in the space `PIGROCRM_LEADS_SPACE` names, the only space that held hub
  documents. Whether Documenso accepts that `externalId` format is checked by the first card.
- **The sweep.** The hub's `rebase contracts-sweep` does two things: it recovers signatures
  and it files company requests in the CRM (`crm_sweep`, `cli/signing.py:82-107`). It runs
  every ten minutes from the hub's `sweep` compose service (`docker-compose.yml:257-271`).
  The signature half moves to the CRM. The hub **keeps a sweep for `crm_link`**, renamed for
  what it does, so requests keep reaching the CRM, and after the cutover it never touches a
  signature. The CRM does not carry over the early return of the hub's recovery round
  (`signing_recovery.py:301-302`, `if self.service.engagements is None: return ...`): its
  round always recovers signatures.
- **Checks.** `contracts-check` (can this machine typeset a contract) and `documenso-check`
  (does the environment reach Documenso) move with the renderer and with Documenso.
- **The reserved area's promise.** Framework 17.1 says documents are signed in the reserved
  area, which keeps a copy both parties can download. The hub's member area shows the signing
  link and serves the signed PDF (`members.py:232-246`); after the move it does so through the
  door (§ 5), so the contract text stays true.

## 3. Hours, days and availability

- **What the door writes in a freelancer's own space.** When an engagement turns active, the
  freelancer's space gets rebase as a customer (as today) and a **contract** with a rate card
  mapped from the letter: a day or hour unit becomes `daily` or `hourly`, a flat fee becomes
  `one_off` with its dates (`RATE_CARD_TYPES`, `contracts/models.py:50`). A new space gets
  `space_profile = solo` and `module_contracts = on`, and `module_deals` on only where the
  space already holds the old deal (frame B7). The freelancer records days or hourly figures
  as work units (`work_units`), which the `solo` profile shows (frame A2, defaults table).
  Spaces opened before this keep their deal and their hours, and the report reads both. The
  registry row gains `contract_id` beside `deal_id`.
- **The report** stays live, never copied: rebase's engagement reads the freelancer's work
  units and hours for a period from the freelancer's space. Inside one installation this is
  the engagements service called in-process from the space that `PIGROCRM_LEADS_SPACE` names,
  with the hub's idempotency and windows (`engagement_report.report_windows`). The lock order
  (freelancer, then engagement, then documents, `match_rows.py:7-9`) governs writes; the
  report takes no lock. A company that is not that space has no cross-space read: its
  freelancers' days arrive as approvals, invoices or uploads.
- **Availability**: from every active engagement (dates and `days_per_week`), the CRM computes
  a freelancer's committed days per week, by week. It is shown on the freelancer's page, in the
  engagement form (a warning when a new engagement overlaps), and answered to the hub through
  the door (`GET` by the hub's freelancer id), so the hub's talent search can show it next to
  each talent without owning the data. An engagement without `days_per_week` makes the answer
  «sconosciuta» for that week, never «libera». The hub's match pages are not removed until an
  admin has filled every missing value (§ 7).

## 4. Payments to freelancers

### What makes a day payable

A day is payable when it is in an **approved «Rendiconto»**. The framework agreement says how
(6.1 to 6.2): the freelancer sends the month's report by the third working day; rebase puts it
to the client and tells the freelancer the outcome within seven working days of receiving it;
with no reasoned objection in that time the report counts as approved, and a contested part is
held while the rest is paid.

In the CRM the report is an **approval request on the engagement**. Its approval is a person's
act in rebase's space (the client's agreement relayed, or its objection recorded), or the
tacit date passing; it is not an MCP tool and never an agent's act (frame A4). The freelancer's
own `worked` work units alone make nothing payable. Hours become days only where the letter's
unit says so: an hourly letter pays approved hours.

**As built (REB-1002).** The request is the table `engagement_monthly_reports` (one row per
engagement and month; `MonthlyReport` in code, «Rendiconto» to a person). It stores the day
rebase received the report, the tacit date, who approved it and the `held_dates` with their
reason, and copies no day. A person records the receipt (`POST
/api/engagements/{id}/monthly-reports`) and approves (`POST .../{month}/approval`, with
optional `held_dates` and the `objection` that justifies them); both are `PERSON_ONLY_ACTIONS`
and no MCP tool exists for either. The tacit date is the seventh working day after receipt,
counted without Saturdays, Sundays and the national holidays of `fiscal.calendar` (the
agreement does not define «giorno lavorativo»: that calendar is an assumption for the legal
review of the contracts). A report counts as approved the day after its tacit date with no
decision, without any row being written; a day cannot be held after that. The live report is
`GET /api/engagements/{id}/report` and the MCP tool `get_engagement_report`: it asks the
engagements door in-process, in windows of at most 800 days, for the work units of the
freelancer's contract (and the time entries of an old deal), and marks a day `payable` when its
ledger state is work done (`approved`, `worked`, `worked_without_approval`, `invoiced`, `paid`),
it falls inside the engagement's dates, its month counts as approved and it is not held. The
quantity is hours for an hourly letter and days otherwise (an old entry's hours at 8 a day);
a flat fee has no amount to read from days. It works only from the space `PIGROCRM_LEADS_SPACE`
names.

### Due items

Each engagement writes **due items out** (C4) for the freelancer, in core, so they exist
whether or not the `cash` module is on:

- **forecast** (C4 source kind `engagement`, keyed by engagement and period) at each billing
  point the letter sets: a month's days at month end, or the flat fee's dates, the amount from
  the fee and the days;
- **expected** when the freelancer's invoice or receipt arrives through the inbox (C2) and is
  registered as a purchase (C3), with its due date from the rule below. **The purchase
  replaces the forecast, never adds to it** (C4 § 1): the purchase is linked to the engagement
  by the freelancer organisation and the period (proposed to a person when more than one
  engagement fits), and that link drops the forecast in the same transaction, so the cash
  schedule never shows the money twice.

### The payment rule

The letter carries `payment_rule` (`fixed_term` or `on_collection`) and, for the second,
`collection_days`.

- `fixed_term`: days from the invoice (or receipt) date, optionally to month end, through
  `invoices/due_date.py::due_date_from_terms`.
- `on_collection`: the freelancer is paid a number of days after the client has settled, never
  later than the legal backstop:

  `due = min( max( client settlement + collection_days, document date + fixed term ), date the
  invoice or receipt is received + 60 days )`

  The 60-day backstop is law 81/2017 art. 3 as framework 7.1 prints it («in ogni caso entro 60
  giorni dal ricevimento della fattura»): **«to verify»** against the primary source and added
  to the frame's A5 register. No letter can pass it, so the moved `checked()` validates the
  two new fields against it: `collection_days` from 1 to 60, a fixed term within the limit,
  and nothing a letter writes can move the date past the backstop. C4's due rule
  `after_settlement` gives the `max` half; the `min` half is a **`not_after` date on that
  rule** (the one addition C5 asks of C4 § 1.3), so the projected date of an unsettled
  dependent is capped too.
- **Pairing.** The engagement names the client contract (or deal) it serves. The freelancer's
  due item for a period `depends_on` the client's due items for the same contract and period.
  When one client invoice covers several engagements, each freelancer item depends on that
  invoice's item. A partly paid client item counts as paid only when it is settled in full. With
  no client invoice yet, the item waits, and the backstop still dates it.
- **The clause.** It exists since 2026-11-09 (REB-979, Lorenzo's call, recorded in
  `docs/design/DECISIONS.md`), and its wording awaits the overall legal review of the
  contracts. In the hub it is the optional letter field `collection_days` (`giorni-incasso`,
  1 to 60): set, the rule is `on_collection`, unset, `fixed_term`. The letter then prints
  «N giorni dopo che il Cliente ha saldato rebase per lo stesso lavoro, e in ogni caso entro
  60 giorni dal ricevimento della fattura» (the receipt, on the occasional letter) and a
  paragraph «Pagamento a incasso» (`engagement-letter.md` and
  `engagement-letter-occasional.md`). The framework agreements' 7.2, both at version 1.1, say
  a letter may do this with the risk staying rebase's, which keeps the substance of the
  conflict the read was to settle. A letter under an agreement signed before carries the
  parties' consent itself, as an exception to 7.2, so no agreement is re-signed. The printed
  text names no fixed-term floor, so **C5 drops the `max` half** for such a letter (REB-1003):
  an `on_collection` letter stores no fixed term (the table says so), and its item is
  `after_settlement` with `collection_days` as the offset and `not_after` alone, so the formula
  that runs is `min( client settlement + collection_days, date the invoice or receipt is
  received + 60 days )`. The letter text is unchanged.

**What the forecast writes** (REB-1003, `collaborators/forecast.py`). An `active` or `concluded`
engagement writes one forecast out per billing point, keyed by engagement and billing date
(`due_items.forecast_key`, unique among live forecasts). A day or hour letter bills each month at
month end for `days_per_week` times the month's working days over five (an hour is 1/8 of a day),
never past `expected_days` in all; a flat fee bills once, at the end of the month its `ends_on`
falls in. Nothing is forecast without `days_per_week` (a day or hour letter) or an end (a flat
one). An open-ended letter is forecast twelve months ahead, and `pigrocrm reconcile-due-items`
moves that horizon. A billing point whose 60-day backstop has passed is not forecast again.
`fixed_term` is dated by `due_date_from_terms` from the billing date. The billing date stands for
the day the invoice or receipt is received until the document arrives, which is what the
`not_after` backstop counts from.

**Pairing** is the sales invoice of the engagement's deal, or of a contract whose work units are on
its lines, whose accrual period covers the billing period (an invoice with no period, issued up
to 31 days after it, counts): the latest one when several do. Issuing or importing a client
invoice pairs the waiting forecasts of its customer, so an invoice written elsewhere is paired by
the next `reconcile-due-items`. An engagement naming only a client organisation, not a deal or a
contract, is not paired. A later invoice for the same period takes the pairing over, and an engagement linked to
another client forgets the old one's invoice. An invoice with no period belongs to the period
that ended last before it was issued. The forecast is written in the freelancer's legal entity
(else the contract's, the client's, the default): one that differs from the entity that
invoices the client is never paired, and says nothing. A client invoice annulled after pairing
is not re-paired.

### Withholding and net

When the space's legal entity is a withholding agent and the freelancer's kind is
`professional` or `occasional`, a second due item goes to the tax office (C3 § 5, the frame's
A5 register). The net paid is:

**net = total minus withholding minus the INPS «gestione separata» share rebase withholds on
occasional work**

- the withholding is 20% of the A5 base: the fee plus the INPS rivalsa and refunded expenses,
  VAT and the professional fund contribution excluded (C3 stores it as rate, base and amount);
- the «gestione separata» share is withheld from an occasional fee above the yearly
  threshold (framework 5.7, which prints the 5,000 euro threshold and article 44 comma 2 of
  decree-law 269/2003): **«to verify»**, and not computed before it is;
- the stamp duty on an occasional receipt is in clause 6.3 of the occasional framework («to
  verify» before it is computed).

A freelancer's payment that would fall before the client's money arrives on a fixed term is
exactly what the cash schedule's projected balance shows (C4 § 4).

## 5. What the hub keeps, and how the two talk

The hub keeps: signup, the freelancer card and CV, the anonymous CV, the member area, talent
search, the talent cloud, team requests, campaigns and referrals. Neither product opens the
other's database (2026-09-09). Every call is idempotent and retried.

### Creating an engagement

From a company request, «Crea match» in the hub mints the engagement's id, stores its read
model row (below) with the company's daily budget snapshot, and opens the CRM's engagement form
on the request's deal (the request is already filed in the CRM with its deal, `crm_link.py`,
2026-10-30), with the freelancer chosen by their hub id. The CRM finds or creates the
freelancer organisation from the hub's card through the door, by the hub freelancer id, never
by name. The budget stays in the hub: it is what the referral reward needs and what never
reaches a letter.

### The hub's read model

The hub keeps one small table, **`hub_engagements`**: engagement id, hub freelancer id, hub
company id, letter id and number, `signed_at`, mode, fee, expected days, role, start and end,
the company's daily budget snapshot, and the state. Rows in the state `handoff` (opened, no CRM
notice yet) are read by nothing.

It is **upserted by notices from the CRM** on every state change: created, sent, signed,
cancelled, closed, notice served. The CRM writes each notice to an **outbox table** in the same
transaction as the change, and a retry sweep in the CRM's leads space sends it to a hub route
under its own token setting (not the engagements token, which opens the CRM). The bearer travels
only over HTTPS, plain HTTP only to this machine (the `speaks_https` rule of the hub's
`pigro_door.py`). Each notice carries a sequence number and the hub ignores an older one, so a
retry or a late delivery cannot move a state back.

What reads it:

- **The referral reward.** `referral_rewards.document_id` is a non-null foreign key to
  `contract_documents` (`models/referrals.py:98`), written in the signature transaction
  (`record_reward_if_signed`, `referrals.py:336-374`) from the match's fee, budget snapshot,
  expected days and mode. A migration repoints it to `hub_engagements`; the reward is written
  when the `signed` notice lands, in the same transaction as the upsert. The ledger
  (`referral_ledger.py`, `EARNING_MATCH_STATES`, `referrals.py:108`) and `Referrals.tsx` read
  the table instead of `Match`, `ContractDocument` and `Company`; the `cancelled` and `closed`
  notices keep the earning states right.
- **Verification of a referral.** By the first signed letter (REB-658), now read from the
  table (`_mature_first_signed_letter`, `referrals.py:317-334`); verification by the first
  magic-link login is unchanged (DECISIONS 2026-10-05).
- **The card's engagement facts** (`hub_facts.py:187-195`, `card_staleness.py:57`: role,
  months, how it ended) read the table, which replaces `Match` and the `match_closed` trail
  rows there.

### What the hub reads from the CRM through the door

Root-only, with the engagements token:

- **availability** by the hub freelancer id (§ 3);
- **the member's contracts** (`GET /me/contracts` today): the list with states, the signing
  link while a document waits, the previous frameworks and the derived renewal and notice
  dates, and **the signed PDF**, authorised by the hub's session identity mapped to the hub
  freelancer id (`members.py:232-246`);
- **the fiscal identifiers** (tax code, VAT number, PEC) by hub freelancer id, for the
  anonymous CV's redaction list. `anonymous_cv/writer.py:44,341` builds that list from
  `FreelancerFiscal`; without this read the table's removal would shrink the list and a CV
  could carry a tax code;
- **the merge check**: `merge.py:375-386` refuses merging two cards with conflicting frameworks
  or an active engagement with the same company. It asks the CRM through the door, and merging
  two freelancers needs the CRM organisation merge first (C3's merge card).

### What the CRM reads from the hub

- **The referrer's name** for the letters (`{{segnalato-da}}`, `{{azienda-segnalata-da}}`): a
  hub route by hub freelancer id and hub company id, which answers with
  `ReferralService.referrer_name` (`referrals.py:264`) and so only for a verified referral
  (DECISIONS 2026-10-06, REB-664). The CRM calls it at render and at send, stores the printed
  value in the document's data (a letter keeps the value stored at creation), and prints
  «nessuno» when the referral is unverified or the call fails.

## 6. MCP and permissions

The hub tools named by the frame's C5 move to PigroCRM under the `collaborators` module,
renamed for the CRM's word:

| Hub tool | CRM tool |
|---|---|
| `create_match` | `create_engagement` |
| `preview_match` | `preview_engagement` |
| `send_match_for_signature` | `send_engagement_for_signature` |
| `resend_signing_mail` | `resend_signing_mail` |
| `refresh_contract` | `refresh_document_signature` |
| `cancel_match` | `cancel_engagement` |
| `close_match` | `close_engagement` |
| `list_matches`, `get_match` | `list_engagements`, `get_engagement` |
| `get_match_report` | `get_engagement_report` |
| `set_freelancer_tax_data` | `set_freelancer_tax_data` |
| (new) | `get_freelancer_availability` |
| `cancel_contract` (cancels an unsigned framework agreement) | `cancel_framework_agreement` |
| `record_notice` (valid only on a framework agreement) | `record_framework_notice` |

`link_match_to_pigro` is **removed without a replacement**: the engagement lives in the CRM, so
there is nothing to link. In the hub, thirteen tools leave `tools_matches.py`
(`create_match`, `preview_match`, `send_match_for_signature`, `resend_signing_mail`,
`refresh_contract`, `cancel_contract`, `cancel_match`, `close_match`, `record_notice`,
`list_matches`, `get_match`, `get_match_report`, `link_match_to_pigro`) and
`set_freelancer_tax_data` leaves `tools_fiscal.py`, all in the same release.

The hub kept the matches **admin-only** (`deps.admin()`), and so does the CRM: every engagement
read and write is admin. Widening it to every collaborator would expose the freelancer's hours
and invoice numbers (`engagements/service.py:215-298`) and let agents burn letter numbers.

| Action | Person | Agent |
|---|---|---|
| read engagements, the report, availability, letter previews | admin | the admin's token; no `full_access` |
| update a draft engagement | admin | the admin's token; no `full_access` |
| create an engagement | admin | `full_access`: it draws a letter number a cancellation never gives back (`matches.py:272`, `framework.py:187`) |
| send for signature, resend the mail, cancel or close an engagement, cancel a framework agreement, record a notice | admin | `full_access`, behind `mcp_full_access`: each sends or ends a legal document |
| refresh a document's signature | admin | the admin's token; it only reads Documenso's state |
| set a freelancer's tax data | admin | `full_access`: personal data printed on contracts |
| approve a «Rendiconto» | admin person | none: a person's act (frame A4) |
| configure Documenso | deploy work | none: not a screen, not a tool |

The report, preview and letter tools carry text other people wrote (time-entry descriptions,
role, activities), so `get_engagement_report`, `preview_engagement`, `get_engagement`,
`list_engagements`, `create_engagement` and `send_engagement_for_signature` go in
`FREE_TEXT_TOOLS` (`apps/mcp/.../provenance.py:28`). Each new action gets its row in
`AGENT_ALLOWED_ACTIONS` or `FULL_ACCESS_ACTIONS` (`core/agent_actions.py`), its `MIN_ROLE` row
in `permissions.ts` and its line in the surface-coverage test, within the tool budget
(`test_mcp_tool_annotations.py`). A module that is off registers no tools (frame A2).

## 7. Moving what exists

**Neighbour work, and what landed first.** REB-689 (the company contract's general terms and
proposal, and the freelancer NDA) merged in the hub on 2026-11-10, before any of this. The CRM
seeds every text under `contracts/texts/` as it stands then, REB-689's included: the
freelancer NDA becomes a template of the `collaborators` module, the company contract a
template for the customer side.

**No import route** (Lorenzo, 2026-11-13, REB-1104: «non serve avere una funzionalità di
import dall'hub. facciamo noi una migrazione una tantum dei match esistenti dall'hub»). What
exists in the hub is moved once, by us, and nothing in either product is built for it. On
2026-11-13 production's hub held one match, a draft never sent (its letter drew 2026-001, its
framework agreement was generated and not sent), tax data for two freelancers, the 2026
letter counter at 1, no referral reward and no envelope on Documenso; so there is nothing to
freeze, no delta, and no envelope that needs the hub's webhook after the switch.

In this order:

1. **The CRM ships** the module, the door routes of § 5, the outbox and its sweep.
   `pigrocrm ensure-space-defaults` writes `module_collaborators = on` for the space
   `PIGROCRM_LEADS_SPACE` names: it is not a module of today, so frame B2 would otherwise
   read it as off there and the module would be invisible after the hub's tools are gone. The
   hub ships its `hub_engagements` table and its notice and referrer-name routes, and its
   «Crea match» opens the CRM's form.
2. **The one-off migration**, by hand through the CRM's own tools and routes, never by
   writing either database directly. Each freelancer's tax data are set with
   `set_freelancer_tax_data`, and an admin classifies the fiscal kind (`professional` or
   `flat_rate`, with the declaration document for the second). A match that still matters is
   recreated with `create_engagement` and its `hub_match_id`; one that does not is left to the
   hub's backups. A letter number the hub drew for a draft that never reached a freelancer is
   not carried, so the CRM's counter needs no write. The migration is checked against the
   hub's tables before they are dropped, and its result is written on the card.
3. **The hub's release** removes the match side, only once the migration is checked and no
   `days_per_week` is missing:
   - **admin pages**: `Matches`, `MatchReport`, `Contracts`, `CreateMatch`; `Referrals` changes
     to read the read model; `TalentMenu` loses «Match e contratti» and its «Crea match» opens
     the CRM form; `FreelancerDetail` loses its match and fiscal panels; `SidebarNav` loses the
     entries. The member «Contratti» page stays and reads through the door. After the pages go,
     `TeamRequests.tsx` still imports the shared `list-kit`: check that the state pill it shares
     with the match list survives;
   - **routes**: `routers/matches.py` whole (including the fiscal routes `:90-96` and
     `pigro/link` at `:209`) and `routers/documenso.py`, with the hub's webhook deleted on
     Documenso;
   - **tools and text**: the fourteen tools of § 6, the MCP `INSTRUCTIONS` text
     (`server.py:59`), the engagement builder in `tools_common.py`, and the `link_match_to_pigro`
     service in `http.py`;
   - **settings**: `documenso_url`, `documenso_api_token`, `documenso_webhook_secret`
     (`config.py:100-104`) and `signer_json`; `pigro_engagements_token` stays, since `crm_link`
     uses it;
   - **tables and modules**: `matches`, `contract_documents`, `freelancer_fiscal` and
     `contract_letter_counters`, with `matches.py`, `match_*.py`, `contracts/`, `signing*.py`,
     `documenso.py`, `framework.py`, `fiscal.py`, `engagements.py` and `engagement_report.py`;
     the readers that change are named in § 5 (referrals and ledger, the anonymous CV writer,
     the card facts, the merge).
   The Documenso compose stack and its backups move to the CRM's deploy at this step.

## 8. What is tested

An engagement's letter rendered from the space's template with no client price in it; a
payment term over the legal limit refused, and an `on_collection` letter capped by the 60-day
backstop; framework before letter and only the freelancer signing, through a Documenso fake,
with the envelope found from the root webhook through the registry; the door writing a
contract and a rate card per unit in a new freelancer's space and the report reading work units
and old hours; availability with two overlapping engagements and the warning, and «sconosciuta»
for one without `days_per_week`; a fixed-term and an on-collection payment, the second moving
with its client's settlement, with one client invoice covering two engagements; a purchase
replacing the engagement's forecast; withholding only when the payer is an agent, and the net
formula; a day payable only in an approved or tacitly approved «Rendiconto»; the hub's
availability read; the notice outbox with its retry, an out-of-order notice, and the reward
written once on `signed`; the referrer's name «nessuno» when the call fails; every hub match
tool gone and every CRM tool classified as the table says.

## 9. Not in this design

The marketplace tools (decision 1); a cross-space read for a company that is not the leads
space; freelancer self-service of engagements inside their own space beyond recording days;
paying freelancers from PigroCRM (C4 § 8); the wording of the `on_collection` clause (§ 4).

## Rows for `docs/design/DECISIONS.md`, on approval

- Supersedes in part the 2026-10-30 row: the engagement with a freelancer (match, letters,
  signature, hours report, availability, payments) lives in the CRM's `collaborators` module;
  the hub keeps the talent side, a read model of engagements fed by CRM notices, and reads
  availability and contracts through the door.
- The CRM's engagement reads and writes stay admin-only, as the hub's matches were; an agent
  needs `full_access` to create an engagement or to send or end a document.

## Cards

Filed once the design is approved: the `freelancer` role, the hub freelancer id and the fiscal
kind from the hub's tax data; engagements, `days_per_week` and their states; letters from
templates with the moved field rules and the key map; Documenso in the CRM, its webhook and
sweep; the door writing a contract in the freelancer's space; the live report from work units
and the «Rendiconto» approval; availability and its door read; payment rules, due items,
withholding and the net; the hub's read model, the notice outbox and the referral repoint; the
door reads for the member area, the CV redaction and the merge; the hub's «Crea match» on the
CRM; the one-off migration; the hub's removal; the MCP tools moved and classified; the brief
for the engagement pages; those pages.
