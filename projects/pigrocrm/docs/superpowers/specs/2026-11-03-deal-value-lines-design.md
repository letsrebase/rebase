# Deal value lines and the deal-to-contract conversion

Date: 2026-11-03. Card: REB-927. Project: «Price a deal as a mix of one-off, recurring and
day-rate lines» (P-REB-78).

## 1. Why, and what is already decided

A deal carries one number, `deals.expected_value` (`deals/models.py:85`). Every commercial
figure sums it as one kind of number: the pipeline total and the weighted value per stage
(`deals/repository.py:126`) and the value won in a period (`deals/repository.py:213`). A
monthly maintenance fee typed as «2.000» and a project typed as «30.000» are added as if
they were the same thing, and a fee typed as its yearly total says nothing about when the
money arrives. The ceiling simulator (`analytics/service.py:201`) adds a raw budget
(`budgeted_value`, or hours times rate) to the current year, so it cannot see when a
fee's payments fall either.

The shapes a deal really takes, from the deals of the rebase space read over the CRM's MCP
on 2026-11-03: a project at a fixed price, a person placed at a day rate for a number of
months, a fractional role paid every month, and mixes of them. The contract side already
knows these shapes: a contract has rate cards of type `one_off`, `fixed_recurring`,
`daily` and `hourly` (`contracts/models.py:50`), and `contracts/projection.py` expands
recurring fees into dated occurrences. The deal, the pre-sale object, has none of it, and
nothing links a deal to the contract it becomes.

Lorenzo decided on 2026-11-03:

- A deal's value is any combination of one-off amounts, recurring fees and day or hour
  rates. A recurring fee is monthly or annual.
- A recurring fee may have an end. An open-ended one is counted at 12 months.
- The value is gross: what the customer pays, VAT excluded, never rebase's margin.
- A won deal converts into a linked contract, and the conversion carries every amount.

And, asked about the one limit the design adds: «per ora lasciamo un solo tipo per
contratto», so a deal and a contract hold at most one day or hour rate in force on a
date (§2, §7).

## 2. The value line

A deal owns an ordered list of lines, `deal_value_lines`, an owned child table with no
soft delete, the shape of `rate_cards` under `contracts`. Names and values reuse the rate
card's wherever the meaning is the same.

| Column | Type | Meaning |
|---|---|---|
| `deal_id` | FK `deals.id`, not null, indexed | The owner. |
| `position` | `Integer`, not null | Order on screen; unique per deal. |
| `description` | `String(255)`, nullable | What the line is for («Sviluppo», «Manutenzione»). |
| `unit` | `String(10)`, not null, CHECK `flat`/`day`/`hour` | What one quantity is: a lump sum, a day, an hour (`RATE_CARD_UNIT`'s names). |
| `delivery_period` | `String(10)`, not null, CHECK `one_off`/`monthly`/`annual` | How often the line is paid (`RATE_CARD_PERIODS`'s names). |
| `unit_price` | `Numeric(12, 2)`, not null, `>= 0` | The price of one unit, gross; `rate_cards.amount`'s precision. |
| `quantity` | `Numeric(8, 2)`, not null, `> 0` | Units per occurrence. Always 1 on a `flat` line (CHECK): a fee is its price. |
| `start` | `Date`, nullable | The first occurrence. Null follows the deal's `expected_close_date`. |
| `duration_months` | `Integer`, nullable, `>= 1` | On a recurring line, how long it runs; null is open-ended. On a `one_off` day or hour line, the window its days fall in; null is open. Always null on a `flat` `one_off` line (CHECK). |

What the combinations mean:

| Case | Lines |
|---|---|
| Project at a fixed price | `flat`, `one_off`, 30.000 |
| Upfront plus maintenance | `flat`, `one_off`, 20.000; `flat`, `monthly`, 500, open-ended |
| Upfront plus a day rate | `flat`, `one_off`, 10.000; `day`, `monthly`, 600 × 4, 12 months |
| Setup plus a yearly licence | `flat`, `one_off`, 3.000; `flat`, `annual`, 6.000, 36 months |
| A person for a year | `day`, `monthly`, 450 × 18, 12 months |
| A training block in December | `day`, `one_off`, 800 × 5, 1 month |

**At most one `day` or `hour` line in force on any date.** A contract prices a worked day
by the one day or hour rate in force on its date (§7), so two such rates in force together
could not convert. A day or hour line is in force from its start for `duration_months`,
or with no end when that is null. The deal service refuses a line that overlaps another,
naming it, and runs the check on every write of the lines and of `expected_close_date`,
since a line with no `start` moves with it. A day or hour line needs a start (its own or
the deal's `expected_close_date`); without one it is refused. Two people at two different
day rates at the same time are two deals and two contracts. Fees (`flat` lines) overlap
freely. Lorenzo confirmed the limit on 2026-11-03.

**Limits.** A line's total (§3) and the deal's total must fit `Numeric(12, 2)`; the
service refuses one that does not with `ValidationFailed` naming the line, never a
database error.

**Writes.** The API replaces the whole list in one write: the old rows are deleted and
flushed before the new ones are inserted, so the unique `(deal_id, position)` holds.

**Row-level security.** `deal_value_lines` carries the `legal_entity_scope` policy
`deal_visible(deal_id)` with `FORCE ROW LEVEL SECURITY`, as every child table
(`migrations/versions/0048_azienda_scope.py:62` for `rate_cards`), and joins the test
template's statement list.

**Existing data.** The migration writes one `flat`, `one_off` line for every deal with a
non-null `expected_value`, at that value. A deal with no value gets no line.

## 3. The figures derived from the lines

All arithmetic is one pure function in core, `value_summary(terms)`, fed by a deal's lines
and, after the conversion, by a contract's rate cards (§8). The occurrence stepping is a
helper shared with `contracts/projection.py`, which today handles only `fixed_recurring`
cards. Each line is rounded with `round_money` (`ROUND_HALF_UP`) and the rounded lines are
summed, as the weighted value already does.

### 3.1 Occurrences

A `one_off` line occurs once, on its start. A recurring line occurs on its start and every
month (or every 12 months) after it, counted from the start and never from the previous
occurrence, while the occurrence falls before the start plus its duration. An open-ended
line uses a duration of 12 months. This is the stepping of
`generate_recurring_fee_occurrences` (`contracts/projection.py:76`): a monthly line of 12
months occurs 12 times, an annual one of 36 months 3 times, an annual one of 18 months
twice.

### 3.2 The figures

- **Total**: Σ `unit_price × quantity × occurrences`. The deal service writes it into
  `deals.expected_value` in the same transaction as any change to the lines, and no caller
  can set it. No lines: `expected_value` is null and the deal counts as «senza valore» as
  today. Every query that sums `expected_value` stays as it is, the weighted value
  included.
- **One-off**: Σ of the `one_off` lines.
- **Monthly recurring**: Σ of the monthly lines' `unit_price × quantity`, plus Σ of the
  annual lines' `unit_price × quantity / 12`. Day and hour lines with a monthly or annual
  period count: that money arrives every month.

The one-off and monthly figures are computed on read, never stored.

### 3.3 Dates and the share in a year

A line with no `start` starts on the deal's `expected_close_date`. The total does not
depend on dates; the share in a year (§6) does. That share is the occurrences dated in the
year, except a `one_off` day or hour line with a duration, which is spread evenly over the
months of its window, since its days are worked across them. The total stays the same.

## 4. API and MCP

- `DealCreate` and `DealUpdate` take `value_lines`, the whole list. Neither takes
  `expected_value` any more, and `DealCreate` gains `extra="forbid"` (only `DealUpdate`
  has it today, `deals/schemas.py:68` and `:96`), so a REST call that sends it is refused
  instead of silently dropped.
- `expected_value` stays in `EXTRA_NATIVE_FIELDS["deal"]` (`schema_registry.py`) so the
  name cannot become a custom field, and `value_lines` is a native field.
- `DealRead` carries `expected_value`, `one_off_value` and `monthly_recurring_value`. The
  two figures are computed per page in one batched query, the way `customer_names` is
  (`deals/service.py:162`), so a list and the kanban carry them too. The lines themselves
  are returned only by the single deal read.
- `POST /api/deals/value-preview` takes a list of lines and the deal's expected close date
  and answers the three figures, so the form shows them without the browser computing
  money (§5).
- MCP `create_deal` and `update_deal` take `value_lines`; a call with `expected_value` is
  refused naming the field and pointing at `value_lines`. The `deal://` and `customer://`
  resources print the lines and the three figures.

## 5. The web app

The deal form edits the list of lines: a row per line with description, unit, period,
price, quantity, start and duration. The three figures under the list come from
`value-preview`, called on change; the browser adds and multiplies nothing, and
`no-float-money.test.ts`'s `ECONOMIC_FIELDS` gains `unit_price`, `quantity`,
`one_off_value` and `monthly_recurring_value`. A new deal opens with one `flat`, `one_off`
line, so a person who knows only the total types it as before. The deal detail shows each
line and the three figures, with a hint under the total that an open-ended fee counts 12
months. Lists and the kanban show the fee alone (`500 €/mese`) when there is no one-off, the
one-off plus the fee when there are both, the one-off alone when nothing recurs, and the
total only for a deal with no lines (REB-950). The timeline labels (`payloadLabels.ts`) gain `value_lines`.
«Nuova proforma» on a deal prefills the one-off value, or nothing when it is zero, instead
of the total (`DealDetail.tsx:234`). These are edits inside existing surfaces and need no
brief.

## 6. Dashboard and ceiling simulator

- The commercial dashboard keeps its totals and adds the monthly recurring value of the
  open deals and of the deals won in the period, labelled as a monthly figure and never
  added to a total. It is computed through `value_summary` in Python and declared as the
  third exception to the rule that its aggregates are plain sums
  (`deals/repository.py:132`).
- The simulator gains an optional `deal_id`. This reverses on purpose the docstring of
  `CeilingSimulationQuery` (`analytics/schemas.py:380`), which accepts only raw figures so
  the question can be asked before a deal is saved; the raw path stays for that case, and
  the docstring is rewritten. With a `deal_id`, the ceiling addition is the deal's share in
  the simulated year (§3.3), and the addition checked against a contract's customer share
  cap (`cap_service.py`) is the deal's share in that contract's anniversary year containing
  `expected_date`.

## 7. Rate cards that can coexist

`ck_rate_cards_no_overlap` (`contracts/models.py:218`) refuses any two cards of a contract
in force on the same day, so a contract cannot hold an upfront fee, a monthly fee and a
day rate together, which is what a converted deal brings. The constraint gets a
`WHERE type IN ('daily', 'hourly')`: a contract has at most one day or hour rate in force
on a date, the one a worked day is priced by, and its fees overlap freely.

Every reader that resolves "the card in force on a date" reads only day and hour cards:

- core: `work_units/day_mapping.py:54` (`resolve_rate_card`),
  `work_units/invoicing.py:109`, `analytics/repository.py:315`,
  `invoices/import_review.py:229`;
- web: `features/days/labels.ts:166` (`activeRateCard`, used by `DayForm.tsx:70`; the
  query in `days/queries.ts:117` fetches every card) and
  `features/contracts/RateCardsSection.tsx:123` (`cardInForceOn`, the «Chiudi la tariffa
  in vigore» banner);
- `rate_card_service.py:95`: `close_previous` is accepted only on a new `daily` or
  `hourly` card and closes only the `daily` or `hourly` card in force; on a fee card it is
  refused.

`analytics/days.py:297` and `reminders/detectors/billing_contracts.py` already filter by
type. A grep for `valid_from <=` and `valid_from <` over core and web is part of the
card's evidence.

`daily` and `hourly` cards gain `expected_quantity` (`Numeric(8, 2)`, nullable), and
`ck_rate_cards_period_only_fixed_recurring` lets them carry a `delivery_period` too, both
or neither: the forecast a deal's day or hour line carried, in the same names. It does not
change how a day is priced, and a hand-made day card leaves both empty. Every layer of the
rate card gains them: `RateCardCreate`, `RateCardUpdate` and `RateCardRead`, MCP
`create_rate_card` and `update_rate_card`, the proposal's card, `RateCardsSection` and
`payloadLabels.ts`. `rules.py`'s `check_rate_card_shape` mirrors the new CHECKs, because
`RateCardService.create` reports any `IntegrityError` as an overlap.

Renewal (`renewal_service.py:132`) copies `expected_quantity` along with the other
columns of a carried card.

## 8. The conversion

- `contracts.deal_id`, a nullable unique FK to `deals`: a contract comes from at most one
  deal, a deal becomes at most one contract. `deal_id` joins `_NOT_RENEWED`
  (`renewal_service.py:25`), so a renewal does not copy it; the chain is
  `previous_contract_id`.
- `ContractService.create_from_deal(deal_id, data, actor)`, exposed as
  `POST /api/deals/{deal_id}/contract` and the MCP tool `convert_deal_to_contract`, under
  the action key `convert_deal_to_contract`, classified in `agent_actions.py` with
  `create_contract` and labelled in `ACTION_LABELS`. Refused unless the deal sits in a
  `won` stage, has at least one line and has no contract yet.
- What the deal gives: the customer and the legal entity, a proposed title (the deal's
  name), a proposed start (the earliest line start, else today), a proposed end, and the
  cards. What the caller gives or confirms: title, start, end, `renewal_type`, the notice
  days, `billing_cadence` and `expense_policy`. Payment terms stay null and cascade from
  the customer. The contract starts as `draft`.
- The proposed end is null when any line has no end (an open-ended recurring line, or a
  day or hour line with no duration); otherwise it is the latest last day among the lines
  that have one. `flat` `one_off` lines do not set it, so a project-only deal proposes no
  end and the person enters the delivery date.
- A line with no `start` takes the contract's start. Every card's dates are fixed at the
  conversion.
- Each line becomes one card, valid from the line's start, with `valid_to` on the day
  before start plus duration when the line has one:

| Line | Card |
|---|---|
| `flat`, `one_off` | `one_off`, unit `flat`, amount = `unit_price`, `valid_to` = `valid_from` |
| `flat`, `monthly` | `fixed_recurring`, unit `month`, `delivery_period` `monthly`, amount = `unit_price` |
| `flat`, `annual` | `fixed_recurring`, unit `year`, `delivery_period` `annual`, amount = `unit_price` |
| `day`, any period | `daily`, unit `day`, amount = `unit_price`, `expected_quantity` = `quantity`, `delivery_period` = the line's |
| `hour`, any period | `hourly`, unit `hour`, the same as `day` |

  A `flat` `one_off` card is closed on its own day, so a renewal does not carry the
  upfront fee into the next term (`renewal.py:138`). It follows that
  `billing_contracts.py`'s completion detector, which reads a one-off card in force at
  the contract's end, does not flag it: an upfront is billed at the start, from the
  deal's proforma (§5).
- **The invariant, tested by property over random line mixes with two-place prices and
  quantities**: `value_summary` over the deal's lines and over the new contract's cards
  gives the same total, one-off and monthly figures, with the end the conversion proposes
  (a person who shortens the term changes the contract's value on purpose). On the contract side it reads a
  `fixed_recurring` card by its period (quarterly included), a `daily` or `hourly` card by
  its forecast and ignores one without, and a `one_off` card once.
- After the conversion the deal's lines are locked: a write to them is a `Conflict` naming
  the contract, which from then on is the one source of the price. The deal keeps its total
  as booked; the deal page links to the contract and the contract page to the deal.

## 9. The hub

A company request is always a day rate. `crm_link.py` stops sending `expected_value` and
sends one `day` line, priced at the daily budget, starting on `period_from`:

- With a duration in the AI draft: `one_off`, `quantity` = the total days (days a week ×
  weeks × people), `duration_months` = ⌈weeks × 12 / 52⌉. The total is exactly today's
  (400 × 3 × 12 × 2 = 28.800, `test_crm_link.py:190`), and the share in a year is spread
  over the window (§3.3).
- With no duration: `monthly`, open-ended, `quantity` = days a week × people × 52 / 12 to
  two places, so the CRM counts it at 12 months instead of leaving the value empty.
- No daily budget or no days a week: no line, as today. The hub sends no line when the
  price, the quantity or the line's total would not fit the CRM's limits, so a large value
  never becomes a permanent `422` (`crm_link.py:75`, the guard it keeps today).

`LeadDeal` (`engagements/schemas.py:170`) takes `value_lines` instead of `expected_value`.
Both sides forbid unknown fields, so in either release order a request filed between the
two tags gets a `422`, which the hub records as `rejected` (`crm_link.py:271`) and the sweep
does not retry. The two tags ship one after the other, and any request rejected or still
`to_link` in the window is sent again with «Riprova»; no alias is kept.

The match door (`engagements/service.py:654`) keeps creating its deal with `hourly_rate`
and `budgeted_hours` and no line: the letter's fee is what rebase pays the freelancer, not
what the customer pays. A person adds the lines before converting such a deal.

## 10. What does not change

- `budgeted_value`, `budgeted_hours` and `hourly_rate` on the deal: the internal estimate
  and the rate that values logged hours (`timetracking/rates.py`), not what the customer
  pays.
- The weighted value's formula and the «stima» label.
- A contract created by hand, which has no deal and no forecast quantities.
- Quarterly lines on a deal: contracts accept quarterly fees, deals do not until someone
  needs one.
