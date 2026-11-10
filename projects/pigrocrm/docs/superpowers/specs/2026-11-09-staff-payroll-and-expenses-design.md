# Staff, payroll estimates and expenses

Status: approved by Lorenzo on 2026-11-09. Card: REB-965. Project: «Plan what staff and
directors cost and when» (P-REB-86). Frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` (the «frame»), § A2, A3,
A4, A5, A6, A8 and C7. Writes due items (C4,
`2026-11-09-due-items-bank-and-cash-schedule-design.md`); its withholdings and contributions
are lines of an F24 the tax view shows (C6, `2026-11-09-taxes-and-contributions-design.md`);
its expense notes become purchases (C3). Payslips and tax documents arrive through the inbox
(C2).

## Why

Lorenzo, 2026-11-09, on what a company needs beyond profit taxes: the taxes and contributions
for employees and directors «in base ai compensi impostati per ognuno, con anche ripercussioni
sul flusso di cassa», the TFM, travel and mileage reimbursements, and «spese in capo
all'azienda dei dipendenti (pranzi, spostamenti in taxi, tutte le spese ammissibili)».
PigroCRM has none of it: no employee, director, payroll, INAIL, TFR, TFM or expense note
anywhere in `projects/pigrocrm`. The only labour cost is a user's `default_hourly_cost`, used
to value hours (`timetracking/rates.py`).

## Decisions taken (Lorenzo, 2026-11-09)

1. PigroCRM estimates and plans; the payroll consultant computes. Payslips and paid F24s,
   uploaded, replace the estimate (frame C7, «the payroll consultant computes»).
2. Everything here that moves cash appears in the cash schedule; accruals appear on the P&L.

## 1. Staff and directors

A **staff member** belongs to a legal entity and is one of:

- **employee**: the collective agreement's name and level, gross annual pay (RAL), the number
  of monthly payments (13 or 14), part-time share, start and end, working hours per month (used
  by § 5), and the employer's INPS and INAIL rates typed per staff member (the sector and risk
  class are taxpayer facts, «to verify» by the payroll consultant);
- **director** («amministratore»): the annual fee, how it is paid (monthly or otherwise),
  whether it accrues a TFM and how much a year, and the INPS «gestione separata» split between
  company and director (to verify). A sole director is a director with the same rules;
- linked, optionally, to a PigroCRM user (so their expense notes are theirs, and their labour
  cost can feed time tracking, § 5). The pay day is set per legal entity, with an override per
  staff member.

**Which entities may have staff.** `it-standard` and `it-company`, and `it-flat-rate`: a
flat-rate taxpayer is not a withholding agent except on employees (L. 190/2014, art. 1 c. 69,
verified in frame A5), so it withholds on employees only. Never `non-it`, which has no Italian
payroll rules. Directors exist only on `it-company`.

## 2. What they cost, and when it leaves

For each month, an **estimate** per staff member, from the pack's coefficients and the typed
inputs of § 1 (all «to verify», data in the fiscal pack as C6 § 1 keeps it). No payroll
withholding tables are built. The net pay and the IRPEF withholding are estimated only from the
ratios of the last accepted payslip for that person (net to gross, withholding to gross). A
staff member with no accepted payslip yet has neither a net pay estimate nor an IRPEF
withholding line: the due item to the person and the F24 withholding line both wait, so no
partial F24 forecast appears.

| Part | Leaves | Due item |
|---|---|---|
| net pay | on the pay day (for example the 27th, or the 10th of the next month) | out, to the person |
| withholdings and contributions (IRPEF, employee and employer INPS) | on the 16th of the next month, by F24 (to verify, frame A5) | out, payee the F24, one line kind per charge (IRPEF, INPS, INAIL), grouped in the month's F24 (C6 § 2) |
| INAIL | once a year with the yearly settlement (to verify, frame A5) | out, an INAIL line of an F24 |
| TFR | accrued every month (not cash), paid at the end of employment or to a pension fund (to verify, frame A5) | an accrual on the P&L; out when it is paid |
| the 13th and 14th payments | in their months (December; June or July by agreement; to verify, frame A5) | out |
| a director's fee and «gestione separata» | as the fee is paid, and its F24 on the 16th; the fee carries IRPEF withholding and the «gestione separata» split as pack data, both to verify (frame A5) | out |
| TFM | accrued every year; paid at the end of the mandate. Deductibility needs conditions, among them a written deed of earlier date (to verify, frame A5) | an accrual; out when paid |

The estimate gives the **cost** (on the P&L) and the **cash** (due items) separately, since
they fall in different months.

**As built (REB-1009).** `GET /api/staff/{id}/hourly-cost` answers the proposal and writes
nothing; `POST` on the same path adopts the figure the person was shown. The hour is a month's
cost (`PayrollService.reference_monthly_cost`: the estimate's accrual, never a payslip's gross, which prints one payment without the 13th and 14th and would move the hour with whether one was typed)
over the member's `hours_per_month`, rounded to the cent. The month is the current one, moved to
the nearest month served whole, since a part month's cost is prorated and would understate the
hour. It needs a linked user and the hours; a missing one is a 422 that names it. Adoption is
refused (409) when the cost no longer gives the figure sent, so what is written is what was seen,
and writes through `TimeEntryService.update_user_rates` (timeline entry included); hours already
logged keep their cost. Both are `PERSON_ONLY_ACTIONS` (`propose_staff_hourly_cost`,
`adopt_staff_hourly_cost`): no token reads or adopts it, and there is no tool.

**On the P&L.** The period P&L (`analytics/pnl.py:171-176` values labour from
`time_entries.applied_cost`) gains its own personnel line from payroll: accepted payslips, or
the estimate before them. For a user linked to a staff member, the period P&L excludes that
user's time entries' `applied_cost`, so labour is not counted twice. The deal P&L keeps valuing
hours at the hourly cost, as an allocation to the deal.

**As built (REB-1008).**

- The estimate is `staff/estimate.py`, pure; `staff/payroll.py` reads and writes with it.
  A month is the month the work belongs to. Cost accrues gross pay over twelve months plus the
  employer's INPS and INAIL at the typed rates (13th and 14th accrue every month); cash has net
  pay on the pay day, the 13th and 14th in their typed months, withholding on the 16th of the
  month after the payment, INPS on the 16th of the month after the month worked, INAIL's yearly
  premium on 16 February. The dates are the pack's `PayrollLaw` (frame A5, verified 2026-11-10).
- What no source states for everyone is **typed per staff member**, never defaulted: the employee's
  INPS share, and the months of the 13th and 14th payments. A part with a missing input is not
  estimated and `not_estimated` names it. The F24 lines (withholding, INPS, INAIL) come together
  or not at all: they need the pay day, the pack's dates, the ratios and the three typed rates
  (the employer's and the employee's INPS share and INAIL's), since INPS is paid as one line and
  INAIL's premium in the same F24. The 13th and 14th leave in their own typed month, whatever
  the pay day's offset says of the regular payment.
- A director has a cost from the fee and, from the ratios, a net fee on the pay day, and **no F24
  line**: what a fee is withheld at and the INPS «gestione separata» are «to verify» (frame A5,
  REB-975), so the estimate says `fee_withholding` and `gestione_separata`, not a number.
- The **accepted payslip** is a row of `staff_payslips` (gross, net, withholding of one month),
  typed by a person for now (`record_staff_payslip`, person-only); the inbox's proposal will
  write the same row. Its month stops being an estimate for what it prints: the net pay and the
  withholding are `expected` due items with its figures, the cost is its gross, and the ratios of
  the latest payslip drive every other month. The INPS line and INAIL's premium are not on a
  payslip and stay forecasts.
- Forecast due items are written on every change of the inputs, the pay day or a payslip, from
  two months back to twelve ahead, as `forecast` out items of the `payroll` source. An unchanged
  one is left alone, a changed one is dropped and rewritten, and one whose date passed is
  dropped: a forecast is never settled, a document replaces it.
  The daily automations pass rolls the window for every legal entity with staff, each in its own
  savepoint; a failure is reported on stderr and makes the command exit 1. A recorded payslip
  cannot be corrected yet: a correction is a later card.
- The period P&L's `personnel` is the sum of those costs over the window (by days of each month),
  `null` for a reader who may not read pay and with the module off; where it is read, the hours
  of a user linked to a staff member of the same legal entity leave the deals' labour. Everyone
  else reads payroll cash as one «Stipendi» line per date (`list_payroll_totals`), with no person.

**Replaced by the real thing.** The payroll consultant's payslips («cedolini») and the month's
F24 arrive through the inbox (C2) as PDFs, under the inbox kinds `payslip` and `tax_document`;
the model or the user's agent reads their figures into a proposal a person accepts (frame
§ A4). A **payslip**, per person, fixes that person's month: once accepted, the estimate for
that person is replaced by the real figures and the due items become `expected` with the real
amounts. The consultant's **F24** is a C6 tax document: its lines fix the amounts of the payroll
due items they cover and settle them through C6's F24 grouping. F24 lines that are not payroll
belong to C3 or C6.

**Where a payslip file lives.** A payslip file is a `Document` owned by the staff member (the
documents' owner set gains staff, after C3's organisations). PigroCRM's own model reads payslips
only when the space switched the model on and an admin also enabled payslip reading, named on
the inbox's privacy page (C2). Otherwise the user's agent with `full_access` reads them, or a
person types the figures.

## 3. Expense notes, travel and mileage

- **An expense note** belongs to a staff member. It is a receipt (a photo from the phone's «+»,
  a PDF, a forwarded mail) with its date, amount, category and the staff member who paid. An
  agent creates a note by referencing an inbox item uploaded over REST (bytes never travel over
  MCP).
- **Who may submit and read.** No new role is built. A note is visible to its staff member when
  linked to a user, and to admins whose scope covers the legal entity. A member with the
  collaborator role may submit their own notes (a member with the readonly role cannot, since
  readonly cannot write). An admin enters notes for staff who are not users. An admin cannot
  approve their own note: another admin approves, or, in a space with one admin, the note is
  flagged as self-approved in the export (C8).
- **Categories** carry two deductibility values, both «to verify» per category (meals, taxis,
  hotels, representation): the VAT share (C3) and the direct-tax share (here).
- **Travel**: at cost (each expense documented), as a flat daily allowance, or mixed; the
  allowance's exempt thresholds in Italy and abroad, and the traceable-payment rule for
  reimbursements, are «to verify» (frame A5) and pack data.
- **Mileage**: kilometres times a rate per vehicle; the rate comes from the ACI tables when
  their source is verified (frame A5), typed by a person until then.
- **Approval**: an admin approves or rejects. An approved note becomes a C3 purchase of kind
  `expense_note` with its staff member, so cost, VAT, due item and export have one owner of the
  figure. Reimbursed in a payslip (the payroll consultant is told through the «variazioni
  paghe» export, C8), its due item is settled by that payslip and no second due item exists.
  Reimbursed by transfer, it is an ordinary outgoing due item to the person.
- An expense paid directly by the company (a company card) is a purchase (C3), not a note.
- Names avoid the existing `contract_expenses`: the table is `expense_notes`, the tools
  `submit_expense_note` and `approve_expense_note`.

## 4. Privacy and permissions

Pay is the most personal data the product would hold. «A member with the collaborator role»
below means the permission role, not the freelancers of frame C5.

| Action | Who |
|---|---|
| read staff, pay, payroll estimates and payslips | admins whose scope covers the legal entity; agent with `full_access`; never a member with the collaborator or readonly role |
| payroll-source due items and their matched bank movements | the same admins; other roles see one aggregate «Stipendi» line per date, with no person, in the schedule and the Home |
| create or change a staff member and their pay | admin person |
| accept a proposed payslip, F24 or expense | an admin person (frame § A4); accepting stays a person's act |
| submit an expense note | the staff member (a user with the collaborator role), or an admin for them; agent allowed for the user's own notes |
| approve, reject, reimburse | admin, not on their own note; agent with `full_access` |
| close a payroll month | admin; agent with `full_access` |

The tables carry `legal_entity_id` and are under the same row security as every money table
(DECISIONS 2026-10-03), which filters by legal entity only; the finer rules above are checks in
the service. Payroll text and payslips are in `FREE_TEXT_TOOLS` where an agent reads them.

**Amendments to sibling designs.**

- C4 § 5 (who reads due items, the schedule and movements): due items of the payroll source and
  their matched movements are readable only by admins whose scope covers the legal entity (and
  agents with `full_access`); other roles get the aggregate «Stipendi» line. C4's reads of due
  items are filtered by source kind to do this.
- Proposals (`proposals/service.py`): `get` and `list` check no role today and `accept` and
  `reject` need only `require_person` and `require_write` (admin or collaborator). Proposals gain
  per-type visibility: `payslip`, `f24` and `expense` proposals are listed, read and accepted
  only by admins; the existing contract and work-unit types are unchanged.
- Inbox (C2): the kinds `payslip` and `tax_document` are visible only to admins after
  classification, not under plain entity scope.

### Tools

Per frame A8. Every write below is an admin act unless it says otherwise; agents with
`full_access` may call the ones marked so, and the rest are REST only.

| Tool | Who | `full_access` |
|---|---|---|
| `create_staff_member`, `update_staff_member` | admin | yes |
| `get_staff_member`, `list_staff`, `get_payroll_month` (read pay) | admin | yes |
| `accept_proposal` of a payslip or F24 | admin person only | no, a person's act |
| `submit_expense_note` | the staff member, an admin for them | no; an agent may submit for the user's own notes, from an inbox item |
| `approve_expense_note`, `reject_expense_note`, `reimburse_expense_note` | admin, not on their own note | yes |
| `close_payroll_month` | admin | yes |
| hourly cost proposal (§ 5) | admin, REST button | no; `update_user_rates` stays admin-only and MCP-forbidden |

Closing a payroll month locks that month's accepted payslips and estimates. The service methods
of the `full_access` tools go in `_FORBIDDEN` of `test_mcp_surface_coverage.py` and
`FORBIDDEN_SERVICE_CALLS` of `test_mcp_invoice_ban.py`, as the existing privileged tools do.

## 5. Labour cost for time tracking

A staff member's monthly cost divided by their working hours per month (a field on the staff
member, § 1) gives an hourly cost that may be adopted as the linked user's
`default_hourly_cost`, which the rate resolver already freezes on each time entry
(`timetracking/rates.py`). `default_hourly_cost` is per user, not per legal entity. The proposal
is a button on the staff page that an admin presses over REST; it is not the proposals module.
It is never written by itself, since it changes every future entry's value, and
`update_user_rates` stays admin-only and MCP-forbidden. The period P&L already excludes these
users' `applied_cost` (§ 2).

## 6. Module and surfaces

The module `staff` (frame § A2; default on for a company that said so in the onboarding,
§ B3). Dependencies: § 5 needs `time`; expense notes become purchases, so the module needs
`purchases`; due items are core, so `cash` is not a dependency. «Persone dell'azienda» under
«Amministrazione»: staff, their monthly cost and cash, the month's payroll state (estimated,
received, accepted, closed); «Note spese» for the staff member and the approver, on the phone
first. New surfaces: brief and mockups before markup (frame § A7).

## 7. What is tested

A month's estimate split into net pay, F24 and accruals with their dates; the 13th and 14th
in their months; a staff member with no accepted payslip having neither a net pay nor an IRPEF
withholding line, so no partial F24; a payslip proposal accepted and replacing the month; the
consultant's F24 fixing payroll due items through C6's grouping; an expense note from an inbox
item, approved, becoming a purchase, and reimbursed by transfer or settled by a payslip with no
second due item; an admin refused approval of their own note; mileage at a typed rate; a member
with the collaborator or readonly role refused every pay read, and seeing only the aggregate
«Stipendi» line; a collaborator refused to list or accept a payslip proposal; the period P&L
counting labour once for a linked user; staff refused on `non-it`; a token without `full_access`
refused; the hourly cost proposed and not written; a closed payroll month refusing changes.

## 8. Not in this design

Computing payroll; sending anything to the payroll consultant beyond the «variazioni paghe»
export (C8); company cars and fringe benefits, meal vouchers and welfare (named by Lorenzo as
«forse anche altro», each a later design); pension funds beyond paying TFR to one.

## Cards

Filed once the design is approved: staff and directors; the payroll estimate and its due
items; payslips and F24s from the inbox, with the amendments to proposals and the inbox;
TFR and TFM accruals; expense notes with approval and reimbursement; travel and mileage; the
hourly cost proposal; the brief for «Persone dell'azienda» and «Note spese»; those pages.
