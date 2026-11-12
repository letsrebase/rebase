from collections.abc import Callable
from collections.abc import Set as AbstractSet
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics import budget, cash, pnl, year_overview
from pigrocrm.core.analytics.days import monthly_figures, months_between, realised_rates
from pigrocrm.core.analytics.fiscal import estimate_income
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import (
    BudgetPage,
    BudgetQuery,
    CashBase,
    CashOverview,
    CeilingHeadroom,
    CeilingSimulation,
    CeilingSimulationQuery,
    CeilingSimulationResult,
    CeilingStatusRead,
    DaysAnalysis,
    DealPnl,
    EconomicOverview,
    FiscalEstimate,
    PeriodPnl,
    PeriodPnlQuery,
    RevenueByCustomer,
    TaxCalendar,
    TaxCalendarEntry,
    UnbilledBacklog,
    YearOverview,
)
from pigrocrm.core.analytics.year_csv import render_year_csv, year_csv_filename
from pigrocrm.core.config import get_settings
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.db import today_local
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.deals.value import ValueTerm, line_term, share_in
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.basis import year_basis
from pigrocrm.core.fiscal.basis_schemas import YearBasis
from pigrocrm.core.fiscal.calendar import tax_calendar
from pigrocrm.core.fiscal.ceiling import (
    CeilingStatus,
    evaluate_ceiling,
    evaluate_pack,
)
from pigrocrm.core.fiscal.company_estimate import estimate_company_taxes, irap_region_key
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService, reading_for_year
from pigrocrm.core.fiscal.income_facts import read_income_variations
from pigrocrm.core.fiscal.inps_paid import inps_paid_in_year
from pigrocrm.core.fiscal.irap_sites import SiteFacts, SitePlace, splits
from pigrocrm.core.fiscal.law import DeclaredTax, TaxYearLaw
from pigrocrm.core.fiscal.law_inps import InpsCategoryName, TradeGestione
from pigrocrm.core.fiscal.law_irap import IrapActivity
from pigrocrm.core.fiscal.loss_carry import loss_offset_for
from pigrocrm.core.fiscal.losses import TaxLossService
from pigrocrm.core.fiscal.ordinary_estimate import (
    ORDINARY_WARNING,
    InpsFacts,
    OrdinaryEstimate,
    estimate_ordinary_taxes,
)
from pigrocrm.core.fiscal.pack import FiscalPack, resolve_pack
from pigrocrm.core.fiscal.readings import build_tax_readings
from pigrocrm.core.fiscal.repository import (
    FiscalEstimateRepository,
    TaxAdvanceMethodRepository,
)
from pigrocrm.core.fiscal.schemas import (
    COMPANY_PARAMETERS,
    TAXPAYER_PARAMETERS,
    DeclaredTaxFigureRead,
    FiscalProfileRead,
    TaxReadings,
)
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.fiscal.withholdings_suffered import (
    company_withholdings_suffered,
    withholdings_suffered,
)
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity, LegalEntitySite
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.money import ZERO_MONEY, round_money, share
from pigrocrm.core.places.locate import Place, locate
from pigrocrm.core.staff.payroll import PayrollService

ENTITY = "analytics"
# What a frozen estimate keeps of the profile (REB-974): the taxpayer's parameters, the
# ones the figures were computed from or will be, and the regime they belong to. The pack
# and its version are columns of the row, and the law year too.
FROZEN_PARAMETERS = {
    "regime_code",
    "profitability_coefficient",
    "substitute_tax_rate",
    "social_security_rate",
    "inps_category",
    "isa",
    *TAXPAYER_PARAMETERS,
    *COMPANY_PARAMETERS,
}
MAX_DAYS_MONTHS = 120
# The same floor the year routes have, and a lookback of three months before `da` that
# `date` can always represent.
MIN_DAYS_YEAR = 2000


def _ceiling_usage(revenue: Decimal, threshold: Decimal) -> float:
    """How much of `threshold` is used, as a ratio (`1.0` is the ceiling itself, above it
    once crossed): the one number a progress bar needs, computed here because the
    browser adds and divides nothing (§6)."""
    return round(share(revenue, threshold), 4)


def _ceiling_status_read(status: CeilingStatus) -> CeilingStatusRead:
    return CeilingStatusRead(
        id=status.ceiling.id,
        label=status.ceiling.label,
        threshold=status.ceiling.threshold,
        consequence=status.ceiling.consequence,
        revenue=status.revenue,
        remaining=status.remaining,
        exceeded=status.exceeded,
        alert_level=status.alert_level,
        usage=_ceiling_usage(status.revenue, status.ceiling.threshold),
    )


def _synthetic_addition(query: CeilingSimulationQuery) -> Decimal:
    """The one euro figure REB-352 §1.4's simulator adds to `revenue` before
    re-evaluating each ceiling -- the deal's own value estimate when one was typed
    directly, or hours times rate when only those two were, mirroring
    `budget_vs_actual`'s own reading of the first two columns and extending it by
    the third (`Deal.hourly_rate`) for the deal that has not priced a value yet.
    """
    if query.budgeted_value is not None:
        return query.budgeted_value
    if query.budgeted_hours is not None and query.hourly_rate is not None:
        return round_money(query.budgeted_hours * query.hourly_rate)
    raise ValidationFailed(
        ENTITY,
        "budgeted_value",
        "serve una stima per simulare l'aggiunta",
        expected="budgeted_value, oppure budgeted_hours insieme a hourly_rate, oppure deal_id",
    )


def _raw_fields_given(query: CeilingSimulationQuery) -> list[str]:
    return [
        name
        for name in ("budgeted_value", "budgeted_hours", "hourly_rate")
        if getattr(query, name) is not None
    ]


def _addition_over_period(terms: list[ValueTerm]) -> Callable[[date, date], Decimal]:
    """A deal's value as a function of the window it is read over: the share of its terms
    dated in it."""

    def addition(date_from: date, date_to: date) -> Decimal:
        return share_in(terms, date_from, date_to)

    return addition


class AnalyticsService:
    """Reads only, with one exception: the facade over the figure modules of this package
    (`pnl`, `budget`, `cash`, `year_overview`, `days`). `freeze_fiscal_estimates` keeps the
    years' estimates just before a legal entity changes pack, so a year keeps its figures
    after the move (REB-974). The writer that binds hours to a draft invoice lives in
    `timetracking.billing`.

    Every figure is computed here and returned already summed. §6 forbids the frontend of
    this slice from computing any economic total at all -- the previous system's whole P&L lived in
    `App.jsx`, with three fiscal constants and float hour sums, and moving it here is the
    final payment on the debt slice 1 §2.2 cited as the empirical justification for this
    architecture.

    No `require_write` and no `require_admin` anywhere in the read methods: a `readonly`
    actor is entitled to every figure in this file. The P&L of a deal is not more
    sensitive than the deal, the hours and the invoices it is derived from, each of which
    a reader can already list.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = AnalyticsRepository(session)
        self.contracts = ContractRepository(session)

    def deal_pnl(self, deal_id: UUID, actor: Actor) -> DealPnl:
        """The P&L rows of one deal (`pnl.deal_pnl`)."""
        return pnl.deal_pnl(self.session, self.repo, deal_id, actor)

    def period_pnl(
        self, query: PeriodPnlQuery, actor: Actor, modules: AbstractSet[str] | None = None
    ) -> PeriodPnl:
        """The P&L of a window in two columns, closed and in-progress deals
        (`pnl.period_pnl`). `modules` is what the space has on (`None`: everything): without
        `time` the labour and the hours are left out, and without `contracts` the approved
        contract days (`pnl.period_pnl`)."""
        return pnl.period_pnl(self.session, self.repo, query, actor, modules)

    def unbilled_backlog(
        self,
        actor: Actor,
        legal_entity_id: UUID | None = None,
        modules: AbstractSet[str] | None = None,
    ) -> UnbilledBacklog:
        """The arrears: billable hours not yet on the line of an issued invoice, with no
        period.

        `period_pnl` returns the same quantities *for a period*; this one has none, because
        "how much do I have to invoice" is not a question about March (§6.3). It lives on
        this service and not on a dashboard module because its euro value is
        `Σ ROUND(ore × tariffa_applicata, 2)` -- a product of two columns, which §3 forbids
        `core/dashboard/` from containing at all.

        No authorisation check beyond what every other read on this service does: slice 4
        §11 gives every analytics read to every role, and the one admin-only figure -- the
        fiscal estimate -- is a different method entirely. `actor` is therefore accepted
        and unused, which is the shape every read here has; a method that quietly dropped
        the parameter would be the one that is hard to add a check to later.

        It has an MCP tool (`get_unbilled_backlog`, §11.1), so slice 4 §11's exclusion list
        does not grow.

        `modules` is what the space has on (`None`: everything). The hours are `time`'s and
        the approved contract days `contracts`': each half is left out when its module is
        off, and the hours are `None` without `time`.
        """
        hours_on = modules is None or "time" in modules
        days_on = modules is None or "contracts" in modules
        hours, value, without_rate, entries = self.repo.unbilled_backlog(
            legal_entity_id=legal_entity_id, hours=hours_on, contract_days=days_on
        )
        return UnbilledBacklog(
            billable_uninvoiced_hours=hours if hours_on else None,
            accrued_value=value,
            entries_without_rate=without_rate,
            entries=entries,
        )

    def budget_vs_actual(self, query: BudgetQuery, actor: Actor) -> BudgetPage:
        """Estimate against actual, per deal, keyset-paged (`budget.budget_vs_actual`)."""
        return budget.budget_vs_actual(self.session, self.repo, query)

    def ceiling_headroom(
        self, year: int, actor: Actor, legal_entity_id: UUID | None = None
    ) -> CeilingHeadroom:
        """REB-352 §1.4: every active ceiling of the configured jurisdiction pack,
        evaluated against `year`'s real paid revenue -- `evaluate_pack`'s own output,
        read rather than recomputed (no new revenue query, per REB-352 §1.4's own
        reasoning). Raises `NotFound("fiscal_profile", ...)` when nobody has
        configured one yet, the same signal `get_fiscal_estimate` gives: there is no
        pack to resolve without a profile to point at one.

        Not admin-only, unlike `get_fiscal_estimate`: this is a revenue-versus-
        threshold figure over the same `Invoice` rows `cash_overview` and
        `budget_vs_actual` already open to every role, not the taxable-income
        computation that method alone gates.

        One legal entity's, always (REB-630, spec 2026-10-03 §1.9): a ceiling exists on the
        pack one legal entity's profile points at, and its receipts are that legal entity's
        alone. `legal_entity_id` resolves to the only legal entity when the caller names none,
        so a one-legal entity space reads what it always read; with several it is required,
        and `require_single` says so naming the field.
        """
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        profile = FiscalProfileService(self.session).get(actor, legal_entity.id)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        return CeilingHeadroom(
            year=year,
            legal_entity_id=legal_entity.id,
            pack_id=pack.id,
            pack_version=pack.version,
            thresholds=[
                _ceiling_status_read(status)
                for status in evaluate_pack(pack, self.session, year, legal_entity.id)
            ],
        )

    def simulate_ceiling(
        self,
        year: int,
        query: CeilingSimulationQuery,
        actor: Actor,
        legal_entity_id: UUID | None = None,
    ) -> CeilingSimulation:
        """REB-352 §1.4's "would this fit?" simulator: the same active ceilings,
        each re-evaluated with a synthetic addition on top of the real, already-
        summed `revenue` -- `evaluate_ceiling`'s own pure half, exactly so this needs
        no second revenue query (`fiscal/ceiling.py::evaluate_ceiling`'s own
        docstring names this simulator by name). The addition is a not-yet-won
        deal's own estimate, read raw off its own three columns rather than by
        `deal_id`, so nothing has to be saved to ask the question. The legal entity
        resolves as in `ceiling_headroom` (REB-630).

        With a `deal_id` (REB-931) the addition is read off the saved deal's value lines
        instead: the share of them dated in `year`, so a fee that starts in October adds the
        months left of the year and an annual one its single payment. The raw fields and
        `deal_id` are two ways to ask the same question and are refused together.

        With a `contract_id` (REB-761) the same addition is also read against that
        contract's cap on its customer's share, in `contract_cap`: a clause of the
        contract and not of the pack, so it is answered whatever the regime, and the
        contract has to be of the legal entity the simulation is for.
        """
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        profile = FiscalProfileService(self.session).get(actor, legal_entity.id)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        addition_in: Callable[[date, date], Decimal] | None = None
        if query.deal_id is None:
            addition = _synthetic_addition(query)
        else:
            addition_in = self._deal_addition(query.deal_id, query, legal_entity.id)
            addition = addition_in(date(year, 1, 1), date(year, 12, 31))
        results: list[CeilingSimulationResult] = []
        for before in evaluate_pack(pack, self.session, year, legal_entity.id):
            after = evaluate_ceiling(before.ceiling, before.revenue + addition)
            results.append(
                CeilingSimulationResult(
                    id=before.ceiling.id,
                    label=before.ceiling.label,
                    threshold=before.ceiling.threshold,
                    consequence=before.ceiling.consequence,
                    current_revenue=before.revenue,
                    current_remaining=before.remaining,
                    current_usage=_ceiling_usage(before.revenue, before.ceiling.threshold),
                    simulated_usage=_ceiling_usage(after.revenue, after.ceiling.threshold),
                    simulated_revenue=after.revenue,
                    simulated_remaining=after.remaining,
                    falls_within=not after.exceeded,
                    alert_level_simulated=after.alert_level,
                )
            )
        cap = None
        if query.contract_id is not None:
            contract = ContractService(self.session).get(query.contract_id, actor)
            if contract.legal_entity_id != legal_entity.id:
                raise ValidationFailed(
                    ENTITY,
                    "contract_id",
                    "il contratto è di un'altra azienda",
                    expected=f"un contratto di {legal_entity.name}",
                )
            cap = ContractService(self.session).simulate_cap(
                query.contract_id, addition_in or addition, query.expected_date, actor
            )
        return CeilingSimulation(
            year=year,
            legal_entity_id=legal_entity.id,
            pack_id=pack.id,
            pack_version=pack.version,
            brief_addition=addition,
            thresholds=results,
            contract_cap=cap,
        )

    def _deal_addition(
        self, deal_id: UUID, query: CeilingSimulationQuery, legal_entity_id: UUID
    ) -> Callable[[date, date], Decimal]:
        """The saved deal `deal_id` names, as a function of the window its value is read
        over. Refuses, naming the field, a query that also carries the raw estimate, a deal
        of another legal entity, and a deal with no lines (it has no value to add)."""
        given = _raw_fields_given(query)
        if given:
            raise ValidationFailed(
                ENTITY,
                "deal_id",
                f"deal_id e {', '.join(given)} sono due modi di porre la stessa domanda",
                expected="deal_id da solo, oppure la stima a mano",
            )
        deals = DealRepository(self.session)
        deal = deals.get(deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)
        if deal.legal_entity_id != legal_entity_id:
            raise ValidationFailed(
                ENTITY,
                "deal_id",
                "il deal è di un'altra azienda",
                expected="un deal della stessa azienda della simulazione",
            )
        lines = deals.value_lines([deal.id]).get(deal.id, [])
        if not lines:
            raise ValidationFailed(
                ENTITY,
                "deal_id",
                "il deal non ha righe di valore da aggiungere",
                expected="un deal con almeno una riga di valore, oppure la stima a mano",
            )
        fallback = deal.expected_close_date or query.expected_date or today_local()
        return _addition_over_period([line_term(line, fallback) for line in lines])

    def year_overview(
        self, year: int, actor: Actor, legal_entity_id: UUID | None = None
    ) -> YearOverview:
        """REB-757: the year of one legal entity as an accountant reads it, from the register
        alone: what was issued, under which regime and tax treatment, with the stamp duty
        and the social security surcharge apart. A read of the same invoices `annual_revenue` sums
        (`Invoice.year`, `invoice`, `issued`), so `totali.taxable_amount` is that figure and
        the CSV of the year (`year_csv.render_year_csv`) totals to it.

        Open to every role, like the register itself, and one legal entity's always: a register
        is numbered per legal entity, so `legal_entity_id` resolves to the only legal entity when
        the
        caller names none and is required, by name, when the space has several.
        """
        return self._year(year, legal_entity_id)[0]

    def days_analysis(
        self,
        actor: Actor,
        date_from: date | None = None,
        date_to: date | None = None,
        legal_entity_id: UUID | None = None,
    ) -> DaysAnalysis:
        """REB-756: the days of a period by month and state, the realised day rate by
        customer and by contract, and the trend of days worked without approval.

        Both bounds or neither, as every dashboard period is: with neither, the current
        month. Open to every role, like the days themselves, and `legal_entity_id` one more
        predicate, omitted for «tutte». The monthly figures are whole calendar months
        (a bar is a month); the rate reads the exact `da`..`a`. At most ten years, the
        dashboard's own ceiling.
        """
        if (date_from is None) != (date_to is None):
            raise ValidationFailed(
                "period",
                "from" if date_from is None else "to",
                "il periodo richiede entrambe le date, o nessuna",
                expected="da e a insieme, oppure nessuna delle due",
            )
        today = today_local()
        if date_from is None or date_to is None:
            date_from, date_to = month_bounds(today.year, today.month)
        if date_from > date_to:
            raise ValidationFailed(
                "period",
                "from",
                "la data iniziale è successiva a quella finale",
                expected=f"da <= {date_to.isoformat()}",
            )
        if date_from.year < MIN_DAYS_YEAR:
            raise ValidationFailed(
                "period",
                "from",
                f"il periodo non può cominciare prima del {MIN_DAYS_YEAR}",
                expected=f"da >= {MIN_DAYS_YEAR}-01-01",
            )
        if len(months_between(date_from, date_to)) > MAX_DAYS_MONTHS:
            raise ValidationFailed(
                "period",
                "to",
                f"il periodo non può superare {MAX_DAYS_MONTHS} mesi",
                expected=f"un periodo di al più {MAX_DAYS_MONTHS} mesi",
            )
        months, last, days, in_period, always = monthly_figures(
            self.session, date_from, date_to, legal_entity_id, today, get_settings().timezone
        )
        by_customer, by_contract, invoiced_days, rate = realised_rates(
            self.session, date_from, date_to, legal_entity_id
        )
        return DaysAnalysis(
            date_from=date_from,
            date_to=date_to,
            legal_entity_id=legal_entity_id,
            today=today,
            months=months,
            last_complete_month=last,
            days_in_period=days,
            invoiced_days=invoiced_days,
            actual_rate=rate,
            by_customer=by_customer,
            by_contract=by_contract,
            without_approval_in_period=in_period,
            without_approval_always=always,
        )

    def build_year_csv(
        self, year: int, actor: Actor, legal_entity_id: UUID | None = None
    ) -> tuple[str, bytes]:
        """REB-757: the year of one legal entity as the file an accountant opens in a
        spreadsheet, `(filename, content)`: one row per invoice line, then the register,
        each with its total (`year_csv.render_year_csv`). The same invoices, in the same
        order, as `year_overview`, so the file's total is the page's."""
        overview, lines = self._year(year, legal_entity_id)
        content = render_year_csv(overview, lines)
        return year_csv_filename(overview), content.encode("utf-8")

    def _year(
        self, year: int, legal_entity_id: UUID | None
    ) -> tuple[YearOverview, dict[UUID, list[InvoiceLine]]]:
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        return year_overview.build_year(self.repo, legal_entity, year)

    def cash_overview(
        self,
        year: int,
        actor: Actor,
        base: CashBase = "accrual",
        legal_entity_id: UUID | None = None,
    ) -> CashOverview:
        """The year as cash, month by month (`cash.cash_overview`). Read by anyone who
        may read the dashboard: nothing here is fiscal."""
        actor.require_agent_allowed("cash_overview")
        return cash.cash_overview(self.repo, self.contracts, year, base, legal_entity_id)

    def economic_overview(
        self,
        year: int,
        actor: Actor,
        base: CashBase = "accrual",
        legal_entity_id: UUID | None = None,
        modules: AbstractSet[str] | None = None,
    ) -> EconomicOverview:
        """The economic tab of the dashboard, in one answer: the cash view for everyone,
        and for an admin with a fiscal profile the estimate on what was collected and on
        what is projected, with the two nets. **No MCP tool** -- it carries the fiscal
        estimate, and `get_fiscal_estimate`'s reasons apply unchanged.

        `base` moves the charts and the cash cards only. The fiscal block never follows it: `tax`
        is `get_fiscal_estimate` itself, on the one basis the regime's law names for the year
        (`fiscal/basis.py`, REB-1071), so a chart that reads by accrual period must not move the
        taxes and the three surfaces answer the same figure. `projected_tax` is a «what if» on
        the projected revenue, which is no year's basis.

        Under `accrual`, the default, the cash view is read twice for the two nets and the
        projection: eight aggregates instead of four, on a page that already runs a dozen.
        Accepted, rather than threading two readings through `cash_overview`, whose one job is
        one reading.

        `customer_concentration` (§1.5, REB-370) is on no reading switch: it is
        `revenue_by_customer(year)` verbatim, a whole-practice, calendar-year share of
        `annual_revenue` -- a different money entirely from `pension_fund` (cash, VAT included)
        and open to every role the way the rest of this page is, not gated to `admin`
        with the fiscal block.

        `legal_entity_id` (REB-630, spec 2026-10-03 §1.9): the cash and the concentration
        narrow to it, or add up every legal entity without it. The fiscal block is one
        legal entity's or nothing: the named one, or the only one, and `None` in «tutte» on
        a space with several, where the page asks `/api/analytics/fiscal` once per
        legal entity instead, because a flat-rate regime's coefficients and an SRL's arithmetic
        do not add. In «tutte» the concentration rows come per legal entity, each share of
        its own legal entity's revenue, and carry which.

        `modules` is what the space has on (`None`: everything). Without `taxes` the fiscal
        block is not computed at all and the answer carries the cash alone.
        """
        pension_fund = self.cash_overview(year, actor, base, legal_entity_id)
        tax = projected_tax = None
        net = projected_net = None
        if actor.role == "admin" and (modules is None or "taxes" in modules):
            try:
                owner = LegalEntityService(self.session).single(legal_entity_id)
                # Only a regime with an income estimate has one to show: an ordinary or a
                # foreign legal entity gets the cash alone, not a card of zeros (REB-760).
                if owner is not None and self._has_income_estimate(owner.id, year, actor):
                    # The money the taxes are computed on is the legal entity's own, never
                    # the page's: in «tutte» with one active legal entity `pension_fund` still adds
                    # up a deactivated legal entity's receipts, which must not reach the
                    # active one's coefficients (spec §1.9, review of REB-630). The
                    # two nets below are read off the same figure, so they are that
                    # legal entity's too.
                    per_tax_office = (
                        pension_fund
                        if base == "collection" and pension_fund.legal_entity_id == owner.id
                        else self.cash_overview(year, actor, "collection", owner.id)
                    )
                    # The estimate itself, on the basis of the regime (REB-1071): the same
                    # figure `get_fiscal_estimate` answers over REST and MCP. Only the projection
                    # below is a «what if».
                    tax = self.get_fiscal_estimate(year, actor, legal_entity_id=owner.id)
                    # An srl's taxes are on what accrued (IRES and IRAP, REB-1073) and an ordinary
                    # person's start from the year's costs and withholdings (REB-1072): no
                    # projection, which is a «what if» on revenue alone, and no net, which would
                    # take those taxes off the cash gross.
                    if tax.company is None and tax.person is None:
                        projected_tax = self.get_fiscal_estimate(
                            year, actor, revenue=per_tax_office.projected, legal_entity_id=owner.id
                        )
                        if tax.due_total is not None:
                            net = round_money(per_tax_office.actual_gross - tax.due_total)
                        if projected_tax.due_total is not None:
                            projected_net = round_money(
                                per_tax_office.projected_gross - projected_tax.due_total
                            )
            except NotFound:
                # No fiscal profile yet, or no legal entity at all: the page says so and
                # shows the cash alone.
                tax = projected_tax = None
                net = projected_net = None
        return EconomicOverview(
            calculated_at=datetime.now(UTC),
            legal_entity_id=legal_entity_id,
            pension_fund=pension_fund,
            tax=tax,
            projected_tax=projected_tax,
            customer_concentration=[
                RevenueByCustomer(**row._asdict())
                for row in self.repo.revenue_by_customer(year, legal_entity_id)
            ],
            actual_net=net,
            projected_net=projected_net,
        )

    def _has_income_estimate(self, legal_entity_id: UUID, year: int, actor: Actor) -> bool:
        """Whether the legal entity's pack carries the income estimate for `year`; `NotFound`
        without a profile, as `get_fiscal_estimate` answers."""
        profile = FiscalProfileService(self.session).get(actor, legal_entity_id)
        law = resolve_pack(profile.pack_id, profile.pack_version).law_for(year)
        return law is not None and law.has_income_estimate

    def get_fiscal_estimate(
        self,
        year: int,
        actor: Actor,
        *,
        revenue: Decimal | None = None,
        legal_entity_id: UUID | None = None,
    ) -> FiscalEstimate:
        """A **period** report, never per deal (§8).

        The previous system computed this per offer, and the level was the defect rather than the
        formula. INPS gestione separata has a floor owed at zero income and a ceiling, so
        a pro-rata share attributes to a job an amount that does not depend on it; the
        profitability coefficient applies to the year, so applying it to slices and
        adding them gives a different number; and the result moves retroactively, because
        a March project's "profit" would depend on what is invoiced in November. There is
        deliberately no per-deal variant of this method, and `DealPnl` carries no tax
        field for one to be read into.

        The three rates come from `fiscal_profile` (task 4B-2), never from a literal in
        this package: the ATECO coefficient depends on the activity code, the INPS rate
        is re-set by the Legge di Bilancio most years, and `FiscalPanel.tsx` already lets
        their owner edit all three. Nothing is defaulted here -- a column nobody
        configured produces a `None` line, not an assumed one.

        `admin`, and **no MCP tool** -- the only *read* on §11's exclusion list, and for a
        reason unlike the other nine. It is not about reversibility: taxable income,
        contributions and estimated net for a real person are the most sensitive figures
        this product holds, and while residual R10 leaves a PAT without scopes and
        inheriting its owner's full role -- while "give Claude a token" still means "give
        it your account" -- that figure does not enter a conversational context on the
        back of a generic question about deals.

        One legal entity's, always (REB-630, spec 2026-10-03 §1.9): the estimate reads one
        profile's three rates and that legal entity's invoices alone. `legal_entity_id` resolves
        to the only legal entity when none is named, and is required, by name, when the
        space has several: a flat-rate regime's coefficients and an SRL's arithmetic do not
        add, so there is no space-wide figure to answer with.

        The revenue is the year on the one basis the regime's law names (`fiscal/basis.py`,
        REB-1071), and the payload's `basis` says what it was computed from: what was collected
        in the year for the flat-rate regime and the ordinary person, what accrued in it for an
        srl, never the year's invoicing. This is the one function every surface reads.
        """
        actor.require_admin("get_fiscal_estimate")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        # Raises `NotFound("fiscal_profile", <azienda id>)` when nothing is configured,
        # which tells the user which screen to go to -- better than an estimate of zero
        # computed from three nulls, which reads as "you owe nothing".
        profile = FiscalProfileService(self.session).get(actor, legal_entity.id)
        estimate = self._estimate_year(year, legal_entity, profile, revenue)
        if revenue is not None:
            return estimate
        declared = DeclaredTaxFigureService(self.session).declared_for_year(legal_entity.id, year)
        chosen = TaxAdvanceMethodRepository(self.session).for_entity(legal_entity.id).get(year)
        return estimate.model_copy(
            update={
                "advance_method": "forecast" if chosen == "forecast" else "historical",
                "declared": declared,
                "losses": TaxLossService(self.session).losses_for_entity(legal_entity.id),
                "reading": reading_for_year(declared),
                "readings": self._tax_readings(year, estimate, declared),
            }
        )

    def _tax_readings(
        self, year: int, estimate: FiscalEstimate, declared: list[DeclaredTaxFigureRead]
    ) -> TaxReadings:
        """The year's lines from the law of the pack the estimate was computed under (the
        frozen one for a frozen year), so a year keeps the lines it had."""
        law = (
            resolve_pack(estimate.pack_id, estimate.pack_version).law_for(year)
            if estimate.pack_id is not None and estimate.pack_version is not None
            else None
        )
        # The lines the estimate computes, only in a year whose law carries the estimate: an srl's
        # IRES and IRAP, an ordinary person's IRPEF (net of withholdings) and INPS (REB-1072), or
        # the flat-rate regime's substitute tax and INPS.
        estimates: dict[DeclaredTax, Decimal | None] = {}
        if law is not None and law.has_income_estimate:
            if estimate.company is not None:
                estimates = {
                    "ires": estimate.company.ires.amount,
                    "irap": estimate.company.irap.amount,
                }
            elif "irpef" in law.taxes:
                person = estimate.person
                estimates = {
                    "irpef": person.irpef.amount if person is not None else None,
                    "inps": person.inps.amount if person is not None else None,
                }
            else:
                estimates = {
                    "substitute_tax": estimate.substitute_tax,
                    "inps": estimate.contributions,
                }
        return build_tax_readings(law, estimates, declared)

    def estimated_taxes(
        self, year: int, legal_entity: LegalEntity, profile: FiscalProfileRead
    ) -> dict[DeclaredTax, Decimal | None]:
        """The estimate of each tax of `year` the code computes for the legal entity, `None`
        for one whose parameter is missing; a tax the code has no estimate for is absent. The
        figures of `get_fiscal_estimate`, with no role check of its own: its caller is the
        writer of the tax due items (`fiscal/tax_items.py`), which has asked for an
        administrator or runs in the system scope."""
        estimate = self._estimate_year(year, legal_entity, profile, None)
        lines = self._tax_readings(year, estimate, []).lines
        return {line.tax: line.estimate for line in lines if line.estimable}

    def estimated_inps_income(
        self, year: int, legal_entity: LegalEntity, profile: FiscalProfileRead
    ) -> Decimal | None:
        """The income of `year` the INPS contributions are computed on, as the estimate has it
        (REB-1097): an ordinary person's income of art. 54 TUIR (a loss counting as zero), the
        flat-rate regime's income of the Quadro LM. The INPS advances of the next year are
        computed on it. `None` when the estimate cannot be computed or the regime has none. Same
        caller and role rule as `estimated_taxes`."""
        estimate = self._estimate_year(year, legal_entity, profile, None)
        if estimate.person is not None:
            income = estimate.person.income.professional_income
            return None if income is None else max(income, Decimal(0))
        return estimate.taxable_amount

    def get_tax_calendar(
        self, year: int, actor: Actor, *, legal_entity_id: UUID | None = None
    ) -> TaxCalendar:
        """Every tax date of calendar `year` for one legal entity, from the pack its profile
        points at, each with its source (REB-1004, taxes design § 2 and § 5).

        `admin`, and behind `full_access` over MCP, as `get_fiscal_estimate` is: the dates
        say which taxes the legal entity owes, which is its tax position. The dates are
        the pack's and the pack is law: nothing here is computed from the legal entity's
        figures, and a pack with no calendar for `year` answers an empty list, never dates
        borrowed from another year. `NotFound("fiscal_profile")` without a profile, as the
        estimate answers.
        """
        actor.require_admin("get_tax_calendar")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        profile = FiscalProfileService(self.session).get(actor, legal_entity.id)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        dates = (
            tax_calendar(
                pack,
                year,
                calendar_year_end=(
                    legal_entity.fiscal_year_end_month,
                    legal_entity.fiscal_year_end_day,
                )
                == (12, 31),
            )
            or []
        )
        return TaxCalendar(
            year=year,
            legal_entity_id=legal_entity.id,
            pack_id=pack.id,
            pack_version=pack.version,
            dates=[TaxCalendarEntry.model_validate(entry, from_attributes=True) for entry in dates],
        )

    def freeze_fiscal_estimates(self, actor: Actor, legal_entity_id: UUID) -> None:
        """Keep, for every year the legal entity issued in, the estimate its pack computes
        now (REB-974). `FiscalProfileService.upsert` calls it just before it moves the legal
        entity to another pack, so a year computed under the pack being left keeps its
        figures after the move; `get_fiscal_estimate` serves them for any year the new pack
        does not compute. A year the pack being left has no law for is not kept. It never
        commits: the profile change that follows is the transaction, and it takes the legal
        entity's advisory lock first. No admin check of its own, for the same reason: its
        only caller has made it.
        """
        legal_entity = LegalEntityService(self.session).resolve(legal_entity_id)
        profile = FiscalProfileService(self.session).get(actor, legal_entity.id)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        readings = FiscalEstimateRepository(self.session)
        for year in self.repo.revenue_years(legal_entity.id):
            law = pack.law_for(year)
            if law is None or not law.has_income_estimate:
                continue
            estimate = self._compute_year(year, legal_entity, profile, None)
            # The parameters are the profile's and the legal entity's fiscal year end, which
            # the advances will date from.
            readings.save(
                legal_entity.id,
                year,
                pack_id=pack.id,
                pack_version=pack.version,
                law_from_year=law.from_year,
                parameters={
                    **profile.model_dump(mode="json", include=FROZEN_PARAMETERS),
                    "fiscal_year_end_month": legal_entity.fiscal_year_end_month,
                    "fiscal_year_end_day": legal_entity.fiscal_year_end_day,
                    # The region an srl's IRAP rate was read for (REB-1073) and the place a person's
                    # surcharges were read for (REB-1072), kept with the figures so a year moved to
                    # another pack keeps what it was computed for.
                    **(
                        {"irap_region": estimate.company.irap.region}
                        if estimate.company is not None
                        else {}
                    ),
                    **(
                        {
                            "irpef_region": estimate.person.irpef.region,
                            "municipal_cadastral_code": estimate.person.irpef.cadastral_code,
                        }
                        if estimate.person is not None
                        else {}
                    ),
                },
                figures=estimate.model_dump(mode="json"),
            )

    def _estimate_year(
        self,
        year: int,
        legal_entity: LegalEntity,
        profile: FiscalProfileRead,
        revenue: Decimal | None,
    ) -> FiscalEstimate:
        """The estimate of `year` for one legal entity under the pack its profile points at.
        When the legal entity's own revenue is the basis (no caller's «what if» `revenue`) and
        a reading was frozen when it left the pack the year was computed under, that reading is
        returned as it was, when the pack it points at now no longer computes the year or is
        another pack than the frozen one: a year belongs to the regime it was lived in, so a
        legal entity that moves to the ordinary pack keeps the flat-rate year it left, though
        the ordinary pack computes that year too (REB-1072)."""
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        law = pack.law_for(year)
        if revenue is None:
            frozen = FiscalEstimateRepository(self.session).get(legal_entity.id, year)
            if frozen is not None and (
                law is None or not law.has_income_estimate or frozen.pack_id != pack.id
            ):
                return FiscalEstimate.model_validate({**frozen.figures, "frozen": True})
        return self._compute_year(year, legal_entity, profile, revenue)

    def _compute_year(
        self,
        year: int,
        legal_entity: LegalEntity,
        profile: FiscalProfileRead,
        revenue: Decimal | None,
    ) -> FiscalEstimate:
        """The estimate of `year` computed now under the pack the profile points at, whatever was
        frozen before: what `freeze_fiscal_estimates` keeps, so a leaving pack never freezes the
        figures an earlier freeze kept of another pack."""
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        law = pack.law_for(year)
        basis = None
        if revenue is None:
            # The year on the one basis the regime's law names (`fiscal/basis.py`, REB-1071):
            # what was collected in the year for a cash regime, what accrued in it for an
            # accrual one. This is the only place an estimate's revenue is chosen, so the REST
            # route, the MCP tool and the economic overview answer the same figure for the same
            # year. A year the pack states no basis for has no revenue to speak of and no figure.
            basis = year_basis(self.session, legal_entity, year, pack)
            revenue = basis.revenue if basis is not None else ZERO_MONEY
        estimate = estimate_income(
            year=year,
            legal_entity_id=legal_entity.id,
            # A caller's own `revenue` is a «what if»: the economic overview's projection,
            # which no basis was read for.
            revenue=revenue,
            coefficient=profile.profitability_coefficient,
            rate_substitute=profile.substitute_tax_rate,
            social_security_rate=profile.social_security_rate,
            flat_rate=law.flat_rate if law is not None else None,
            # A «what if» revenue has no basis, and the payments of the year say nothing of it.
            contributions_paid=(
                inps_paid_in_year(self.session, legal_entity.id, year)
                if basis is not None and law is not None and law.flat_rate is not None
                else None
            ),
        )
        update: dict[str, object] = {
            "missing_parameters": (
                [
                    name
                    for name in (
                        "profitability_coefficient",
                        "substitute_tax_rate",
                        "social_security_rate",
                    )
                    if getattr(profile, name) is None
                ]
                if law is not None and law.flat_rate is not None
                else []
            ),
            "pack_id": pack.id,
            "pack_version": pack.version,
            "law_from_year": law.from_year if law is not None else None,
            "basis": basis,
        }
        if basis is not None and law is not None and "irpef" in law.taxes:
            person = self._ordinary_estimate(legal_entity, profile, pack, law, basis)
            if person is not None:
                update.update(
                    {
                        "person": person,
                        "warning": ORDINARY_WARNING,
                        "contributions": person.inps.amount,
                        "due_total": person.due_total,
                        "missing_parameters": person.missing_parameters,
                    }
                )
        estimate = estimate.model_copy(update=update)

        if basis is None or law is None or law.company_tax is None:
            return estimate
        # An srl's IRES and IRAP from the same basis (REB-1073): the region is read from the
        # legal entity's address at this moment, never stored.
        rules = pack.payment_rules_for(basis.year)
        credit_rule = rules.withholding_credit if rules is not None else None
        withheld = (
            company_withholdings_suffered(
                self.session, legal_entity.id, basis.period_from, basis.period_to, credit_rule
            )
            if credit_rule is not None and credit_rule.income_basis == "accrual"
            else None
        )
        place = locate(legal_entity.city, legal_entity.province, legal_entity.country)
        company = estimate_company_taxes(
            basis,
            law.company_tax,
            place=place,
            province=legal_entity.province,
            withholdings=withheld,
            withholdings_source=credit_rule.source if credit_rule is not None else "",
            activity=IrapActivity(
                ateco_2007=profile.ateco_2007_code, ateco_2025=profile.ateco_2025_code
            ),
            losses=lambda income: loss_offset_for(
                self.session, legal_entity, pack, "ires", basis, income=income
            ),
            variations=read_income_variations(self.session, legal_entity, basis, pack),
            sites=self._irap_sites(legal_entity, basis, place),
        )
        if company is None:
            return estimate
        ires, irap = company.ires.amount, company.irap.amount
        return estimate.model_copy(
            update={
                "company": company,
                # What an srl sets aside is both taxes, and a total with a tax missing would
                # read as a smaller bill: no total until both are known.
                "due_total": (
                    round_money(ires + irap) if ires is not None and irap is not None else None
                ),
            }
        )

    def _irap_sites(
        self, legal_entity: LegalEntity, basis: YearBasis, place: Place
    ) -> SiteFacts | None:
        """The legal entity's places of business, and the pay of its staff when one of them is in
        another region than the registered office's (REB-1092): the staff's pay is the costly
        read and only the split of art. 4 c. 2 needs it. `None` for a legal entity with no site."""
        rows = list(
            self.session.execute(
                select(LegalEntitySite)
                .where(LegalEntitySite.legal_entity_id == legal_entity.id)
                .order_by(LegalEntitySite.region, LegalEntitySite.city, LegalEntitySite.id)
            ).scalars()
        )
        if not rows:
            return None
        sites = tuple(
            SitePlace(
                id=row.id,
                region=row.region,
                label=", ".join(part for part in (row.city, row.address) if part) or row.region,
            )
            for row in rows
        )
        if not splits(irap_region_key(place, legal_entity.province), sites):
            return SiteFacts(sites=sites, staff=())
        pay = PayrollService(self.session).staff_pay_in_range(
            legal_entity.id, basis.period_from, basis.period_to
        )
        return SiteFacts(sites=sites, staff=pay.staff, unpriced=pay.unpriced)

    def _ordinary_estimate(
        self,
        legal_entity: LegalEntity,
        profile: FiscalProfileRead,
        pack: FiscalPack,
        law: TaxYearLaw,
        basis: YearBasis,
    ) -> OrdinaryEstimate | None:
        """An ordinary person's INPS and IRPEF for the year of `basis` (REB-1072): the figures
        of `fiscal/ordinary_estimate.py` on the legal entity's profile, the place its address
        names (read now, never stored) and the withholdings its customers kept on what it
        collected, for the year the pack's rule credits them against."""
        rules = pack.payment_rules_for(basis.year)
        credit_rule = rules.withholding_credit if rules is not None else None
        withheld = (
            withholdings_suffered(self.session, legal_entity.id, basis.year, credit_rule)
            if credit_rule is not None
            else None
        )
        return estimate_ordinary_taxes(
            basis,
            pack,
            law,
            inps_facts=InpsFacts(
                category=cast(InpsCategoryName | None, profile.inps_category),
                trade=cast(TradeGestione | None, profile.inps_trade),
                covered_elsewhere=profile.inps_covered_elsewhere,
                before_1996=profile.inps_before_1996,
                over_65_reduction=profile.inps_over_65_reduction,
                new_enrolment_reduction=profile.inps_new_enrolment_reduction,
                first_enrolment_month=profile.inps_first_enrolment_month,
            ),
            place=locate(legal_entity.city, legal_entity.province, legal_entity.country),
            province=legal_entity.province,
            withholdings=withheld,
            household=profile.household,
            inps_paid=inps_paid_in_year(self.session, legal_entity.id, basis.year),
            losses=loss_offset_for(self.session, legal_entity, pack, "irpef", basis),
        )


__all__ = ["AnalyticsService"]
