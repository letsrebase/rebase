# Mapping mastro's forecasting and analytics section onto PigroCRM

Date: 2026-09-23. Status: **signed off by Lorenzo, 2026-09-23.** Implementation
issues REB-370 through REB-375 are filed under the milestone "See ceiling headroom,
concentration and a real forecast" (see § 5); § 6's own decision is resolved and
recorded in REB-361. Tracker: REB-352, in
the project *Bring mastro's ledger, invoice import and forecasting into PigroCRM*.
Written in English, the repository's current rule for anything written from now on;
the two spec documents this one matches in shape,
`2026-09-08-spazi-un-database-per-tenant-design.md` and
`2026-09-17-inviti-e-ruoli-di-uno-spazio-design.md`, are Italian only because they are
grandfathered records of decisions already taken.

## 0. Why

mastro's `docs/specs/2026-08-23-analysis-section-design.md` and
`docs/specs/flows-audit/05-the-year-and-the-gaps.md` describe a real, partly-built
analysis surface: a certainty split (collected / committed / projected) that feeds a
ceiling meter, a client-concentration chart, a cash calendar, and a renewal-assumption
mechanism that lets a human correct the projection's own blind spot for future
day-rate work. mastro's own design doc names the gap precisely, citing the audit
behind it: "the one number a ceiling exists to produce — how much room is left — is
computed nowhere" (`2026-08-23-analysis-section-design.md:51-55`, referencing
`flows-audit/05-the-year-and-the-gaps.md`'s finding directly, corroborated at
`flows-audit/05...:82-85`, "0 decisions the product can answer directly... not 'room
remaining before the soft/hard ceiling'"), and the redesign it proposes leads
with a headroom sentence and a "would this fit?" simulator on the contract form
(`2026-08-23-analysis-section-design.md:178-203`, `flows-audit/05...:122-154`).

This document goes through each of mastro's analysis concepts and states, for each,
what PigroCRM already has, what is missing, and — critically — which missing pieces
are blocked on REB-344 (mastro's ledger entities: contract, rate card, the day
lifecycle, and the ceiling/pack interface) landing first, versus which are buildable
today straight off PigroCRM's existing `Invoice`, `Customer` and `Deal` data and its
existing `analytics`/`dashboard`/`digest` modules.

The short version, confirmed by reading every module below first-hand rather than
assumed from the issue text: PigroCRM is **further along than mastro is on cash and
receivables**, and **has nothing at all** on the two concepts that require a contract —
ceiling headroom and pace/renewal assumptions. The concentration cap sits in the
middle: a whole-practice, calendar-year version is buildable now, a contract-anchored
one is not.

Every bare `path:line` reference into mastro below (`certainty.ts`, `forecast.ts`,
`ceiling.ts`, `ceiling-status.ts`, `it-flat-rate.ts`, and its own `docs/specs/**`) is
read against `github.com/fiorelorenzo/mastro` at commit `53ef2942`, 2026-09-22 — a
private reference repository, not a dependency this monorepo's own tooling resolves,
so the pin is recorded here rather than in a lockfile. A line drifting after that
commit is a reason to re-read the citation, not a reason to distrust this document's
own PigroCRM-internal citations, which are checked against this PR's own diff base.

## 1. mastro's five analysis concepts, one at a time

### 1.1 Collected — already built, no gap

mastro's `collectedAmount` is cash-basis money in the bank over a window
(`certainty.ts:224-233`). PigroCRM's `AnalyticsService.cash_overview`
(`analytics/service.py:400-475`) already computes exactly this, monthly and annually,
as `CashOverview.incassato` (`analytics/schemas.py:278`, fed by
`AnalyticsRepository.monthly_incassato`, `analytics/repository.py:303-376`). Nothing to
build here.

### 1.2 Committed — half built; the day-approval half is blocked on REB-344

mastro's `committedAmount` (`certainty.ts:273-328`) sums three things: every unpaid
invoice's remaining balance (`UnpaidInvoiceCommitment.remaining`,
`certainty.ts:75-78`), every `approved`/`worked`/`disputed` day not yet invoiced,
grouped and priced per rate card into a `PredictedInvoice`
(`certainty.ts:116-120`, `forecast.ts:139-176`'s `fetchUninvoicedWorkUnits`), and
recurring-fee occurrences that fall inside a contract's own irrevocability window
(`certainty.ts:149-162`, `219-222`'s `scheduleEnd`).

PigroCRM has a direct equivalent for the first piece and a rough, less certain
equivalent for the second:

- **Unpaid invoices.** `InvoiceRepository.sum_da_incassare` (`invoices/repository.py:333-352`)
  is `Σ totale` over issued, unpaid invoices — the same figure mastro's
  `UnpaidInvoiceCommitment` sums, modulo mastro's per-invoice remaining-balance
  granularity (a partially paid invoice) that PigroCRM's all-or-nothing
  `stato_pagamento` does not carry yet. `CashOverview.da_incassare`
  (`analytics/schemas.py:279`) already surfaces it monthly and annually.
- **Billable-but-unbilled work.** PigroCRM has no day-level state machine
  (`proposed → approved → worked → disputed → invoiced → paid`) — that is exactly
  REB-344's gap to close. What exists today is coarser:
  `AnalyticsService.unbilled_backlog` (`analytics/service.py:273-299`,
  `UnbilledBacklog` schema at `analytics/schemas.py:137-155`) sums every
  `TimeEntry` not yet invoiced (`Σ ROUND(ore × tariffa_applicata, 2)`), with no notion
  of "approved to bill" versus merely logged. It is usable today as a rougher stand-in
  for this half of "committed" (it already answers "how much is waiting to be
  invoiced"), but it cannot distinguish an hour a client has signed off on from one
  that has not been reviewed at all — the exact distinction mastro's state machine
  exists to make (`worked_without_approval` is a named branch): REB-344's own body
  states plainly there is "no approval evidence record anywhere in PigroCRM today".
  Once REB-344 lands the day-approval state machine,
  this half of "committed" should read approved-and-billable time only, narrowing what
  `unbilled_backlog` already computes rather than replacing its arithmetic.
- **Recurring-fee occurrences inside the irrevocability window.** Zero equivalent.
  PigroCRM has no contract, no rate card, no recurring-fee schedule, and no
  termination-notice concept (`grep -ri "rinnovo\|preavviso" packages/core` returns
  nothing outside an unrelated OAuth string, confirmed independently here as REB-344's
  own neighbour scan already found). **Blocked on REB-344.**

### 1.3 Projected — blocked on REB-344; PigroCRM's own "proiettato" is a different, existing concept, not a substitute

mastro's `projectedAmount` (`certainty.ts:403-433`) is recurring-fee occurrences
**beyond** the irrevocability window, up to a contract's own end date, optionally
extended by a human-recorded `RenewalAssumption` (`certainty.ts:143-147`,
`344-385`'s `renewalAssumptionContribution`, prorating `expectedVolumeMinorUnits`
across `horizonEndsOn`). This has no equivalent in PigroCRM at all — there is no
recurring-fee schedule to project beyond. **Fully blocked on REB-344.**

Separately, and worth being precise about so the two are never conflated:
`CashOverview.proiettato`/`bozze` (`analytics/schemas.py:267-285`,
computed in `AnalyticsService.cash_overview`, `analytics/service.py:417-426,463`) is
PigroCRM's own, already-shipped notion of "projected" — but it comes from **drafts and
proformas already sitting in the system**, not from a pace or a schedule. It answers
"what would the year collect if every draft and proforma issued as-is", which is a
narrower and more conservative claim than mastro's schedule-plus-assumption
projection: a consultant with no draft in progress projects zero here even if their
history says otherwise, exactly the blind spot mastro's own audit names for pure
day-rate clients (`flows-audit/05...:49-57`, "for a client billed purely by the day...
nothing about future, not-yet-worked days ever enters the projection"). PigroCRM's
`proiettato` should stay what it is (a draft-based figure, already useful and already
live) and a future pace/assumption-based projection should be a new, separate figure
alongside it, not a replacement.

### 1.4 Ceiling headroom and "would this fit?" — blocked on REB-344's ceiling entity, with one caveat

mastro's ceiling engine (`ceiling.ts:197-301`, `ceiling-status.ts:37-97`) evaluates
every active ceiling — pack-level (`measure: 'absolute_amount'`,
`perimeter: {kind: 'all_clients'}`, e.g. the forfettario's €85,000/€100,000 thresholds)
and contract-level (`measure: 'percentage_share'`, `perimeter: {kind: 'client'}`, see
§1.5) — through one function, and headroom is the pure derivation
`limitValue - currentValue` over that result
(`2026-08-23-analysis-section-design.md:178-182`, `flows-audit/05...:122-125`). The
"would this fit?" simulator (`flows-audit/05...:127-143`) reuses the same
`evaluateCeiling` against a synthetic extra `LedgerRow`, so it needs no new query, only
a ceiling to evaluate against.

PigroCRM's `FiscalProfile` (`fiscal/models.py:9-74`) is a single, non-historicised row
carrying the FatturaPA parameters and the three coefficiente/aliquota columns
`analytics/fiscal.py::estimate_income` reads — **it has no ceiling column, no
threshold, and no pack-interface concept at all**, confirmed directly rather than
inferred: nothing in `fiscal/models.py` names an 85k/100k figure or a percentage-share
concept, and `estimate_income`'s own `AVVERTENZA`
(`analytics/fiscal.py:34-38`) states outright that it "non tiene conto del minimale e
del massimale contributivo" — the ceiling is a declared, named gap in this module, not
an oversight, which is exactly why a ceiling check belongs as an extension of
`estimate_income`'s inputs rather than a second copy of the same arithmetic sitting
beside it.

Designing that ceiling entity — whether `FiscalProfile` grows the two forfettario
thresholds directly, or a new small table plays mastro's pack-interface role, and
whether a contract-scoped concentration clause is representable at all before a
`Contract` row exists — is explicitly REB-344's own scope (REB-344's issue body lists
"ceilings/jurisdiction pack" as one of the concepts it maps), not this document's. What
this document owns is: once that entity exists, the headroom figure and the "would
this fit?" simulator are pure derivations over `AnalyticsRepository.annual_revenue`
(`analytics/repository.py:378-389`, already `Σ Invoice.imponibile` for the year under
`_revenue_filter()`) exactly the way mastro's headroom is a pure derivation over
`evaluateActiveCeilings`'s output — no new revenue query, only a threshold to compare
against and, for the interactive simulator, a synthetic addition to that same sum. So
**the headroom figure itself is blocked on REB-344's ceiling entity** — there is
nothing to compute room against until a threshold exists. The simulator's other half,
the synthetic addition, is **not** blocked the way this document first assumed:
`Deal` already carries exactly the rate-and-volume estimate mastro's "day rate, days
per month" fields play — `ore_preventivate` (hours), `valore_preventivato` (money)
and `tariffa_oraria` (a rate, `Numeric(12, 6)` because "a rate is a factor")
(`deals/models.py:79-91`) — and `AnalyticsService.budget_vs_actual`
(`analytics/service.py:301-398`) already reads the first two to derive a comparable
figure (`tariffa_media_preventivata`, `analytics/service.py:369-373`), for a
different purpose (estimate-vs-actual on a deal already in the pipeline) but over the
same columns a synthetic "would a new deal's own estimate fit" addition would read.
A day-specific model (a rate that varies by calendar day, a start date, an
irrevocability window) is still absent and still waits on REB-344 exactly as mastro's
own audit names — the forecast "structurally cannot see future ad-hoc day-rate
income" (`flows-audit/05...:92-99`) is a real gap this simulator would inherit for a
*day-rate* engagement specifically — but an hours-and-value estimate on a not-yet-won
`Deal` is not a day-rate model, it is the coarser estimate PigroCRM already has, and
it is enough to source a synthetic addition the moment a ceiling exists to check it
against. So: **the simulator is blocked only on the ceiling entity, the same blocker
as headroom itself, not on a second, separate rate/day concept.** The one further
caveat: a **bare, non-interactive "collected so far this calendar year against a
known threshold"** number needs no new entity beyond a place to store the threshold,
since `annual_revenue(anno)` already exists — but that number is materially weaker
than what "Done when" a ceiling feature would actually need (a forward answer to
"can I say yes"), so this document does not recommend building the bare version as a
stand-in; see § 5.

### 1.5 Client concentration cap — the whole-practice version is buildable now; the contract-anchored version is blocked on REB-344

mastro's client concentration cap is a `percentage_share` ceiling scoped to one
client (`ceiling.ts:270-301`, `ceilingFromContractRow`), evaluated over the ledger the
same way a pack ceiling is, but with a period basis (`cash_received_contract_year`)
anchored to the contract's own start date rather than the calendar
(`ceiling-status.ts:20-26,51-54`).

PigroCRM already has a concentration-shaped view, and it is important not to conflate
it with what mastro's cap measures: `DashboardService.get_receivables_dashboard`
(`dashboard/service.py:274-314`) returns `per_cliente`
(`EsposizioneCliente`, `dashboard/schemas.py:306-315`), backed by
`InvoiceRepository.receivables_by_customer` (`invoices/repository.py:623-651`). Its
`quota` is a share of the **largest customer's outstanding receivable**
(`_quota`, `invoices/repository.py:133-136`, "a share of the largest figure... for its
months", the same helper `cash_overview`'s own `share()` uses for its stacked bars) —
that answers "who currently owes the most", a bar-chart proportion, not "what
percentage of my total invoiced income comes from this one client", which is what a
concentration cap actually caps. The two questions are easy to conflate because both
render as a per-customer percentage; they are not the same figure and PigroCRM's
existing one does not become the concentration cap by relabelling it.

A true concentration-cap figure — one client's share of total invoiced revenue over a
period, compared against a cap — is buildable **today**, with no new entity: `Invoice`
already carries `customer_id` directly (`invoices/models.py:59-61`, not only through
`Deal`), so grouping `Σ Invoice.imponibile` by `customer_id` under the same
`_revenue_filter()` `annual_revenue` already applies (`analytics/repository.py:34-51,
378-389`), divided by that same `annual_revenue(anno)` total, is one new query
alongside `annual_revenue` itself, in the same repository, following the same pattern.
What is genuinely blocked on REB-344 is only the **contract-anchored** version: a cap
whose reset window follows one specific engagement's own start date rather than the
calendar year needs a `Contract` row to anchor to, which does not exist yet. So: a
whole-practice, calendar-year concentration view is not blocked; a per-contract clause
with its own anniversary is.

### 1.6 Cash calendar — already built for the ledger-independent 90%; only the contract-date overlay is blocked

mastro's cash calendar buckets collected/committed/projected by month across a
rolling window, with contract-date markers (irrevocability window ends, renewal
dates) alongside it (`2026-08-23-analysis-section-design.md:205-219`).

PigroCRM already has two independent, complementary month-by-month views that
together cover everything except the contract-date markers:

- `AnalyticsService.cash_overview`'s `CashOverview.mesi`
  (`analytics/service.py:400-475`, `CashMonth` at `analytics/schemas.py:226-264`) is a
  full calendar-year, month-by-month stacked view of `incassato`/`da_incassare`/
  `bozze`/`costi`, with real relative shares already computed
  (`quote_andamento`/`quote_proiezione`, `analytics/service.py:446-455`) — notably,
  this already avoids the exact bug mastro's own redesign is fixing ("the stacked bar
  never stacks", `2026-08-23-analysis-section-design.md:40-42`): PigroCRM's
  `CashMonth` already carries genuinely different layers with real relative
  proportions, not one dominant tier per month by construction.
- `DashboardService.get_receivables_dashboard`'s `per_mese`
  (`ReceivablesDashboard.per_mese`, backed by `InvoiceRepository.receivables_by_due_month`,
  `invoices/repository.py:598-621`) is a forward-looking, due-date-ordered view of what
  is owed — the same question mastro's cash calendar answers for its "committed"
  layer — and `fasce`/`ageing_receivables` (`invoices/repository.py:557-596`) already
  buckets overdue receivables into six bands (`scaduto`, `entro_30`, `da_31_a_60`,
  `da_61_a_90`, `oltre_90`, `senza_scadenza`), a direct superset of mastro's proposed
  "overdue by band" feature (`2026-08-23-analysis-section-design.md:216-217`,
  0-30/31-60/61-90/90+ — PigroCRM's extra `senza_scadenza` band exists because
  `data_scadenza` is nullable here, `invoices/repository.py:83`).

What is missing, and blocked on REB-344, is only the overlay mastro's design adds on
top of an equivalent chart: markers for contract-specific dates (an irrevocability
window closing, a renewal deadline) — there is no contract row to read those dates
from. The chart itself is not blocked; the annotations on it are.

### 1.7 Renewal assumptions and pace — fully blocked on REB-344

mastro's `RenewalAssumption` (`certainty.ts:133-147`) is an explicit, human-recorded
belief about revenue beyond a contract's own known term (a probability, an expected
volume, a horizon date), and the "observed pace" proposal
(`2026-08-23-analysis-section-design.md:192-200`) reads a contract's own invoicing
history to suggest one, which a human then accepts. Neither a contract, nor a
recurring-fee schedule, nor any per-engagement pace-tracking exists in PigroCRM today.
**Fully blocked on REB-344**; nothing here is buildable early without inventing the
contract concept itself, which is not this spike's job.

## 2. The rivalsa fold-in: one automatic path, and one real tension to flag

REB-344 owns whether and how a PigroCRM contract elects the 4% INPS rivalsa surcharge
(art. 1 comma 212, legge 662/1996) as an automatic invoice line. This document's job is
to state precisely what happens to that money once it exists, since the two issues
share the same underlying `ricavi` figure.

**The good news, verified rather than assumed: no code change is needed for the
ceiling side.** `Invoice.imponibile` is computed by `totals.py::sum_totals`
(`invoices/totals.py:185-195`) as the sum of every `RiepilogoGroup`'s `imponibile`
(`invoices/totals.py:149-181`) across the whole document, **excluding only the stamp
duty** — a rivalsa line, whatever `natura` it carries, is one more VAT-grouped line and
therefore already inside `Invoice.imponibile` the moment REB-344 adds it, with no
change to `sum_totals` or to any query. `AnalyticsRepository.annual_revenue`
(`analytics/repository.py:378-389`) sums exactly this column, filtered only by
`_revenue_filter()` (`analytics/repository.py:34-51`) — `tipo`, `stato`,
`deleted_at`, nothing about which lines make up the total. So a ceiling check built on
`annual_revenue` (or the per-customer concentration query in § 1.5) automatically
includes a rivalsa line the day REB-344 ships it, exactly as mastro's own citation
requires: "l'importo concorre al limite dei ricavi e compensi del regime forfettario"
(`mastro/src/lib/server/fiscal/packs/it-flat-rate.ts:262`, `474`).

**The tension: that same automatic inclusion is wrong for the tax estimate, and this
is the same citation's second half.** The full sentence in mastro's own legal citation
continues "... ai sensi della circolare... n. 10/E del 4 aprile 2016, **pur non
costituendo reddito imponibile**" — while not constituting taxable income
(`it-flat-rate.ts:262`, `474`, corroborating comment at lines 51-55: "counting towards
ricavi/compensi despite not being taxable income"). `estimate_income`
(`analytics/fiscal.py:43-106`) has exactly one `ricavi` parameter, and it applies
`coefficiente_redditivita` to **all** of it to derive `imponibile` (the annual taxable
base, `analytics/fiscal.py:66`) — there is no second input for "money that counts
toward the ceiling but should not be taxed at the coefficiente rate". `get_fiscal_estimate`
(`analytics/service.py:524-567`) feeds this parameter from the same
`annual_revenue(anno)` a ceiling check would read (`analytics/service.py:563`). So the
day a rivalsa line exists and both features read `annual_revenue` unmodified, the tax
estimate would silently overtax the rivalsa's 4% at the coefficiente rate, while the
ceiling check would (correctly) count it in full. **This is a decision for whoever
builds the ceiling and the rivalsa line together, not something this spike can resolve
by itself**: either `estimate_income` grows a second parameter (ricavi included in the
ceiling perimeter but excluded from the coefficiente base), or the rivalsa amount is
carried as its own tagged figure at the point revenue is summed (mirroring mastro's own
mechanism, a per-charge `countsTowardsRevenuePerimeter` declaration,
`it-flat-rate.ts:280-283`, read once by `fetchLedgerRows` rather than hardcoded per
charge) so `annual_revenue`-for-ceiling and `ricavi`-for-tax can diverge by exactly the
rivalsa amount. Flagged here as a decision for the lead in § 6, not resolved by this
document, and it belongs to whichever issue implements the ceiling and the rivalsa
line together — most likely a single follow-up rather than two, since neither is
correct without the other.

Checked and confirmed the opposite is not a problem: PigroCRM's stamp duty (bollo) is
never recharged to the client (`invoices/pdf.py:213-218`'s own PDF declaration, "a
carico dell'emittente") and is already excluded from `imponibile`/`totale`
(`invoices/totals.py:186-192`) — nothing to fix on that side.

## 3. Where a future signal plugs into the existing dashboard and digest, not a new mechanism

`DashboardService.get_operational_dashboard` (`dashboard/service.py:212-272`) already
carries a `segnali: list[Signal]` (`dashboard/schemas.py:233-251`), each a plain
`codice`/`etichetta`/`conteggio`/`collegamento` row over a `COUNT` its own repository
produced — exactly the shape a future "approaching ceiling" or "customer concentration
above the preferred share" signal would take, added as one more entry in that same
list rather than a new alerting mechanism. `DigestService.build`
(`digest/service.py:134-275`, `WeeklyDigest` schema at `digest/schemas.py:115-135`)
already sends "Da incassare" split into overdue and due-within-seven-days
(`IN_SCADENZA_GIORNI`, `digest/service.py:61`) every Monday — this is the natural home
for a future "day pending approval" or "approaching ceiling" notification mastro's own
review flags as entirely missing today (`flows-audit/02-the-daily-loop.md`: "nothing
pushes, digests, or badges" a pending proposal). Concretely:

- The § 1.5 whole-practice concentration share, once it exists, can be a new
  `Signal` on the operational dashboard the moment a preferred-share threshold is
  configured — buildable now, blocked on nothing beyond § 1.5's own query.
- A "day pending approval" signal needs REB-344's day-approval state machine first.
- An "approaching ceiling" signal needs REB-344's ceiling entity first (§ 1.4).

No new push mechanism is needed for any of these; only new rows in structures that
already exist.

## 4. Summary: blocked on REB-344 versus buildable now

| Concept | Buildable today, off existing `Invoice`/`Customer`/`Deal` and `analytics`/`dashboard`/`digest` | Blocked on REB-344 |
|---|---|---|
| Collected | Already built (`cash_overview`'s `incassato`) | — |
| Committed — unpaid invoices | Already built (`sum_da_incassare`, `da_incassare`) | — |
| Committed — billable unbilled work | Rough stand-in exists (`unbilled_backlog`); narrowing to "approved only" | The day-approval state itself |
| Committed — recurring fees in-window | — | Entirely (no contract, no schedule) |
| Projected (schedule + assumption) | — | Entirely (no contract, no schedule) |
| PigroCRM's own draft/proforma "proiettato" | Already built, distinct concept | — |
| Ceiling headroom (static, YTD vs. threshold) | Arithmetic only, needs a threshold to read | The threshold/pack entity itself (REB-344's own scope) |
| Ceiling headroom ("would this fit?" simulator) | The synthetic addition itself, off `Deal.ore_preventivate`/`valore_preventivato` | Only the ceiling entity to check it against (same blocker as headroom) |
| Client concentration, whole-practice, calendar-year | Buildable now (new query, `Invoice.customer_id` grouping) | — |
| Client concentration, per-contract, anniversary-reset | — | Entirely (no `Contract` row to anchor to) |
| Cash calendar (the chart itself) | Already built (`cash_overview.mesi`, `receivables_by_due_month`, `ageing_receivables`) | — |
| Cash calendar — contract-date markers | — | Entirely (no contract dates to read) |
| Renewal assumptions / observed pace | — | Entirely |
| Rivalsa folding into the ceiling-relevant ricavi | Automatic once REB-344 adds the line (`sum_totals`, no code change) | The rivalsa line's existence itself |
| Rivalsa **not** being taxed at the coefficiente rate | — | A real design decision, owned jointly by REB-344's rivalsa work and whichever issue builds the ceiling (§ 2) |

## 5. Proposed follow-up work

Per this project's own gate — "starts with three design spikes, one per area above; no
implementation issue is filed until Lorenzo signs off on each spike's entity
mapping" — the order below is recorded here, inside the merged spec, and **not** filed
as Linear issues. It states the order this spike would recommend once REB-344's
entity mapping is signed off, with the ledger-independent pieces sequenced first
where that is genuinely possible.

1. **Whole-practice client concentration view.** New query
   (`Σ Invoice.imponibile` grouped by `customer_id` over a calendar year, divided by
   `annual_revenue`), a new card on the economic dashboard or its own report. No new
   entity, no dependency on REB-344.
   Acceptance: a reader can see each customer's share of the year's invoiced revenue,
   ranked, distinct from the existing `per_cliente` receivables-exposure view.

2. **A "customer concentration above preferred share" signal**, alongside the three
   existing ones on `OperationalDashboard.segnali`, once (1) exists and a threshold is
   configured somewhere reachable by that signal.
   Acceptance: the operational dashboard shows a fourth signal, following the same
   `codice`/`etichetta`/`conteggio`/`collegamento` shape as the other three.

3. **Narrow `unbilled_backlog`'s committed contribution to approved-only time**, once
   REB-344's day-approval state machine exists.
   Acceptance: the committed figure counts only time in an approved-or-later state,
   not every logged, unbilled hour.

4. **The ceiling entity, the headroom figure, and the "would this fit?" simulator
   together**, once REB-344's pack-interface decision lands, extending
   `estimate_income`'s inputs rather than duplicating its arithmetic, and resolving
   § 2's tax-versus-ceiling tension for `ricavi` as part of the same change. The
   simulator is sequenced with headroom rather than after it, since its synthetic
   addition already has a source (`Deal.ore_preventivate`/`valore_preventivato`,
   § 1.4) and needs nothing from REB-344 beyond the ceiling entity itself.
   Acceptance: a reader sees room remaining before each active threshold, computed
   from real `annual_revenue`, with the rivalsa question from § 2 answered rather
   than left open; entering an hours-and-value estimate on a not-yet-won deal shows
   a live projection against that same threshold, with no persistence required.

5. **Contract-anchored concentration cap and cash-calendar contract-date markers**,
   once REB-344's `Contract` row exists.
   Acceptance: a concentration cap can be scoped to one engagement's own anniversary,
   and the cash calendar shows renewal and irrevocability-window dates alongside the
   months it already renders.

6. **Recurring-fee projection and renewal assumptions**, last, once REB-344's
   recurring-fee schedule exists.
   Acceptance: a contract's own recorded pace or assumption contributes to a genuine
   "projected" figure distinct from PigroCRM's existing draft-based `proiettato`.

## 6. Decision for the lead — resolved 2026-09-23

Lorenzo, 2026-09-23: the rivalsa amount is carried as its own tagged, subtractable
figure at the point revenue is summed (mirroring mastro's `countsTowardsRevenuePerimeter`
declaration, `it-flat-rate.ts:280-283`), not a second `ricavi`-shaped input on
`estimate_income`. This keeps `estimate_income`'s signature stable for every other
caller and puts the "does this charge count toward the ceiling but not the coefficiente"
question on the charge itself, the same place mastro's own pack declares it. Built as
part of REB-361 (the ceiling-and-rivalsa issue in the ledger milestone), alongside the
pack module and the rivalsa invoice line themselves — the same issue this decision
always belonged to, since neither is correct without the other.
