from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.fiscal import estimate_income
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import (
    BindTimeRequest,
    BudgetPage,
    BudgetQuery,
    BudgetVsActualRow,
    CashBase,
    CashMonth,
    CashOverview,
    CeilingHeadroom,
    CeilingSimulation,
    CeilingSimulationQuery,
    CeilingSimulationResult,
    CeilingStatusRead,
    ContractDateMarker,
    DealPnl,
    EconomicOverview,
    FiscalEstimate,
    PeriodPnl,
    PeriodPnlQuery,
    PnlTotals,
    RevenueByCustomer,
    UnbilledBacklog,
)
from pigrocrm.core.config import get_settings
from pigrocrm.core.contracts.dates import irrevocability_window_end, renewal_deadline
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.db import today_local
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.ceiling import (
    CeilingStatus,
    evaluate_ceiling,
    evaluate_pack,
    taxable_ricavi,
)
from pigrocrm.core.fiscal.pack import resolve_pack
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn, InvoiceRead
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.money import (
    ZERO_HOURS,
    ZERO_MONEY,
    percentage_of,
    round_money,
    sum_hours,
    sum_money,
)
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.storage.factory import storage_from_settings
from pigrocrm.core.timetracking.locks import PeriodLockService, period_label
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.service import TimeEntryService, billed_entry_ids

ENTITY = "analytics"


def _months_between(da: date, a: date) -> list[tuple[int, int]]:
    """Every (year, month) the window touches, inclusive. Iterated rather than computed
    with arithmetic on month numbers, which is where December off-by-ones live."""
    months: list[tuple[int, int]] = []
    year, month = da.year, da.month
    while (year, month) <= (a.year, a.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _comparable(estimate: Decimal | None) -> Decimal | None:
    """The estimate itself when it can be compared against, `None` when it cannot.

    An estimate is comparable only when it is present **and** greater than zero. `None`
    means nobody estimated. `0` is the state residual A14 made the only reachable spelling
    of "no estimate" before Task 4B-1 closed it, and it reads to any division as "zero
    hours estimated, infinite overrun". Both are refused as denominators here -- the
    secondary defence §9.2 requires regardless of A14.

    Returns the narrowed value rather than a `bool` on purpose: a predicate would leave
    every call site holding a `Decimal | None` that the type checker has no way to follow,
    and the usual repair for that is a `cast` or an `assert` -- an escape hatch standing
    exactly where the null this function exists to catch would come through.
    """
    if estimate is None or estimate <= 0:
        return None
    return estimate


def _totals(rows: list[tuple[Decimal, Decimal, Decimal]]) -> PnlTotals:
    """One column of `(ricavi, costi diretti, costo lavoro)` triples, summed.

    `sum_money` and not `sum()`: the rows arriving here are already rounded per deal, and
    the column total has to be the sum of the printed figures rather than the rounding of
    an exact sum -- the same rule the timesheet obeys, for the same reason (§6.2).
    """
    ricavi = sum_money([row[0] for row in rows])
    costi = sum_money([row[1] for row in rows])
    lavoro = sum_money([row[2] for row in rows])
    margine = ricavi - costi - lavoro
    return PnlTotals(
        ricavi=ricavi,
        costi_diretti=costi,
        costo_lavoro=lavoro,
        margine_lordo=margine,
        margine_percentuale=percentage_of(margine, ricavi),
        deal=len(rows),
    )


def _group_order(key: tuple[Decimal, tuple[int, int] | None]) -> tuple[int, int, Decimal]:
    """Month first, rate within it -- the order the lines are printed in.

    A `sorted(..., key=...)` on the raw key would compare `tuple[int, int] | None`
    against `tuple[int, int]` and fail; flattening it to `(0, 0)` puts the un-dated
    groups of `raggruppa_per_mese = False` first, which is where they belong when they
    are also the only ones.
    """
    tariffa, mese = key
    anno, numero_mese = mese if mese is not None else (0, 0)
    return (anno, numero_mese, tariffa)


def _ceiling_status_read(status: CeilingStatus) -> CeilingStatusRead:
    return CeilingStatusRead(
        id=status.ceiling.id,
        etichetta=status.ceiling.etichetta,
        soglia=status.ceiling.soglia,
        conseguenza=status.ceiling.conseguenza,
        ricavi=status.ricavi,
        residuo=status.residuo,
        superata=status.superata,
        livello_allerta=status.livello_allerta,
    )


def _synthetic_addition(query: CeilingSimulationQuery) -> Decimal:
    """The one euro figure REB-352 §1.4's simulator adds to `ricavi` before
    re-evaluating each ceiling -- the deal's own value estimate when one was typed
    directly, or hours times rate when only those two were, mirroring
    `budget_vs_actual`'s own reading of the first two columns and extending it by
    the third (`Deal.tariffa_oraria`) for the deal that has not priced a value yet.
    """
    if query.valore_preventivato is not None:
        return query.valore_preventivato
    if query.ore_preventivate is not None and query.tariffa_oraria is not None:
        return round_money(query.ore_preventivate * query.tariffa_oraria)
    raise ValidationFailed(
        ENTITY,
        "valore_preventivato",
        "serve una stima per simulare l'aggiunta",
        expected="valore_preventivato, oppure ore_preventivate insieme a tariffa_oraria",
    )


class AnalyticsService:
    """Reads only, except for `bind_time_to_invoice` (Task 4B-7).

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

    def __init__(self, session: Session, storage: DocumentStorage | None = None) -> None:
        """`storage` is optional and is used by `bind_time_to_invoice` alone.

        Every read method in this file needs none, and the twenty-odd call sites that
        only want a figure keep building `AnalyticsService(session)` with one argument.
        The one writer needs an `InvoiceService`, whose constructor requires a backend
        because the artefacts a *later* emission produces are written through it -- not
        because creating a draft writes anything. Defaulted to `None` and resolved from
        settings inside that one method, so no read path ever constructs a storage
        backend, and in particular a misconfigured `gdrive` cannot make a P&L fail.
        """
        self.session = session
        self.storage = storage
        self.repo = AnalyticsRepository(session)
        self.deals = DealRepository(session)
        self.entries = TimeEntryService(session)
        self.contracts = ContractRepository(session)

    def deal_pnl(self, deal_id: UUID, actor: Actor) -> DealPnl:
        """The rows of §7.1, each from its one stated source.

        `valore_maturato = ricavi + valore delle ore fatturabili non fatturate`. It is
        **not** revenue, enters no P&L row, and is returned in a field of its own name
        (§7.3): on an `in corso` deal it is the honest figure and the margin is
        provisional; the margin is only reportable when the state is `chiuso`.

        Which reading of "already invoiced" this uses, since task 4B-3 left two that
        deliberately differ: the **invoice-state** one, through `billed_entry_ids`, not
        the link-based one the `fatturato` list filter applies. An hour bound to a line of
        a *draft* invoice is `fatturato` in that list and still fully editable; here it
        still counts as billable-and-unbilled, because a draft is not revenue and the
        link-based reading would leave the work in neither figure -- priced, done and
        invisible until somebody pressed "issue".
        """
        deal = self.deals.get(deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)

        ricavi, fatture = self.repo.deal_revenue(deal_id)
        costi_diretti = self.repo.deal_direct_costs(deal_id)
        # Reuses 4A's own summary rather than recomputing hours here: one definition of
        # "labour cost" and one of "state", so the Ore tab and the Economia tab can never
        # disagree about the same deal.
        summary = self.entries.deal_summary(deal_id, actor)
        margine = ricavi - costi_diretti - summary.costo_lavoro

        return DealPnl(
            deal_id=deal_id,
            stato=summary.stato,
            ricavi=ricavi,
            costi_diretti=costi_diretti,
            costo_lavoro=summary.costo_lavoro,
            margine_lordo=margine,
            # `percentage_of` returns `None` for a zero denominator and performs no
            # division at all in that case, so this is the only place the question is
            # asked and there is no second answer to keep aligned.
            margine_percentuale=percentage_of(margine, ricavi),
            ore_totali=summary.ore_totali,
            ore_fatturabili_non_fatturate=summary.ore_fatturabili_non_fatturate,
            valore_maturato=ricavi + summary.valore_ore_non_fatturate,
            ore_senza_tariffa=summary.ore_senza_tariffa,
            fatture_emesse=fatture,
        )

    def period_pnl(self, query: PeriodPnlQuery, actor: Actor) -> PeriodPnl:
        """Aggregated over a date window, optionally scoped to one customer.

        Each quantity is attributed to the period by **its own** date: revenue by
        `invoices.data_emissione`, costs by `costs.data`, labour cost by
        `time_entries.data`. Not by the deal's date, which does not exist, and not by one
        common date, which none of the three has. `query.base` offers a second reading
        of the revenue alone (ORB-61): by the accrual period the invoice declares,
        because invoicing runs late and August's work issued in September has to be
        readable in August. Costs and hours do not move with it, and neither does
        anything else in this report.

        Presented in **two columns** -- closed deals and deals in progress -- because
        adding a finished job's margin to a half-done one produces a figure that is
        neither, and that changes every week for reasons which are not business
        performance. The reportable number is the first, and there is deliberately no
        combined field to read by mistake.
        """
        if query.a < query.da:
            raise ValidationFailed(
                ENTITY, "a", "intervallo invertito", expected="una data non anteriore a 'da'"
            )

        revenue = self.repo.revenue_in_range(query.da, query.a, query.customer_id, query.base)
        per_deal_costs, general = self.repo.costs_in_range(query.da, query.a, query.customer_id)
        labour = self.repo.labour_cost_in_range(query.da, query.a, query.customer_id)
        # Slice 6 §5's three rows, from the same aggregate `unbilled_backlog` uses and
        # merely bounded to the period: one definition of "ore fatturabili non fatturate",
        # not one per screen. They are informative and enter no margin, which is why they
        # sit outside both `PnlTotals` columns rather than inside either.
        ore_arretrate, valore_arretrato, senza_tariffa, _ = self.repo.unbilled_backlog(
            query.da, query.a, query.customer_id
        )

        chiusi: list[tuple[Decimal, Decimal, Decimal]] = []
        in_corso: list[tuple[Decimal, Decimal, Decimal]] = []
        for deal in self.repo.deals_in_range(query.da, query.a, query.customer_id, query.base):
            row = (
                revenue.get(deal.id, ZERO_MONEY),
                per_deal_costs.get(deal.id, ZERO_MONEY),
                labour.get(deal.id, ZERO_MONEY),
            )
            # The state comes from the same place the deal's own P&L gets it, so the two
            # screens can never disagree about which column a deal belongs in -- and, in
            # particular, both read "already invoiced" as the *invoice state* through
            # `billed_entry_ids`, never as the link-based `fatturato` list filter. An
            # hour bound to a line of a draft invoice is not revenue yet.
            stato = self.entries.deal_summary(deal.id, actor).stato
            (chiusi if stato == "chiuso" else in_corso).append(row)

        locks = PeriodLockService(self.session)
        # A window is only as closed as its least-closed month: reporting a quarter as
        # closed because one of its months is would be the wrong reassurance in the one
        # place it matters.
        periodo_chiuso = all(
            locks.is_closed(date(anno, mese, 1)) is not None
            for anno, mese in _months_between(query.da, query.a)
        )

        return PeriodPnl(
            da=query.da,
            a=query.a,
            customer_id=query.customer_id,
            base=query.base,
            chiusi=_totals(chiusi),
            in_corso=_totals(in_corso),
            # Never apportioned onto any deal (§7.4), and absent entirely under a
            # customer filter, because a general expense belongs to no customer.
            spese_generali=general,
            valore_maturato=valore_arretrato,
            ore_fatturabili_non_fatturate=ore_arretrate,
            ore_senza_tariffa=senza_tariffa,
            periodo_chiuso=periodo_chiuso,
            # Free: a COUNT over two columns that already exist, and the one thing a
            # reader most needs to know about an open period (§6.4).
            voci_scritte_in_ritardo=self.repo.late_entry_count(query.da, query.a),
        )

    def unbilled_backlog(self, actor: Actor) -> UnbilledBacklog:
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
        does not grow. That is the right outcome: the backlog is the figure an agent can be
        most useful about, and it is read-only.
        """
        ore, valore, senza_tariffa, voci = self.repo.unbilled_backlog()
        return UnbilledBacklog(
            ore_fatturabili_non_fatturate=ore,
            valore_maturato=valore,
            voci_senza_tariffa=senza_tariffa,
            voci=voci,
        )

    def budget_vs_actual(self, query: BudgetQuery, actor: Actor) -> BudgetPage:
        """Estimate against actual, per deal, with the pro-rata comparison beside the
        full one rather than instead of it.

        This is the payment on an investment made in advance: `deals.ore_preventivate`
        and `deals.valore_preventivato` have existed since slice 1 -- written with a
        comment saying so -- and have never been read by anybody. The actuals are the one
        missing half.
        """
        if query.a < query.da:
            raise ValidationFailed(
                ENTITY, "a", "intervallo invertito", expected="una data non anteriore a 'da'"
            )

        revenue = self.repo.revenue_in_range(query.da, query.a, query.customer_id)
        hours = self.repo.hours_in_range(query.da, query.a, query.customer_id)
        deals = self.repo.deals_in_range(query.da, query.a, query.customer_id)

        # Keyset pagination over the already-ordered deal list rather than a second
        # database round trip: `deals_in_range` orders by `(nome, id)`, so the cursor is
        # the last id of the page.
        if query.cursor is not None:
            ids = [deal.id for deal in deals]
            if query.cursor in ids:
                deals = deals[ids.index(query.cursor) + 1 :]
        window = deals[: query.limit]
        next_cursor = window[-1].id if len(deals) > query.limit and window else None

        rows: list[BudgetVsActualRow] = []
        for deal in window:
            ore_consuntivate = hours.get(deal.id, ZERO_HOURS)
            ricavi = revenue.get(deal.id, ZERO_MONEY)
            # Narrowed to "comparable or absent" once, here, so that below there is
            # nothing left to divide by that could be null or zero.
            ore_prev = _comparable(deal.ore_preventivate)
            valore_prev = _comparable(deal.valore_preventivato)

            avanzamento = (
                percentage_of(ore_consuntivate, ore_prev) if ore_prev is not None else None
            )
            # The pro-rata needs BOTH estimate columns. With a value estimate and no hours
            # estimate there is no progress to derive it from, and inventing one from the
            # invoiced value would be circular -- the invoiced value is the quantity being
            # judged.
            pro_rata = (
                round_money(valore_prev * avanzamento / Decimal(100))
                if (valore_prev is not None and avanzamento is not None)
                else None
            )
            rows.append(
                BudgetVsActualRow(
                    deal_id=deal.id,
                    nome=deal.nome,
                    # The stored column, not the narrowed one: a deal estimated at zero
                    # hours should show the zero somebody typed. What `_comparable`
                    # governs is what may be divided by, never what may be displayed.
                    ore_preventivate=deal.ore_preventivate,
                    ore_consuntivate=ore_consuntivate,
                    valore_preventivato=deal.valore_preventivato,
                    ricavi=ricavi,
                    avanzamento_ore=avanzamento,
                    budget_pro_rata=pro_rata,
                    # Measured against the pro-rata, never the full budget: at 40% of the
                    # hours, being at 40% of the estimated value is on track, and
                    # comparing with 100% would mark every job in progress as
                    # underperforming.
                    scostamento_valore=(ricavi - pro_rata) if pro_rata is not None else None,
                    scostamento_ore=(ore_consuntivate - ore_prev if ore_prev is not None else None),
                    tariffa_media_preventivata=(
                        round_money(valore_prev / ore_prev)
                        if (ore_prev is not None and valore_prev is not None)
                        else None
                    ),
                    # The row that serves best, and the only form in which "is this client
                    # worth it?" has a numeric answer: how much was realised per hour
                    # worked, comparable even between deals of very different sizes.
                    # `None` with no hours logged, never `0.00`, which would read as
                    # "realised nothing per hour" -- the opposite of the truth on a deal
                    # invoiced without a timesheet.
                    tariffa_media_consuntivata=(
                        round_money(ricavi / ore_consuntivate) if ore_consuntivate > 0 else None
                    ),
                    non_preventivato=ore_prev is None and valore_prev is None,
                    pro_rata_non_calcolabile=valore_prev is not None and ore_prev is None,
                )
            )

        budgeted = [row for row in rows if not row.non_preventivato]
        return BudgetPage(
            items=rows,
            next_cursor=next_cursor,
            # Only over rows with an estimate: including the unestimated ones would make
            # the aggregate depend on how many deals nobody estimated.
            totale_preventivato=sum_money([row.valore_preventivato for row in budgeted]),
            totale_ricavi=sum_money([row.ricavi for row in budgeted]),
            deal_preventivati=len(budgeted),
            deal_non_preventivati=len(rows) - len(budgeted),
        )

    def ceiling_headroom(self, anno: int, actor: Actor) -> CeilingHeadroom:
        """REB-352 §1.4: every active ceiling of the configured jurisdiction pack,
        evaluated against `anno`'s real paid revenue -- `evaluate_pack`'s own output,
        read rather than recomputed (no new revenue query, per REB-352 §1.4's own
        reasoning). Raises `NotFound("fiscal_profile", ...)` when nobody has
        configured one yet, the same signal `get_fiscal_estimate` gives: there is no
        pack to resolve without a profile to point at one.

        Not admin-only, unlike `get_fiscal_estimate`: this is a revenue-versus-
        threshold figure over the same `Invoice` rows `cash_overview` and
        `budget_vs_actual` already open to every role, not the taxable-income
        computation that method alone gates.
        """
        profile = FiscalProfileService(self.session).get(actor)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        return CeilingHeadroom(
            anno=anno,
            pack_id=pack.id,
            pack_version=pack.version,
            soglie=[
                _ceiling_status_read(status) for status in evaluate_pack(pack, self.session, anno)
            ],
        )

    def simulate_ceiling(
        self, anno: int, query: CeilingSimulationQuery, actor: Actor
    ) -> CeilingSimulation:
        """REB-352 §1.4's "would this fit?" simulator: the same active ceilings,
        each re-evaluated with a synthetic addition on top of the real, already-
        summed `ricavi` -- `evaluate_ceiling`'s own pure half, exactly so this needs
        no second revenue query (`fiscal/ceiling.py::evaluate_ceiling`'s own
        docstring names this simulator by name). The addition is a not-yet-won
        deal's own estimate, read raw off its own three columns rather than by
        `deal_id`, so nothing has to be saved to ask the question.
        """
        profile = FiscalProfileService(self.session).get(actor)
        pack = resolve_pack(profile.pack_id, profile.pack_version)
        aggiunta = _synthetic_addition(query)
        risultati: list[CeilingSimulationResult] = []
        for prima in evaluate_pack(pack, self.session, anno):
            dopo = evaluate_ceiling(prima.ceiling, prima.ricavi + aggiunta)
            risultati.append(
                CeilingSimulationResult(
                    id=prima.ceiling.id,
                    etichetta=prima.ceiling.etichetta,
                    soglia=prima.ceiling.soglia,
                    conseguenza=prima.ceiling.conseguenza,
                    ricavi_attuali=prima.ricavi,
                    residuo_attuale=prima.residuo,
                    ricavi_simulati=dopo.ricavi,
                    residuo_simulato=dopo.residuo,
                    rientra=not dopo.superata,
                    livello_allerta_simulato=dopo.livello_allerta,
                )
            )
        return CeilingSimulation(
            anno=anno,
            pack_id=pack.id,
            pack_version=pack.version,
            aggiunta_sintetica=aggiunta,
            soglie=risultati,
        )

    def _contract_date_markers(self, anno: int) -> list[ContractDateMarker]:
        """REB-352 §1.6's overlay: every irrevocability-window close and renewal
        deadline that falls inside `anno`'s calendar, across every non-deleted
        contract. The irrevocability date is measured "as of" today regardless of
        `anno` -- a notice period is a forward-looking promise, never a fact about a
        past year -- so a past `anno` never grows one and a future one only does
        once today's window actually reaches into it.
        """
        year_start = date(anno, 1, 1)
        year_end = date(anno, 12, 31)
        oggi = today_local()
        markers: list[ContractDateMarker] = []
        for contract in self.contracts.list_active():
            end = irrevocability_window_end(contract, oggi)
            if end is not None and year_start <= end <= year_end:
                markers.append(
                    ContractDateMarker(
                        contract_id=contract.id,
                        titolo=contract.titolo,
                        customer_id=contract.customer_id,
                        tipo="fine_irrevocabilita",
                        data=end,
                    )
                )
            deadline = renewal_deadline(contract)
            if deadline is not None and year_start <= deadline <= year_end:
                markers.append(
                    ContractDateMarker(
                        contract_id=contract.id,
                        titolo=contract.titolo,
                        customer_id=contract.customer_id,
                        tipo="scadenza_rinnovo",
                        data=deadline,
                    )
                )
        markers.sort(key=lambda m: (m.data, m.titolo))
        return markers

    def cash_overview(self, anno: int, actor: Actor, base: CashBase = "competenza") -> CashOverview:
        """The year as cash, month by month (`CashOverview`). Read by anyone who may read
        the dashboard: nothing here is fiscal, and every figure is a SUM the repository
        produced plus additions done once, here. `base` says which month a document
        falls in (ORB-133). The year's totals agree under the two readings except for a
        document whose declared period and whose money fall in different years: that
        one is in one year's view and not the other's."""
        actor.require_agent_allowed("cash_overview")
        incassato = self.repo.monthly_incassato(anno, base)
        da_incassare = self.repo.monthly_da_incassare(anno, base)
        bozze = self.repo.monthly_bozze(anno, base)
        costi = self.repo.monthly_costi(anno)
        months = range(1, 13)
        zero = ZERO_MONEY
        stack_andamento = max(
            (incassato.get(m, zero) + costi.get(m, zero) for m in months), default=zero
        )
        stack_proiezione = max(
            (
                incassato.get(m, zero)
                + da_incassare.get(m, zero)
                + bozze.get(m, zero)
                + costi.get(m, zero)
                for m in months
            ),
            default=zero,
        )

        def share(value: Decimal, of: Decimal) -> float:
            return float(value / of) if of > zero else 0.0

        mesi = [
            CashMonth(
                anno=anno,
                mese=m,
                incassato=incassato.get(m, zero),
                da_incassare=da_incassare.get(m, zero),
                bozze=bozze.get(m, zero),
                costi=costi.get(m, zero),
                pila_andamento=round_money(incassato.get(m, zero) + costi.get(m, zero)),
                pila_proiezione=round_money(
                    incassato.get(m, zero)
                    + da_incassare.get(m, zero)
                    + bozze.get(m, zero)
                    + costi.get(m, zero)
                ),
                quote_andamento={
                    "incassato": share(incassato.get(m, zero), stack_andamento),
                    "costi": share(costi.get(m, zero), stack_andamento),
                },
                quote_proiezione={
                    "incassato": share(incassato.get(m, zero), stack_proiezione),
                    "da_incassare": share(da_incassare.get(m, zero), stack_proiezione),
                    "bozze": share(bozze.get(m, zero), stack_proiezione),
                    "costi": share(costi.get(m, zero), stack_proiezione),
                },
            )
            for m in months
        ]
        tot_incassato = sum_money(incassato.values())
        tot_da_incassare = sum_money(da_incassare.values())
        tot_bozze = sum_money(bozze.values())
        tot_costi = sum_money(costi.values())
        proiettato = round_money(tot_incassato + tot_da_incassare + tot_bozze)
        return CashOverview(
            anno=anno,
            base=base,
            incassato=tot_incassato,
            da_incassare=tot_da_incassare,
            bozze=tot_bozze,
            proiettato=proiettato,
            costi=tot_costi,
            lordo_effettivo=round_money(tot_incassato - tot_costi),
            lordo_proiettato=round_money(proiettato - tot_costi),
            mesi=mesi,
            scadenze_contrattuali=self._contract_date_markers(anno),
        )

    def economic_overview(
        self, anno: int, actor: Actor, base: CashBase = "competenza"
    ) -> EconomicOverview:
        """The economic tab of the dashboard, in one answer: the cash view for everyone,
        and for an admin with a fiscal profile the estimate on what was collected and on
        what is projected, with the two nets. **No MCP tool** -- it carries the fiscal
        estimate, and `get_fiscal_estimate`'s reasons apply unchanged.

        `base` moves the charts and the cash cards only. The fiscal block is always
        computed on the `incasso` reading, because the forfettario is taxed on what was
        collected in the calendar year and a chart that reads by accrual period must not
        move the taxes (`docs/design/DECISIONS.md`, 2026-09-10). Within one year the two
        readings' totals agree except for a document whose period and payment fall in
        different years, which is exactly the case where the tax must follow the money.

        Under `competenza`, the default, that is the cash view read twice: eight
        aggregates instead of four, on a page that already runs a dozen. Accepted, rather
        than threading two readings through `cash_overview`, whose one job is one reading.

        `concentrazione_clienti` (§1.5, REB-370) is on no reading switch: it is
        `revenue_by_customer(anno)` verbatim, a whole-practice, calendar-year share of
        `annual_revenue` -- a different money entirely from `cassa` (cash, VAT included)
        and open to every role the way the rest of this page is, not gated to `admin`
        with the fiscal block.
        """
        cassa = self.cash_overview(anno, actor, base)
        per_fisco = cassa if base == "incasso" else self.cash_overview(anno, actor, "incasso")
        fiscale = fiscale_proiettato = None
        netto = netto_proiettato = None
        if actor.role == "admin":
            try:
                fiscale = self.get_fiscal_estimate(anno, actor, ricavi=per_fisco.incassato)
                fiscale_proiettato = self.get_fiscal_estimate(
                    anno, actor, ricavi=per_fisco.proiettato
                )
            except NotFound:
                # No fiscal profile yet: the page says so and shows the cash alone.
                fiscale = fiscale_proiettato = None
        if fiscale is not None and fiscale.totale_dovuto is not None:
            netto = round_money(per_fisco.lordo_effettivo - fiscale.totale_dovuto)
        if fiscale_proiettato is not None and fiscale_proiettato.totale_dovuto is not None:
            netto_proiettato = round_money(
                per_fisco.lordo_proiettato - fiscale_proiettato.totale_dovuto
            )
        return EconomicOverview(
            calcolato_alle=datetime.now(UTC),
            cassa=cassa,
            fiscale=fiscale,
            fiscale_proiettato=fiscale_proiettato,
            concentrazione_clienti=[
                RevenueByCustomer(**row._asdict()) for row in self.repo.revenue_by_customer(anno)
            ],
            netto_effettivo=netto,
            netto_proiettato=netto_proiettato,
        )

    def get_fiscal_estimate(
        self, anno: int, actor: Actor, *, ricavi: Decimal | None = None
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
        """
        actor.require_admin("get_fiscal_estimate")
        # Raises `NotFound("fiscal_profile", "singleton")` when nothing is configured,
        # which tells the user which screen to go to -- better than an estimate of zero
        # computed from three nulls, which reads as "you owe nothing".
        profile = FiscalProfileService(self.session).get(actor)
        if ricavi is None:
            # Every issued invoice of the year, deal or no deal: the estimate is about
            # the person's income, and an invoice attached to no deal is still income.
            # Reduced by the pack's own tagged charges (REB-352 §2, §6's resolved
            # decision): a rivalsa line counts toward the ceiling in full but is not
            # taxable income, so it never reaches the coefficiente base here even
            # though `annual_revenue` sums it in full.
            pack = resolve_pack(profile.pack_id, profile.pack_version)
            ricavi = taxable_ricavi(pack, self.session, anno, self.repo.annual_revenue(anno))
        return estimate_income(
            anno=anno,
            # `ricavi` overrides the reduced figure above when the caller asks "what
            # if": the economic overview passes what was collected, and what is
            # projected, neither of which the pack's own tag applies to.
            ricavi=ricavi,
            coefficiente=profile.coefficiente_redditivita,
            aliquota_sostitutiva=profile.aliquota_imposta_sostitutiva,
            aliquota_inps=profile.aliquota_inps,
        )

    def bind_time_to_invoice(
        self, deal_id: UUID, data: BindTimeRequest, actor: Actor
    ) -> InvoiceRead:
        """Turns selected hours into the lines of a **draft** invoice, and writes the
        link back onto each of them.

        **Issues nothing and computes no total.** It builds an `InvoiceCreate` with its
        lines and calls `InvoiceService`, which stays the sole owner of numbering,
        fiscal validation, rounding and freezing (slice 3 §3, §6, §9). Nothing here
        joins slice 3's locked transaction -- the part of the system that least wants
        new participants.

        `admin`, and **no MCP tool** (§11's exclusion list). Binding hours to a draft is
        harmless while the draft is a draft, but it is the step that determines their
        freezing at issue, and choosing *which* hours to invoice is a commercial
        decision. Slice 3 §11 withdrew `issue_invoice` from MCP with the same reasoning;
        this is the rung immediately below it.

        Which reading of "already invoiced" applies, since task 4B-3 deliberately left
        two that differ: the double-invoicing guard below uses the **invoice-state** one,
        through `billed_entry_ids`, which joins to `invoices` and asks whether the line
        belongs to an *issued* document. The **link-based** reading -- `invoice_line_id
        IS NOT NULL`, one indexed column, what the `fatturato` list filter applies --
        would refuse to rebind an hour sitting on a draft nobody ever issued, and that
        draft may be a mistake somebody is trying to redo. This method is what writes the
        link, so it is what makes the two readings differ at all; every P&L figure
        downstream reads the invoice-state one for the same reason, because a draft is
        not revenue.

        The link is written **when the draft line is born**, not at issue: the FK is
        `ON DELETE SET NULL`, so while the invoice is a draft the hours stay modifiable
        and slice 3's wholesale line replacement unbinds and rebinds them without
        orphans; the moment `issue()` commits, the bound hours are frozen without
        `issue()` having had to know they exist.

        **The rounding disagreement, and which figure wins.** A line's `prezzo_totale`
        is `ROUND(Σ ore × tariffa, 2)`, while the timesheet prints
        `Σ ROUND(ore × tariffa, 2)`. The two differ by cents -- three entries of 0.10 h
        at 33.333333 EUR/h are 9.99 per entry and 10.00 as one line -- and §6.2 states
        this as its one exception rather than reconciling it: hours × rate is an
        *estimate* (`valore_maturato`), and it stops being consulted the moment an
        invoice exists. From then on the revenue is the invoice's own `imponibile`, the
        figure on the document the client received.
        """
        actor.require_admin("bind_time_to_invoice")
        deal = self.deals.get(deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)

        # De-duplicated, order preserved. The same id twice in one request would land in
        # one group twice and bill those hours twice on a single line -- silently, since
        # every count downstream would still agree with itself.
        wanted = list(dict.fromkeys(data.entry_ids))
        rows: list[TimeEntry] = []
        for entry_id in wanted:
            entry = self.session.get(TimeEntry, entry_id)
            # Never a silent skip: a dropped id produces a draft missing work the user
            # believed they had selected, and they find out from the client.
            if entry is None:
                raise NotFound("time_entry", entry_id)
            rows.append(entry)

        stray = [str(e.id) for e in rows if e.deal_id != deal_id or e.deleted_at is not None]
        if stray:
            raise ValidationFailed(
                "time_entry",
                "entry_ids",
                f"{len(stray)} voci non appartengono a questo deal o sono archiviate",
                expected=f"solo voci del deal {deal_id}: {', '.join(stray)}",
            )

        # A property of the work itself, decided when it was logged -- an internal
        # meeting, a rewrite nobody agreed to pay for -- so it never belongs on a draft,
        # whatever rate it happens to carry.
        internal = [str(e.id) for e in rows if not e.fatturabile]
        if internal:
            raise ValidationFailed(
                "time_entry",
                "entry_ids",
                f"{len(internal)} voci non sono fatturabili",
                expected=f"solo voci fatturabili: {', '.join(internal)}",
            )

        # Split in one pass rather than filtered twice, so that below `tariffa` is a
        # `Decimal` the type checker can follow instead of a `Decimal | None` narrowed
        # by an `assert` standing exactly where the missing price would come through.
        priced: list[tuple[Decimal, TimeEntry]] = []
        unpriced: list[TimeEntry] = []
        for entry in rows:
            tariffa = entry.tariffa_applicata
            if tariffa is None:
                unpriced.append(entry)
            else:
                priced.append((tariffa, entry))
        if unpriced:
            # Counted in the reason and listed in `expected`, so the refusal is an
            # instruction -- "give these two entries a rate" -- and not an obstacle. An
            # invoice line with no unit price is not issuable, and inventing one here
            # would decide on the user's behalf what their work is worth.
            raise ValidationFailed(
                "time_entry",
                "entry_ids",
                f"{len(unpriced)} voci non hanno una tariffa: assegnane una prima di "
                "generare la bozza",
                expected=(
                    "una tariffa su ogni voce selezionata: "
                    f"{', '.join(str(e.id) for e in unpriced)}"
                ),
            )

        already = billed_entry_ids(self.session, rows)
        if already:
            frozen = [e.invoice_line_id for e in rows if e.id in already]
            numero, anno = self.session.execute(
                select(Invoice.numero, Invoice.anno)
                .join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id)
                .where(InvoiceLine.id == frozen[0])
            ).one()
            # The document is named, because "already invoiced" is only actionable if
            # the user can go and look at the invoice in question.
            raise Conflict(
                "time_entry",
                f"{len(already)} voci sono già su una fattura emessa: non si fattura "
                "due volte lo stesso lavoro",
                voci=len(already),
                numero=numero,
                anno=anno,
            )

        # One line per `(tariffa, mese)`. Not one per entry: a forty-line invoice is
        # unreadable for the client, and the detail already has its place -- the
        # timesheet attached to it (§10.2). `raggruppa_per_mese = False` collapses to one
        # line per rate, which is a presentation choice the caller makes and not a rule.
        groups: dict[tuple[Decimal, tuple[int, int] | None], list[TimeEntry]] = defaultdict(list)
        for tariffa, entry in priced:
            mese = (entry.data.year, entry.data.month) if data.raggruppa_per_mese else None
            groups[(tariffa, mese)].append(entry)
        ordered = sorted(groups.items(), key=lambda item: _group_order(item[0]))

        righe: list[InvoiceLineIn] = []
        for (tariffa, mese), members in ordered:
            ore = sum_hours([m.ore for m in members])
            etichetta = f" {period_label(*mese)}" if mese is not None else ""
            righe.append(
                InvoiceLineIn(
                    # Names the month and the hours, which is what makes a six-line
                    # invoice legible next to a timesheet the client can check it
                    # against.
                    #
                    # A plain hyphen, never the em dash this codebase uses everywhere
                    # else in prose: `Descrizione` goes into the FatturaPA XML, whose
                    # FPR12 profile admits only basic-latin and Latin-1 code points, and
                    # U+2014 is in neither. With an em dash here every draft this method
                    # built was refused at `issue` -- and only there, one step after the
                    # user had already selected the hours -- with a message about
                    # characters they never typed. Found by taking a bound draft all the
                    # way to `emessa` over HTTP in `apps/api/tests/test_analytics_api.py`.
                    # Since ORB-140 the writer spells an em dash as a hyphen itself, so
                    # this is the plain spelling by choice rather than by necessity.
                    descrizione=f"Attività{etichetta} - {ore} ore",
                    quantita=ore,
                    unita_misura="ore",
                    prezzo_unitario=tariffa,
                    # `aliquota_iva` deliberately omitted: `None` means "the regime's
                    # answer", and `natura`/`riferimento_normativo` are not on this
                    # schema at all, precisely so that a caller cannot put the table
                    # constraint `(aliquota_iva = 0) = (natura IS NOT NULL)` within reach
                    # of a request body. The fiscal profile is `InvoiceService`'s to read.
                )
            )

        storage = (
            self.storage if self.storage is not None else storage_from_settings(get_settings())
        )
        invoice = InvoiceService(self.session, storage).create(
            InvoiceCreate(
                customer_id=deal.customer_id,
                deal_id=deal_id,
                tipo="fattura",
                righe=righe,
            ),
            actor,
        )

        # The line ids read back in `numero_linea` order, which `_computed_lines`
        # assigns from the order `righe` was built in -- so the group-to-line mapping is
        # positional and deterministic, rather than matched on a description string that
        # two groups could share.
        line_ids = list(
            self.session.execute(
                select(InvoiceLine.id)
                .where(InvoiceLine.invoice_id == invoice.id)
                .order_by(InvoiceLine.numero_linea)
            ).scalars()
        )
        for line_id, (_, members) in zip(line_ids, ordered, strict=True):
            for entry in members:
                entry.invoice_line_id = line_id
        self._activities_for_binding(deal_id, invoice, rows, actor)
        self.session.commit()
        return invoice

    def _activities_for_binding(
        self, deal_id: UUID, invoice: InvoiceRead, rows: list[TimeEntry], actor: Actor
    ) -> None:
        """One activity on the deal, not one per entry: binding is a single commercial
        act over a selection, unlike `recalculate_rates`, which changes each row's
        meaning individually and therefore records per row.

        Called after `InvoiceService.create` has committed, never before: `record`
        flushes into the caller's transaction, so an activity written ahead of another
        service's own commit would be persisted by that commit even if this method later
        failed.
        """
        ActivityService(self.session).record(
            "deal",
            deal_id,
            "time_bound_to_invoice",
            actor,
            {"invoice_id": str(invoice.id), "voci": len(rows)},
        )


__all__ = ["AnalyticsService"]
