"""Every shape this slice's reports return.

Declared together, and ahead of the methods that build them (tasks 4B-5 to 4B-8), so
that the vocabulary of the whole slice is readable in one file: `revenue` means the same
thing in `DealPnl`, `PnlTotals` and `FiscalEstimate`, and a second meaning would have to
be introduced here, in the open, rather than discovered in a service.

Every figure arrives already summed. §6 forbids the frontend of this slice from
computing any economic total at all -- the previous system's whole P&L lived in `App.jsx`, with
three fiscal constants and float hour sums.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.contracts.schemas import ContractCapSimulation
from pigrocrm.core.fiscal.pack import CeilingConsequence

# Mirrors `Deal`'s own Numeric(p, s) column widths (`deals/models.py`), the same
# reasoning `deals/schemas.py`'s own constants give for existing at all: a value
# beyond a column's capacity would otherwise sail past Pydantic and reach `flush()`
# as a raw `DataError`. Declared locally rather than imported from `deals.schemas`:
# `pigrocrm.core.deals`'s own `__init__.py` imports `DealService`, which reaches
# `dashboard.schemas`, which imports back from this very module -- the same
# reasoning `auth/schemas.py` already gives for declaring `default_hourly_rate`'s
# own width locally rather than importing it.
CEILING_HOURS_MAX_DIGITS = 8
CEILING_VALUE_MAX_DIGITS = 12
CEILING_DECIMAL_PLACES = 2
CEILING_FACTOR_MAX_DIGITS = 12
CEILING_FACTOR_DECIMAL_PLACES = 6

# The same three values as `DealTimeSummary.status`, and deliberately a separate
# declaration rather than an import: `timetracking` answers "what is the state of these
# hours" and `analytics` answers "what is the state of this deal's economics". They
# coincide today because the second is derived from the first, and the day they stop
# coinciding a shared alias would silently pick a side.
DealStatus = Literal["in_progress", "to_invoice", "closed"]


class DealPnl(BaseModel):
    """One deal's profit and loss, every figure computed by the service and returned
    already summed -- the browser adds nothing (§6).

    `accrued_value` is deliberately a separate field from `revenue` and not a variant of
    it: it is the estimate of §3 decision 2, it is **not revenue**, it enters no P&L row,
    and it lives under a heading of its own. On an `in_progress` deal it is the honest number
    to look at and the margin is provisional; the margin is only reportable in the
    `closed` state.
    """

    deal_id: UUID
    status: DealStatus
    revenue: Decimal
    direct_costs: Decimal
    work_cost: Decimal
    gross_margin: Decimal
    # `None`, never `0.00`, when revenue is zero: zero per cent means "everything I
    # earned went out in costs", a zero denominator means nothing has been earned. Two
    # different facts (§7.1, criterion 6).
    percentage_margin: Decimal | None
    total_hours: Decimal
    billable_uninvoiced_hours: Decimal
    accrued_value: Decimal
    hours_without_rate: int
    issued_invoices: int


# Which date puts an invoice's revenue in a period. `issue` is the recorded decision
# (§7.1: revenue is what was invoiced, by the document's own date) and the default; the
# fiscal reports never take the other. `accrual` reads the same revenue by the accrual
# period the document declares, `coalesce(competenza_da, data_emissione)`, because
# invoicing runs late and August's work issued in September must be readable in August
# (ORB-61; `docs/design/DECISIONS.md`, 2026-09-09). Costs and hours ignore it: each
# quantity is attributed by its own date whatever the base.
RevenueBase = Literal["issue", "accrual"]

# Which month a document's money belongs to in the cash view (ORB-133). `accrual` is
# the default Ivan asked for: every invoice, paid or not, and every draft or proforma
# sits in the month of the accrual period it declares, `coalesce(competenza_da,
# data_emissione)`, the same rule `RevenueBase` "competenza" uses. `incasso` is the
# reading the charts had before: a paid invoice by `payment_date`, an unpaid one by
# `due_date`, a draft or proforma by its own document date. Costs are by their own
# date under both. The fiscal estimate never takes this base: it is on the money.
CashBase = Literal["accrual", "collection"]


class PeriodPnlQuery(BaseModel):
    date_from: dt.date
    date_to: dt.date
    customer_id: UUID | None = None
    base: RevenueBase = "issue"
    # One legal entity's deals, costs and hours, or every legal entity's (REB-630, spec
    # 2026-10-03 §1.9). A cost without a deal is shared and counts only in «tutte».
    legal_entity_id: UUID | None = None


class PnlTotals(BaseModel):
    revenue: Decimal
    direct_costs: Decimal
    # `None` when the space has the `time` module off: there is no labour to count, and the
    # margin below is then revenue less the direct costs (`pnl.without_labour`).
    work_cost: Decimal | None
    gross_margin: Decimal
    percentage_margin: Decimal | None
    deal: int


class PeriodPnl(BaseModel):
    """Two columns, never one total (§7.4).

    Adding a finished job's margin to a half-done one produces a figure that is neither,
    and that changes every week for reasons which are not business performance. The
    reportable number is `closed`.
    """

    date_from: dt.date
    date_to: dt.date
    customer_id: UUID | None
    # Echoed from the query, so a reader of the figure knows which of the two readings
    # of revenue produced it (ORB-61) without keeping the request beside the response.
    base: RevenueBase
    # Echoed too (REB-630): `None` is every legal entity, which is what «tutte» shows.
    legal_entity_id: UUID | None = None
    closed: PnlTotals
    in_progress: PnlTotals
    # A cost with `deal_id IS NULL`: it enters the period P&L in a row of its own and is
    # **never apportioned** onto any deal. Every apportionment key has one precise and
    # unacceptable consequence -- a deal's margin would move when a *different* deal was
    # invoiced.
    general_expenses: Decimal
    # Added by slice 6 §5: the informative rows the economic dashboard shows under a
    # heading that is not "ricavi", and which enter no margin. Same names as `DealPnl`'s
    # because they are the same quantity on a different object; the scope lives in the
    # label ("nel periodo" here, "in totale" on the operational dashboard), never in the
    # field name, and §5 forbids the two ever being rendered side by side.
    #
    # One difference from `DealPnl` is deliberate and is recorded here because the names
    # are identical: `DealPnl.accrued_value` is `revenue + value of the unbilled hours`,
    # an estimate of what the deal will finally have been worth. There is no
    # such reading for a period -- a period's revenue is already its own reported row, and
    # adding it in again would put revenue under a heading that says "non sono ricavi".
    # So this one is the accrued value of the unbilled billable hours **alone**, which is
    # what the card that prints it is labelled: "Valore maturato non fatturato".
    # The hours are `None` when the space has the `time` module off; the value and the
    # rate-less count then come from the approved contract days alone.
    accrued_value: Decimal = Field(max_digits=12, decimal_places=2)
    billable_uninvoiced_hours: Decimal | None = Field(max_digits=8, decimal_places=2)
    # A count of entries, not a quantity of hours, exactly like `DealPnl.hours_without_rate`
    # -- and scoped to the same rows as the two figures above it, so the three can be
    # reconciled with each other on the card that shows them together.
    hours_without_rate: int
    # Whether the number can still move, which is the thing a reader most needs to know
    # and costs a COUNT over two columns that already exist (§6.4).
    closed_period: bool
    written_entries_overdue: int | None


class UnbilledBacklog(BaseModel):
    """What is waiting to be invoiced, with no period.

    Separate from `PeriodPnl`'s three same-named fields on purpose (§5): those are the
    period's figures, these are the total. Two different numbers with the same name is the
    fastest way to lose a reader's trust, so the *labels* carry the scope -- "nel periodo"
    on the economic dashboard, "in totale" on the operational one -- and the two are never
    rendered side by side.
    """

    # `None` when the space has the `time` module off: the hours are `time`'s, while the
    # approved contract days in `accrued_value` are `contracts`'.
    billable_uninvoiced_hours: Decimal | None = Field(max_digits=8, decimal_places=2)
    # `Σ ROUND(ore × tariffa_applicata, 2)` over `time_entries` -- slice 4 §7.3's
    # formula, computed here because §3 forbids `core/dashboard/` any multiplication at
    # all -- plus REB-372's own contribution from approved-or-later `work_units`, priced
    # against their own contract's rate card. It is **not** revenue and enters no
    # margin: the revenue is the invoice.
    accrued_value: Decimal = Field(max_digits=12, decimal_places=2)
    # A rate of zero and no rate are different facts (slice 4 §5.1); the same holds for
    # a `work_unit` day whose date has no rate card in force (REB-372). Every row here
    # contributes nothing to `accrued_value`; only the `time_entries` half also
    # contributes to `billable_uninvoiced_hours` -- a `work_unit`'s own quantity is
    # priced in whatever unit its rate card names, not always an hour.
    entries_without_rate: int
    entries: int


class BudgetQuery(BaseModel):
    date_from: dt.date
    date_to: dt.date
    customer_id: UUID | None = None
    # Mandatory pagination from the first commit (residual B3): the margins view is by
    # its nature a list of *closed* deals, so unbounded growth stops being invisible here.
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class BudgetVsActualRow(BaseModel):
    deal_id: UUID
    name: str
    budgeted_hours: Decimal | None
    actuals_hours: Decimal
    budgeted_value: Decimal | None
    revenue: Decimal
    # Per cent, two places: 40 hours of an estimated 100 is `"40.00"`.
    hours_progress: Decimal | None
    budget_pro_rata: Decimal | None
    value_variance: Decimal | None
    hours_variance: Decimal | None
    average_budgeted_rate: Decimal | None
    average_actual_rate: Decimal | None
    # An absent estimate is not an estimate of zero: the row says so, is excluded from
    # the budget aggregates, and is never counted as a 100% overrun (§9.2).
    unbudgeted: bool
    # `budgeted_value` set with `budgeted_hours` null: no progress figure exists
    # to derive a pro-rata from, so only the absolute comparison is shown rather than a
    # progress invented from the invoiced value -- which would be circular, because the
    # invoiced value is the very quantity being judged.
    pro_rata_not_calculable: bool


class BudgetPage(BaseModel):
    items: list[BudgetVsActualRow]
    next_cursor: UUID | None
    # Only over rows with both estimate columns populated: including the unestimated ones
    # would make the aggregate depend on how many deals nobody estimated.
    budgeted_total: Decimal
    revenue_total: Decimal
    budgeted_deals: int
    unbudgeted_deals: int


class FiscalEstimate(BaseModel):
    """§8. Declared an **estimate** in its own payload, not only in the UI copy: a
    labelled estimate is useful, an estimate presented as an actual is the original
    defect in a new form."""

    model_config = ConfigDict(frozen=True)

    year: int
    # The legal entity whose profile and invoices this was computed on (REB-630): an
    # estimate is one legal entity's, never the space's, and the card says whose.
    legal_entity_id: UUID
    estimate: Literal[True] = True
    warning: str
    revenue: Decimal
    profitability_coefficient: Decimal | None
    taxable_amount: Decimal | None
    substitute_tax_rate: Decimal | None
    substitute_tax: Decimal | None
    social_security_rate: Decimal | None
    contributions: Decimal | None
    estimated_net_income: Decimal | None
    # Substitute tax plus contributions: the one figure a person actually has to set
    # aside. Computed here, once, so no screen adds the two lines up on its own.
    due_total: Decimal | None = None
    # REB-974: what the figures were computed with. The pack the legal entity's profile
    # pointed at, its version and the year its law entry starts (`None` when the pack has no
    # law for the year, and then every figure above is `None`).
    pack_id: str | None = None
    pack_version: str | None = None
    law_from_year: int | None = None
    # `True` when these are the figures frozen when the year was computed, served because
    # the legal entity's pack no longer carries the estimate: the year keeps what it was.
    frozen: bool = False


class CashMonth(BaseModel):
    """One month of the cash view: what came in, what is still owed, what sits in
    drafts and proformas, what went out. Amounts are `total` (VAT included: money in
    the bank), costs are what each purchase costs (`cost_amount`).

    `quote` are the same four amounts as a share of the tallest month of their chart, in
    [0, 1], computed here: the browser scales a bar with them and never turns an amount
    string into a number (apps/web/src/test/no-browser-arithmetic.test.ts). `pila_*` are
    the heights of the two stacked columns as money (ORB-139), summed here for the same
    reason: what the cash chart stacks (`incassato + costi`) and what the projection
    stacks (all four). A column's height, not an income: the costs are inside it, which
    is why the field is not called a total and `projected` on `CashOverview` excludes
    them. The chart prints it above a stacked month, where the figure has to match the
    bar a reader measures."""

    year: int
    month: int
    paid: Decimal = Field(max_digits=12, decimal_places=2)
    unpaid: Decimal = Field(max_digits=12, decimal_places=2)
    drafts: Decimal = Field(max_digits=12, decimal_places=2)
    costs: Decimal = Field(max_digits=12, decimal_places=2)
    trend_stack: Decimal = Field(
        max_digits=12,
        decimal_places=2,
        description=(
            "Height of the trend column as an amount: collected + passive costs. It is not "
            "revenue: the costs are inside."
        ),
    )
    projection_stack: Decimal = Field(
        max_digits=12,
        decimal_places=2,
        description=(
            "Height of the projection column as an amount: collected + to collect + "
            "drafts/proformas + passive costs. It is not revenue: the costs are inside."
        ),
    )
    trend_shares: dict[str, float]
    projection_shares: dict[str, float]


class ContractDateMarker(BaseModel):
    """One contract-specific date on the cash calendar (REB-352 §1.6): an
    irrevocability window closing or a renewal deadline, layered onto the month it
    falls in. Neither is a sum and neither moves a `CashMonth` figure -- a marker
    only names a day and which contract it belongs to, the way mastro's own cash
    calendar overlays "irrevocability window ends" and "renewal dates" onto its
    chart without folding either into the chart's own bars
    (`2026-08-23-analysis-section-design.md:205-219`).
    """

    contract_id: UUID
    title: str
    customer_id: UUID
    type: Literal["irrevocability_end", "renewal_due"]
    date: dt.date


class CashOverview(BaseModel):
    """The year as money: `paid` is invoices paid, `unpaid` invoices issued
    and unpaid, `drafts` drafts and proformas not yet turned into invoices, `costs` what
    was spent. Which month each document falls in is `base` (`CashBase`): by the
    accrual period it declares, or by the money's own dates. `projected` is the first
    three added up -- what the year would collect if everything issued and drafted came
    in -- and the two `lordo` figures are income less costs, actual and projected."""

    year: int
    # Echoed from the request, so the page can label the reading it shows (ORB-133).
    base: CashBase
    # Echoed as well (REB-630): one legal entity's cash, or `None` for every legal entity added
    # up, which is the sum «tutte» exists to show.
    legal_entity_id: UUID | None = None
    paid: Decimal = Field(max_digits=12, decimal_places=2)
    unpaid: Decimal = Field(max_digits=12, decimal_places=2)
    drafts: Decimal = Field(max_digits=12, decimal_places=2)
    projected: Decimal = Field(max_digits=12, decimal_places=2)
    costs: Decimal = Field(max_digits=12, decimal_places=2)
    actual_gross: Decimal = Field(max_digits=12, decimal_places=2)
    projected_gross: Decimal = Field(max_digits=12, decimal_places=2)
    months: list[CashMonth]
    # REB-352 §1.6's overlay: every irrevocability-window close and renewal
    # deadline that falls inside `year`, across every contract -- computed "as of"
    # today regardless of which `year` is on screen (an irrevocability window is
    # always a forward-looking promise), so a marker on a past year's calendar
    # never appears and a marker on a future one only does once today's window
    # actually reaches into it.
    contractual_due_dates: list[ContractDateMarker]


class CeilingStatusRead(BaseModel):
    """One ceiling of the configured jurisdiction pack, evaluated against `year`'s
    real paid revenue -- REB-352 §1.4's headroom figure, the reader-facing shape of
    `fiscal.ceiling.evaluate_ceiling`'s own output. `remaining` (`soglia - ricavi`) is
    the "one number a ceiling exists to produce" mastro's own audit named as
    computed nowhere until REB-361 added `evaluate_ceiling`; this class only
    exposes it, and adds no arithmetic of its own.
    """

    id: str
    label: str
    threshold: Decimal = Field(max_digits=12, decimal_places=2)
    consequence: CeilingConsequence
    revenue: Decimal = Field(max_digits=12, decimal_places=2)
    remaining: Decimal = Field(max_digits=12, decimal_places=2)
    exceeded: bool
    alert_level: str | None
    # `ricavi / soglia`: `0.7125` is 71.25% of the ceiling used, above `1` once crossed.
    # Sent so a progress bar is drawn from a figure the server computed.
    usage: float


class CeilingHeadroom(BaseModel):
    """Every active ceiling of the fiscal profile's own pack, for one calendar
    year -- `evaluate_pack`'s own list, unmodified."""

    year: int
    # The legal entity whose pack these ceilings belong to (REB-630).
    legal_entity_id: UUID
    pack_id: str
    pack_version: str
    thresholds: list[CeilingStatusRead]


class CeilingSimulationQuery(BaseModel):
    """What the «does it fit?» simulator adds to the year, in one of two shapes, never both
    (`ValidationFailed` otherwise).

    **Raw**: a deal not yet saved, in the shape its three columns already carry
    (`deals/schemas.py`'s own `DealCreate`): `budgeted_value` is the addition directly when
    typed; with only `budgeted_hours` and `hourly_rate` set, the service derives it as their
    product. The question can be asked before the deal is ever saved, which is why this path
    stays.

    **`deal_id`** (REB-931): a saved deal, read through its value lines instead of typed
    again. The addition to the simulated year is the deal's share in that calendar year,
    that is the occurrences of its lines dated in it (`deals.value.share_in`), so a monthly
    fee starting in October adds three months and an annual one its one payment, where the
    raw figure could only say «all of it». A line with no start follows the deal's
    `expected_close_date`, else `expected_date`, else today.

    `contract_id` (REB-761) names a contract whose cap on its customer's share the same
    addition is also read against, over the contract's own anniversary year containing
    `expected_date` (the day the money is expected, today when absent): the addition goes
    to that contract's customer. With `deal_id` the addition read against the cap is the
    deal's share in that anniversary year, not in the calendar one. The pack's ceilings are
    asked either way."""

    deal_id: UUID | None = None
    budgeted_hours: Decimal | None = Field(
        default=None,
        max_digits=CEILING_HOURS_MAX_DIGITS,
        decimal_places=CEILING_DECIMAL_PLACES,
        ge=0,
    )
    budgeted_value: Decimal | None = Field(
        default=None,
        max_digits=CEILING_VALUE_MAX_DIGITS,
        decimal_places=CEILING_DECIMAL_PLACES,
        ge=0,
    )
    hourly_rate: Decimal | None = Field(
        default=None,
        max_digits=CEILING_FACTOR_MAX_DIGITS,
        decimal_places=CEILING_FACTOR_DECIMAL_PLACES,
        ge=0,
    )
    contract_id: UUID | None = None
    expected_date: dt.date | None = None


class CeilingSimulationResult(BaseModel):
    """One ceiling, before and after the synthetic addition -- `rientra` is
    "would this fit?" itself: the addition does not push this ceiling's own
    revenue to or past its threshold."""

    id: str
    label: str
    threshold: Decimal = Field(max_digits=12, decimal_places=2)
    consequence: CeilingConsequence
    current_revenue: Decimal = Field(max_digits=12, decimal_places=2)
    current_remaining: Decimal = Field(max_digits=12, decimal_places=2)
    # `CeilingStatusRead.usage`, before and after the synthetic addition.
    current_usage: float
    simulated_usage: float
    simulated_revenue: Decimal = Field(max_digits=12, decimal_places=2)
    simulated_remaining: Decimal = Field(max_digits=12, decimal_places=2)
    falls_within: bool
    alert_level_simulated: str | None


class CeilingSimulation(BaseModel):
    year: int
    legal_entity_id: UUID
    pack_id: str
    pack_version: str
    brief_addition: Decimal = Field(max_digits=12, decimal_places=2)
    thresholds: list[CeilingSimulationResult]
    # The cap of the contract the request named, read against the same addition; `None`
    # when it named none (REB-761).
    contract_cap: ContractCapSimulation | None = None


class YearTotals(BaseModel):
    """The register of one legal entity's year, summed. `taxable_amount` is `annual_revenue`'s
    figure over the same invoices, and `social_security_surcharge` is already inside it: the social
    security surcharge line
    is a line of the invoice like any other (REB-352 §2)."""

    invoices: int
    taxable_amount: Decimal = Field(max_digits=12, decimal_places=2)
    social_security_surcharge: Decimal = Field(max_digits=12, decimal_places=2)
    stamp_duty: Decimal = Field(max_digits=12, decimal_places=2)
    total: Decimal = Field(max_digits=12, decimal_places=2)


class YearRegime(BaseModel):
    """Revenue of the year under one regime, read from what each invoice froze at issue
    (`snapshot.tax`), never from today's profile: a legal entity that changed regime in
    the year has two rows, and each invoice is counted under the one it was issued in."""

    regime_code: str | None
    pack_id: str
    invoices: int
    revenue: Decimal = Field(max_digits=12, decimal_places=2)


class YearTreatment(BaseModel):
    """Revenue of the year under one tax treatment: a rate, its `Natura` and the legal
    reference printed beside it, exactly as the lines carry them."""

    vat_rate: Decimal = Field(max_digits=5, decimal_places=2)
    vat_nature: str | None
    legal_reference: str | None
    lines: int
    revenue: Decimal = Field(max_digits=12, decimal_places=2)


class YearRegisterRow(BaseModel):
    """One issued invoice of the register. `legal_name` and `vat_number` are the
    customer's as they were at issue (the snapshot), the way the document printed them."""

    invoice_id: UUID
    year: int
    number: int
    issue_date: dt.date
    customer_id: UUID
    legal_name: str
    vat_number: str | None
    sdi_document_type: str
    taxable_amount: Decimal = Field(max_digits=12, decimal_places=2)
    social_security_surcharge: Decimal = Field(max_digits=12, decimal_places=2)
    stamp_duty: Decimal = Field(max_digits=12, decimal_places=2)
    total: Decimal = Field(max_digits=12, decimal_places=2)
    # The sentence the invoice itself prints for its stamp duty, or `None` without one.
    stamp_duty_declaration: str | None
    payment_status: str
    payment_date: dt.date | None


class YearOverview(BaseModel):
    """The year of one legal entity as the accountant reads it: what was issued, under which
    regime and tax treatment, with the stamp duty and the social security surcharge kept apart.

    Scoped by the register year (`Invoice.year`), the same `invoice`/`issued` universe
    `annual_revenue` sums, so `totali.taxable_amount` is the revenue figure of the year and
    the CSV's total is this one. `cancelled` counts the invoices struck from the register
    in the year: they keep their number and are listed in none of the figures.
    """

    year: int
    legal_entity_id: UUID
    legal_entity_name: str
    total: YearTotals
    regimes: list[YearRegime]
    treatments: list[YearTreatment]
    register: list[YearRegisterRow]
    cancelled: int


class RevenueByCustomer(BaseModel):
    """One customer's share of the year's invoiced revenue -- the concentration figure
    REB-352's own mapping calls for (§1.5, §5 item 1 of
    `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`):
    `Σ Invoice.imponibile` for this customer over the calendar year, divided by that
    same year's `annual_revenue`.

    Not `ExposureCustomer` (`dashboard/schemas.py`), whose `share` is a share of the
    *largest* customer's outstanding receivable and answers "who currently owes the
    most". This answers a different question -- "what share of total invoiced income
    comes from this one client" -- against the whole year's revenue, and the two do not
    become the same figure by relabelling either one.
    """

    customer_id: UUID
    legal_name: str
    revenue: Decimal = Field(max_digits=12, decimal_places=2)
    invoices: int
    share: float
    # The legal entity whose revenue `share` is a share of (REB-630, spec 2026-10-03 §1.9):
    # a share of revenue is a share of one legal entity's revenue, so in «tutte» the rows
    # come grouped by it and a customer billed by two legal entities appears twice, once per
    # share. With one legal entity every row carries the same id and nothing changes.
    legal_entity_id: UUID


class EconomicOverview(BaseModel):
    """Economic settings of the dashboard: the year's cash and, for an admin with a
    configured fiscal profile, the tax estimate computed twice -- on collected revenue
    and on projected revenue -- with the nets that follow. For anyone who is not an
    admin, or has no profile, the fiscal part is `None` and the page shows only the cash."""

    calculated_at: datetime
    # The legal entity the whole answer speaks for, or `None` for every legal entity (REB-630).
    # In that case the fiscal block below is empty when the space has several, because
    # two regimes' taxes do not add: the page then asks `/api/analytics/fiscal` once per
    # legal entity instead (spec 2026-10-03 §1.9).
    legal_entity_id: UUID | None = None
    pension_fund: CashOverview
    tax: FiscalEstimate | None
    projected_tax: FiscalEstimate | None
    # Whole-practice, calendar-year concentration (§1.5): not blocked on the ledger
    # milestone the way a contract-anchored cap is, and distinct from `by_customer`'s
    # receivables exposure on the dashboard's own page (`dashboard/schemas.py`).
    customer_concentration: list[RevenueByCustomer]
    actual_net: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    projected_net: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)


class BindTimeRequest(BaseModel):
    """Which hours become invoice lines. `entry_ids` explicit rather than "everything
    billable": choosing *which* hours to invoice is a commercial decision, and a default
    of "all of them" is that decision made silently."""

    entry_ids: list[UUID] = Field(min_length=1)
    group_by_month: bool = True


class DaysStateShare(BaseModel):
    """One state's days in one month. `share` is a share of the tallest month's total in
    [0, 1], computed here so the browser draws a bar and never adds a figure."""

    status: str
    days: Decimal = Field(max_digits=10, decimal_places=2)
    share: float


class DaysMonth(BaseModel):
    """One calendar month of the days analysis.

    `register` is the days that really happened (worked, worked without approval,
    invoiced, paid, disputed, written off); `media_previous` is the mean of the three
    months before this one, a quiet month counting as zero. `complete` is false for a
    month that has not yet ended. `without_approval` is the days (by quantity, so a half day
    is a half) that slipped into «lavorato senza approvazione» in the month, by the moment
    of the transition, so a day since regularised still counts: it is history, not a figure
    about the present.
    """

    month: str
    complete: bool
    by_status: list[DaysStateShare]
    total: Decimal = Field(max_digits=10, decimal_places=2)
    total_share: float
    register: Decimal = Field(max_digits=10, decimal_places=2)
    register_share: float
    media_previous: Decimal = Field(max_digits=10, decimal_places=2)
    without_approval: Decimal = Field(max_digits=10, decimal_places=2)
    share_without_approval: float


class DaysRate(BaseModel):
    """The realised day rate of one customer or one contract against the rate card.

    `actual_rate` is what the invoice lines billed over the days they bill, `None` when
    nothing was invoiced. `nominal_rate` is the rate card's own price over the days a
    card covers, `None` when no billed day is covered; `days_without_list_price` are the days
    no card covered, reported on their own and never priced at zero.
    """

    id: UUID
    label: str
    invoiced_days: Decimal = Field(max_digits=10, decimal_places=2)
    invoiced_amount: Decimal = Field(max_digits=12, decimal_places=2)
    actual_rate: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    days_without_list_price: Decimal = Field(max_digits=10, decimal_places=2)
    nominal_rate: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)


class DaysAnalysis(BaseModel):
    """The days of a period: by month and state, the realised rate, and the trend of days
    worked without the approval the contract asks for. The period is echoed back, rounded
    out to whole months for the monthly figures; the rate reads the exact `da`..`a`."""

    date_from: dt.date
    date_to: dt.date
    legal_entity_id: UUID | None
    today: dt.date
    months: list[DaysMonth]
    last_complete_month: str | None
    days_in_period: Decimal = Field(max_digits=10, decimal_places=2)
    invoiced_days: Decimal = Field(max_digits=10, decimal_places=2)
    actual_rate: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    by_customer: list[DaysRate]
    by_contract: list[DaysRate]
    without_approval_in_period: Decimal = Field(max_digits=10, decimal_places=2)
    without_approval_always: Decimal = Field(max_digits=10, decimal_places=2)


__all__ = [
    "BindTimeRequest",
    "BudgetPage",
    "BudgetQuery",
    "BudgetVsActualRow",
    "DealPnl",
    "DealStatus",
    "FiscalEstimate",
    "PeriodPnl",
    "PeriodPnlQuery",
    "PnlTotals",
    "UnbilledBacklog",
]
