# Taxes and contributions

Status: approved by Lorenzo on 2026-11-09. Card: REB-964. Project: «Plan every tax payment
before it falls due» (P-REB-85). Frame:
`2026-11-09-space-profiles-modules-and-the-money-picture-design.md` (the «frame»), § A1, A3,
A4, A5, A6, A8 and C6. Writes due items (C4,
`2026-11-09-due-items-bank-and-cash-schedule-design.md`); reads IVA and withholdings from C3
(`2026-11-09-suppliers-purchases-and-iva-design.md`), freelancers' withholdings from C5 and
payroll from C7.

## Why

Taxes and contributions are the largest outflows of a year and the ones that arrive with the
least warning. Today PigroCRM estimates only the flat-rate regime's substitute tax and INPS
contributions for a year (`analytics/fiscal.py::estimate_income`, gated by
`pack.estimate_income`, `analytics/service.py:541-545`), and checks the revenue ceilings
(`fiscal/ceiling.py`). There is no due date, no advance, no balance, no instalment, and no
estimate at all for the ordinary regime or a company. The 2026-10-26 row of
`docs/design/DECISIONS.md` left the `tax_payment_due` reminder unbuilt because «the fiscal
pack knows no saldo or acconto date». This design gives it those dates.

## Decisions taken (Lorenzo, 2026-11-09)

1. Two profiles: solo (flat-rate or ordinary) and company (srl); other company forms later as
   new packs (frame decision 1).
2. The accountant keeps the books: an estimate is replaced by the accountant's figure, and the
   advances that follow are recomputed from it (frame § A6).
3. Tax instalment plans are in scope (Lorenzo's list of what was missing).

## 1. Law in the pack, circumstances in the profile

**The pack holds law, keyed by tax year.** Each fiscal pack (`fiscal/pack.py`) gains, per tax
year, the taxes it owes with: what each is computed on, its rates or brackets (IRPEF brackets,
IRES and IRAP rates), the advance rules and thresholds, the dates, the instalment interest and
the 0.40% deferral. Each tax year is a pack version, so the June balance of year N-1 and the
historical advances of year N read the rules that governed N-1, even after a regime or pack
change on 1 January. The dates are «salvo proroghe» (Agenzia): a year's dates are pack data a
release updates when a decree moves them, never a branch in a service (frame § A5).

**The profile holds the taxpayer's parameters.** The fiscal profile (`fiscal/models.py`)
carries what depends on the person, not on the law: the profitability coefficient, the
substitute rate (5% on start-up conditions, 15% otherwise, to verify), the INPS category
(«gestione separata», or «artigiani e commercianti» with their fixed quarterly minimum, to
verify) and the ISA flag (it decides 50/50 or 40/60 advances). The legal entity holds its
fiscal year end (default 31 December) and, for IRAP and the surcharges, its region and
municipality. A missing parameter gives no figure, never a guess (the rates stay nullable).

**A reading freezes what it used.** Each tax-year reading (§ 2) stores the
`(pack_id, pack_version)` and the profile parameters it was computed with, as C3 freezes a
purchase's regime. The profile itself stays «not historicised».

Rows not in the frame's A5 register are added to it in this PR, marked «to verify»; a cell
below that is not verified says so.

| Pack | Owes |
|---|---|
| `it-flat-rate` | substitute tax at the substitute rate (5% or 15%, to verify) on revenue times the profitability coefficient; INPS contributions on the base the code computes today, the taxable amount minus the rounded substitute tax (`analytics/fiscal.py`); whether contributions are deducted before the substitute tax is «to verify» |
| `it-standard` (a person, ordinary regime) | IRPEF by brackets with regional and municipal surcharges (to verify), INPS (to verify), IVA from C3, minus the withholdings suffered on its own invoices (C3 § 3) |
| `it-company` (new: an srl) | IRES and IRAP (rates, to verify), IVA from C3, the annual chamber of commerce fee (to verify), the payroll and director rows C7 owns (coefficients, employer INPS, INAIL, TFR; «gestione separata» for directors, with C5 for occasional workers) |
| `non-it` | no figure is computed |

The withholdings a company pays for others are due items C3 writes (C3 § 5); C6 only shows
them. A card verifies the estimate's contribution formula with the accountant before any
advance is built on it.

### The `it-company` pack

It invoices exactly like `it-standard` (RF01, the same regime strategy, `fiscal/regime.py`),
and differs only in what it owes. `ORDINARY.pack_id` is one string today, so `resolve_regime`
maps RF01 to the set {`it-standard`, `it-company`}. The places that change:

- `PackId` and its assert (`fiscal/schemas.py:23-24`) and `_pack_defaults`
  (`fiscal/schemas.py`, the `it-standard` defaults);
- the VAT-rate check at `fiscal/service.py:149`, extended to `it-company`, so a zero default
  rate is still refused;
- `fiscalModel.ts`, `FiscalForm.tsx`, `NewLegalEntityForm.tsx` and the generated
  `api-types.ts` in `apps/web`.

The onboarding's srl answer writes `it-company` (frame § B3, which mapped srl to
`it-standard` until this pack exists). Existing srl entities on `it-standard` move to
`it-company` from the next 1 January, never mid-year. The year register groups by regime code
(`analytics/year_overview.py:94` today groups by `(regime_code, frozen_pack_id)`), so a year
that spans the switch reads as one RF01 row. A migration card covers the move and a test of
the register for such a year.

## 2. From estimate to figure to due items

For each legal entity, tax and year, PigroCRM holds one of three readings. They rank by stage,
never by time: **paid** over **declared** over **estimate**. A newer estimate never replaces a
declared figure.

1. **Estimate**: computed from the year's own figures on one basis per regime, used by every
   path. Flat-rate: collected revenue minus the INPS surcharge lines (cash principle, to
   verify). Ordinary person: cash, to verify. srl: accrual, to verify. Costs follow C3's
   definition of what a purchase costs. `/api/analytics/fiscal`, the MCP tool and the
   economic overview converge on this basis; today they differ, because `annual_revenue`
   (`analytics/repository.py`) sums issued invoices while `economic_overview` passes
   collected revenue (`analytics/service.py:465-469`). A missing parameter gives no figure.
2. **Declared**: the figures of the tax return as the accountant computed them (tax due,
   balance, advances), typed by an admin (§ 5) or read from the accountant's document through
   the inbox (C2) as a proposal that a person accepts (frame § A4).
3. **Paid**: the F24s actually paid (settlements, C4 § 1).

Forecast items are dropped and recreated when the estimate changes. When a declared figure
changes an advance already settled, the difference is left as a credit or debit on the next
tax due item of that tax, shown for a person to confirm (the legal treatment is «to verify»).

**Due items** (C4 § 1, direction `out`, counterparty the tax office with the tax named). Their
source kinds, validated in code (C4): `tax_balance`, `tax_advance`, `tax_instalment`,
`tax_credit`, `notice_plan`.

- the **balance** of the previous year and the **advances** of the current year, on the
  pack's dates (verified for income taxes, A5: 100% advance in 40% and 60%, 50% and 50% under
  ISA). The advance base is the declared tax net of deductions, credits, withholdings and
  excesses. IRPEF: no advance at or below €51.65, a single November payment below €257.52.
  IRES: the second instalment is split only above €103. The srl instalment rule is «to
  verify». Paying within 30 days after a deadline adds 0.40%, a pack datum;
- the advance by the **historical method** by default (the previous year's declared tax on
  the base above); an admin may choose the **forecast method** for a year, which uses the
  current estimate instead. The method's conditions, and the interest and penalties of a
  shortfall, are «to verify» (the method's existence is on the Agenzia page);
- `forecast` while the figure is an estimate, `expected` once declared, `settled` when paid.

**Company dates** follow the legal entity's fiscal year end, not June and November: the
balance and the first advance fall on the last day of the sixth month after it, or of the
month after a late approval of the financial statements; the second advance falls in the
eleventh month (Agenzia, the page A5 cites).

**Instalment plans.** The June balance and first advance may be paid in monthly instalments up
to 16 December, the November advance in one payment (verified, A5, for individuals); the
interest per instalment is «to verify» and pack data. An admin chooses the plan for a year,
and the due items split accordingly. A partly paid instalment keeps its remainder, with no
re-split. A plan for a notice from the tax office («avviso bonario», «cartella») is entered
by hand: total, number of instalments, dates, interest; its items are `notice_plan`.

**Credits.** A tax credit is a negative due item, source kind `tax_credit`, carrying its
origin: an IVA credit carried forward from C3, or a withholding suffered on sales (C3 § 3).
The year a withholding credit counts in is «to verify».

**F24.** Due items that fall on the same date, credits included, can be grouped in one F24,
with the tax code of each line (codes «to verify», shown as labels). Because a credit is a
negative item, the one bank movement matches the net, and settles the whole F24 and every
item in it (C4 § 3 split matching). F24 receipts are stored as documents on their F24 group;
C8 reads them there.

## 3. What each profile sees

- **solo, flat-rate**: the ceiling distance (as today), the substitute tax and INPS estimate,
  and the next two tax dates with their amounts, on the Home (frame § B5).
- **solo, ordinary**: IRPEF and INPS estimates, the withholdings already suffered, the IVA
  settlements (C3), the next tax dates.
- **company**: IRES, IRAP, IVA, the withholdings C3 writes for freelancers (C5) and staff (C7),
  the fee to the chamber of commerce, the next dates; the cash schedule (C4) carries all of it.

Every one of these reads follows § 5: a member or token that may not read tax figures sees no
tax row, amount or date.

## 4. Reminders

`tax_payment_due` is built now that the dates exist, closing the 2026-10-26 `DECISIONS.md`
note. It uses the catalogue's ladder from `2026-10-26-promemoria-design.md`: warning 14 days
and serious 3 days before the date, for a tax due item not settled, to admins. Messages on
email, Slack and push name the tax and the date and never the amount, which shows only in
the app. The missing-declared-figure reminder is its own catalogue row with its own detector:
it fires when a declared figure is missing after the date the pack says the return is filed
(to verify), so an estimate does not silently stand in for a real figure past its time.

## 5. Module, permissions and MCP

The module `taxes` (frame § A2). Tax figures are the most sensitive data the product holds:
`get_fiscal_estimate` is already admin-only and behind `full_access` over MCP
(`analytics/service.py:586`, `agent_actions.py`). «Admin» below means an admin whose scope
covers the legal entity (`require_single`, `admins_seeing`).

**C4 follows the same gate.** C4's due items are core, but every C4 read, write and settlement
of a due item whose source is a tax is filtered by role and token as this section says:
admins whose scope covers the legal entity, and agents with `full_access`. That includes the
schedule and the Home. Withholding due items written by C3 are not tax sources in this sense
unless C3 says so.

**ORB-188 stays.** `update_fiscal_profile` stays on the agent allowlist where the 2026-09-12
decision put it: the assistant sets the fiscal identity from «Primi passi»
(`apps/web/src/features/get-started/prompts.ts:17`). The new per-taxpayer parameters (§ 1)
live in the same profile and follow the same rule. What is gated are the tax figures and tax
due items.

| Action | Who | MCP tool |
|---|---|---|
| read estimates, declared figures, the tax calendar | admin; agent with `full_access` | `get_fiscal_estimate` (exists), `get_tax_calendar` |
| type a declared figure | admin; agent with `full_access` (sensitivity) | `set_declared_tax_figure` |
| accept a declared figure read from a document | a person (frame § A4; `PERSON_ONLY_ACTIONS`) | none |
| choose the advance method for a year | admin; agent with `full_access` (reversible, but it moves tax due items) | `set_tax_advance_method` |
| choose or enter an instalment plan | admin; agent with `full_access` | `set_tax_instalment_plan` |
| group due items into an F24, settle it | admin; agent with `full_access` | `group_f24`, settling through C4's tool |
| change the fiscal profile, its parameters and pack | as today: the agent allowlist | `update_fiscal_profile` (unchanged) |

The new tools live in `privileged.py`, or in a module added to `PRIVILEGED_MODULES` in
`apps/mcp/tests/test_mcp_invoice_ban.py`. Their service methods are classified in
`apps/mcp/tests/test_mcp_surface_coverage.py`. The ceiling reads stay open to every role as
today: they are revenue, not tax.

**Ownership and order.**

- Payroll coefficients, employer INPS, INAIL and TFR are C7's rows in the pack. The
  «gestione separata» for directors and occasional workers is C7's and C5's.
- The withholding due items are written by C3 (§ 5); C6 only shows them.
- C2's inbox gains the kinds `tax_document` (F24 receipts, tax returns, notices) and
  `payslip`.
- C6's tax-calendar card ships before C3's IVA settlement due-item card, since C3's IVA
  deadlines and December advance sit in the pack's calendar structure this design defines.

## 6. Surfaces

«Tasse» under «Amministrazione»: per legal entity, the year's taxes with their reading
(estimate, declared, paid), the calendar of dates, the plans and the F24s. On the Home, the
next dates (§ 3). New surface: brief and mockups before markup (frame § A7).

## 7. What is tested

The pack data per tax and year, and a missing parameter giving no figure; the law/profile
split and the frozen pack version on a reading; readings ranking by stage; the historical and
forecast advance on the same year; a declared figure replacing the estimate and recomputing
the advances, with the difference carried to the next item; one revenue basis across the REST
route, the MCP tool and the economic overview; an instalment plan splitting the June items
with interest; an F24 with a credit settled by one movement; the `tax_payment_due` ladder and
the missing-figure reminder; company dates for a non-calendar fiscal year; `it-company`
accepting RF01 and invoicing like `it-standard`; the year register for a year spanning the
switch; every tax read, including C4's schedule and the Home, refused to a token without
`full_access`; `update_fiscal_profile` still callable on the default surface.

## 8. Not in this design

Filing any return or paying any F24 from PigroCRM; minimum and maximum contribution rules beyond
what the pack carries; other incomes of a person; deductions and detractions; regional
variations beyond a rate in the pack; company forms other than the srl.

## Rows for DECISIONS.md

- 2026-11-09: the tax calendar is pack data keyed by tax year; law lives in the pack, the
  taxpayer's parameters in the fiscal profile and the fiscal year end on the legal entity;
  each reading freezes the pack version and parameters it used.
- 2026-11-09: `tax_payment_due` now exists, with the dates the pack carries; this closes the
  2026-10-26 note that left it unbuilt.
- No ORB-188 reversal: `update_fiscal_profile` stays on the agent allowlist.

## Cards

Filed once the design is approved: the tax calendar in the packs (first, before C3's IVA
settlement due-item card); the law/profile split and the frozen reading; the `it-company` pack
and the migration of existing srl entities; the estimate per regime on one basis, and the
accountant check of its contribution formula; declared figures (typed and read from a
document); tax due items with the advance methods; instalment plans; credits and F24
groupings; `tax_payment_due` and the missing-figure reminder; the tax tools and their
classification in the MCP tests; the inbox kinds `tax_document` and `payslip` (C2); the brief
for «Tasse»; that page and the Home's tax dates.
