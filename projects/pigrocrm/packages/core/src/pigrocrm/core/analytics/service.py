from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

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
    UnbilledBacklog,
    YearOverview,
)
from pigrocrm.core.analytics.year_csv import render_year_csv, year_csv_filename
from pigrocrm.core.config import get_settings
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.db import today_local
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.ceiling import (
    CeilingStatus,
    evaluate_ceiling,
    evaluate_pack,
    taxable_revenue,
)
from pigrocrm.core.fiscal.pack import resolve_pack
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.money import round_money, share

ENTITY = "analytics"
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
        expected="budgeted_value, oppure budgeted_hours insieme a hourly_rate",
    )


class AnalyticsService:
    """Reads only: the facade over the figure modules of this package (`pnl`, `budget`,
    `cash`, `year_overview`, `days`). The writer that binds hours to a draft invoice
    lives in `timetracking.billing`.

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

    def period_pnl(self, query: PeriodPnlQuery, actor: Actor) -> PeriodPnl:
        """The P&L of a window in two columns, closed and in-progress deals
        (`pnl.period_pnl`)."""
        return pnl.period_pnl(self.session, self.repo, query)

    def unbilled_backlog(
        self, actor: Actor, legal_entity_id: UUID | None = None
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
        """
        hours, value, without_rate, entries = self.repo.unbilled_backlog(
            legal_entity_id=legal_entity_id
        )
        return UnbilledBacklog(
            billable_uninvoiced_hours=hours,
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
        addition = _synthetic_addition(query)
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
                query.contract_id, addition, query.expected_date, actor
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
    ) -> EconomicOverview:
        """The economic tab of the dashboard, in one answer: the cash view for everyone,
        and for an admin with a fiscal profile the estimate on what was collected and on
        what is projected, with the two nets. **No MCP tool** -- it carries the fiscal
        estimate, and `get_fiscal_estimate`'s reasons apply unchanged.

        `base` moves the charts and the cash cards only. The fiscal block is always
        computed on the `collection` reading, because the flat-rate regime is taxed on what was
        collected in the calendar year and a chart that reads by accrual period must not
        move the taxes (`docs/design/DECISIONS.md`, 2026-09-10). Within one year the two
        readings' totals agree except for a document whose period and payment fall in
        different years, which is exactly the case where the tax must follow the money.

        Under `accrual`, the default, that is the cash view read twice: eight
        aggregates instead of four, on a page that already runs a dozen. Accepted, rather
        than threading two readings through `cash_overview`, whose one job is one reading.

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
        """
        pension_fund = self.cash_overview(year, actor, base, legal_entity_id)
        tax = projected_tax = None
        net = projected_net = None
        if actor.role == "admin":
            try:
                owner = LegalEntityService(self.session).single(legal_entity_id)
                # Only a regime with an income estimate has one to show: an ordinary or a
                # foreign legal entity gets the cash alone, not a card of zeros (REB-760).
                if owner is not None and self._has_income_estimate(owner.id, actor):
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
                    tax = self.get_fiscal_estimate(
                        year, actor, revenue=per_tax_office.paid, legal_entity_id=owner.id
                    )
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

    def _has_income_estimate(self, legal_entity_id: UUID, actor: Actor) -> bool:
        """Whether the legal entity's pack carries the income estimate; `NotFound` without a
        profile, as `get_fiscal_estimate` answers."""
        profile = FiscalProfileService(self.session).get(actor, legal_entity_id)
        return resolve_pack(profile.pack_id, profile.pack_version).estimate_income

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
        """
        actor.require_admin("get_fiscal_estimate")
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        # Raises `NotFound("fiscal_profile", <azienda id>)` when nothing is configured,
        # which tells the user which screen to go to -- better than an estimate of zero
        # computed from three nulls, which reads as "you owe nothing".
        profile = FiscalProfileService(self.session).get(actor, legal_entity.id)
        if revenue is None:
            # Every issued invoice of the year, deal or no deal: the estimate is about
            # the person's income, and an invoice attached to no deal is still income.
            # Reduced by the pack's own tagged charges (REB-352 §2, §6's resolved
            # decision): a social security surcharge line counts toward the ceiling in full but is
            # not
            # taxable income, so it never reaches the coefficiente base here even
            # though `annual_revenue` sums it in full.
            pack = resolve_pack(profile.pack_id, profile.pack_version)
            revenue = taxable_revenue(
                pack,
                self.session,
                year,
                self.repo.annual_revenue(year, legal_entity.id),
                legal_entity.id,
            )
        return estimate_income(
            year=year,
            legal_entity_id=legal_entity.id,
            # `revenue` overrides the reduced figure above when the caller asks "what
            # if": the economic overview passes what was collected, and what is
            # projected, neither of which the pack's own tag applies to.
            revenue=revenue,
            coefficient=profile.profitability_coefficient,
            rate_substitute=profile.substitute_tax_rate,
            social_security_rate=profile.social_security_rate,
        )


__all__ = ["AnalyticsService"]
