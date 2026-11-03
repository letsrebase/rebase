"""The profit and loss of one deal and of a period (`AnalyticsService.deal_pnl`,
`AnalyticsService.period_pnl`)."""

from collections.abc import Collection
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.days import months_between
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import DealPnl, PeriodPnl, PeriodPnlQuery, PnlTotals
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.money import ZERO_MONEY, percentage_of, sum_money
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.service import TimeEntryService, billed_entry_ids

ENTITY = "analytics"

type DealState = Literal["in corso", "da fatturare", "chiuso"]


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


def deal_states(session: Session, deal_ids: Collection[UUID]) -> dict[UUID, DealState]:
    """`TimeEntryService.deal_summary(...).stato` for many deals at once, in three
    statements instead of one summary (and its entries) per deal.

    The same rule: a deal in an open stage is `in corso`; otherwise it is `da fatturare`
    while any billable hour is not on an issued invoice (`billed_entry_ids`, the
    invoice-state reading), `chiuso` when none is.
    """
    if not deal_ids:
        return {}
    kinds = {
        deal_id: kind
        for deal_id, kind in session.execute(
            select(Deal.id, PipelineStage.tipo)
            .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
            .where(Deal.id.in_(deal_ids))
        )
    }
    settled = [deal_id for deal_id, kind in kinds.items() if kind != "open"]
    entries = (
        list(
            session.scalars(
                select(TimeEntry).where(
                    TimeEntry.deal_id.in_(settled),
                    TimeEntry.deleted_at.is_(None),
                    TimeEntry.fatturabile.is_(True),
                )
            )
        )
        if settled
        else []
    )
    billed = billed_entry_ids(session, entries)
    with_unbilled = {entry.deal_id for entry in entries if entry.id not in billed}
    states: dict[UUID, DealState] = {}
    for deal_id, kind in kinds.items():
        if kind == "open":
            states[deal_id] = "in corso"
        else:
            states[deal_id] = "da fatturare" if deal_id in with_unbilled else "chiuso"
    return states


def deal_pnl(session: Session, repo: AnalyticsRepository, deal_id: UUID, actor: Actor) -> DealPnl:
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
    deal = DealRepository(session).get(deal_id)
    if deal is None:
        raise NotFound("deal", deal_id)

    ricavi, fatture = repo.deal_revenue(deal_id)
    costi_diretti = repo.deal_direct_costs(deal_id)
    # Reuses 4A's own summary rather than recomputing hours here: one definition of
    # "labour cost" and one of "state", so the Ore tab and the Economia tab can never
    # disagree about the same deal.
    summary = TimeEntryService(session).deal_summary(deal_id, actor)
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


def period_pnl(session: Session, repo: AnalyticsRepository, query: PeriodPnlQuery) -> PeriodPnl:
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

    # `azienda_id` is one more predicate on every read (REB-630, spec 2026-10-03
    # §1.9) and nothing else: the arithmetic below does not know it exists.
    azienda = query.azienda_id
    revenue = repo.revenue_in_range(query.da, query.a, query.customer_id, query.base, azienda)
    per_deal_costs, general = repo.costs_in_range(query.da, query.a, query.customer_id, azienda)
    labour = repo.labour_cost_in_range(query.da, query.a, query.customer_id, azienda)
    # Slice 6 §5's three rows, from the same aggregate `unbilled_backlog` uses and
    # merely bounded to the period: one definition of "ore fatturabili non fatturate",
    # not one per screen. They are informative and enter no margin, which is why they
    # sit outside both `PnlTotals` columns rather than inside either.
    ore_arretrate, valore_arretrato, senza_tariffa, _ = repo.unbilled_backlog(
        query.da, query.a, query.customer_id, azienda
    )

    deals = repo.deals_in_range(query.da, query.a, query.customer_id, query.base, azienda)
    # The state comes from the same rule the deal's own P&L gets it from, so the two
    # screens can never disagree about which column a deal belongs in -- and, in
    # particular, both read "already invoiced" as the *invoice state* through
    # `billed_entry_ids`, never as the link-based `fatturato` list filter. An hour bound
    # to a line of a draft invoice is not revenue yet.
    states = deal_states(session, [deal.id for deal in deals])
    chiusi: list[tuple[Decimal, Decimal, Decimal]] = []
    in_corso: list[tuple[Decimal, Decimal, Decimal]] = []
    for deal in deals:
        row = (
            revenue.get(deal.id, ZERO_MONEY),
            per_deal_costs.get(deal.id, ZERO_MONEY),
            labour.get(deal.id, ZERO_MONEY),
        )
        (chiusi if states[deal.id] == "chiuso" else in_corso).append(row)

    locks = PeriodLockService(session)
    # A window is only as closed as its least-closed month: reporting a quarter as
    # closed because one of its months is would be the wrong reassurance in the one
    # place it matters.
    periodo_chiuso = all(
        locks.is_closed(date(anno, mese, 1)) is not None
        for anno, mese in months_between(query.da, query.a)
    )

    return PeriodPnl(
        da=query.da,
        a=query.a,
        customer_id=query.customer_id,
        base=query.base,
        azienda_id=azienda,
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
        voci_scritte_in_ritardo=repo.late_entry_count(query.da, query.a, azienda),
    )
