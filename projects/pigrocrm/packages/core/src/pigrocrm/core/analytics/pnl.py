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

type DealState = Literal["in_progress", "to_invoice", "closed"]


def _totals(rows: list[tuple[Decimal, Decimal, Decimal]]) -> PnlTotals:
    """One column of `(ricavi, costi diretti, costo lavoro)` triples, summed.

    `sum_money` and not `sum()`: the rows arriving here are already rounded per deal, and
    the column total has to be the sum of the printed figures rather than the rounding of
    an exact sum -- the same rule the timesheet obeys, for the same reason (§6.2).
    """
    revenue = sum_money([row[0] for row in rows])
    costs = sum_money([row[1] for row in rows])
    work = sum_money([row[2] for row in rows])
    margin = revenue - costs - work
    return PnlTotals(
        revenue=revenue,
        direct_costs=costs,
        work_cost=work,
        gross_margin=margin,
        percentage_margin=percentage_of(margin, revenue),
        deal=len(rows),
    )


def deal_states(session: Session, deal_ids: Collection[UUID]) -> dict[UUID, DealState]:
    """`TimeEntryService.deal_summary(...).status` for many deals at once, in three
    statements instead of one summary (and its entries) per deal.

    The same rule: a deal in an open stage is `in_progress`; otherwise it is `to_invoice`
    while any billable hour is not on an issued invoice (`billed_entry_ids`, the
    invoice-state reading), `closed` when none is.
    """
    if not deal_ids:
        return {}
    kinds = {
        deal_id: kind
        for deal_id, kind in session.execute(
            select(Deal.id, PipelineStage.type)
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
                    TimeEntry.billable.is_(True),
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
            states[deal_id] = "in_progress"
        else:
            states[deal_id] = "to_invoice" if deal_id in with_unbilled else "closed"
    return states


def deal_pnl(session: Session, repo: AnalyticsRepository, deal_id: UUID, actor: Actor) -> DealPnl:
    """The rows of §7.1, each from its one stated source.

    `valore_maturato = ricavi + valore delle ore fatturabili non fatturate`. It is
    **not** revenue, enters no P&L row, and is returned in a field of its own name
    (§7.3): on an `in_progress` deal it is the honest figure and the margin is
    provisional; the margin is only reportable when the state is `closed`.

    Which reading of "already invoiced" this uses, since task 4B-3 left two that
    deliberately differ: the **invoice-state** one, through `billed_entry_ids`, not
    the link-based one the `invoiced` list filter applies. An hour bound to a line of
    a *draft* invoice is `invoiced` in that list and still fully editable; here it
    still counts as billable-and-unbilled, because a draft is not revenue and the
    link-based reading would leave the work in neither figure -- priced, done and
    invisible until somebody pressed "issue".
    """
    deal = DealRepository(session).get(deal_id)
    if deal is None:
        raise NotFound("deal", deal_id)

    revenue, invoices = repo.deal_revenue(deal_id)
    direct_costs = repo.deal_direct_costs(deal_id)
    # Reuses 4A's own summary rather than recomputing hours here: one definition of
    # "labour cost" and one of "state", so the Ore tab and the Economia tab can never
    # disagree about the same deal.
    summary = TimeEntryService(session).deal_summary(deal_id, actor)
    margin = revenue - direct_costs - summary.work_cost

    return DealPnl(
        deal_id=deal_id,
        status=summary.status,
        revenue=revenue,
        direct_costs=direct_costs,
        work_cost=summary.work_cost,
        gross_margin=margin,
        # `percentage_of` returns `None` for a zero denominator and performs no
        # division at all in that case, so this is the only place the question is
        # asked and there is no second answer to keep aligned.
        percentage_margin=percentage_of(margin, revenue),
        total_hours=summary.total_hours,
        billable_uninvoiced_hours=summary.billable_uninvoiced_hours,
        accrued_value=revenue + summary.unbilled_hours_value,
        hours_without_rate=summary.hours_without_rate,
        issued_invoices=invoices,
    )


def period_pnl(session: Session, repo: AnalyticsRepository, query: PeriodPnlQuery) -> PeriodPnl:
    """Aggregated over a date window, optionally scoped to one customer.

    Each quantity is attributed to the period by **its own** date: revenue by
    `invoices.issue_date`, costs by `costs.date`, labour cost by
    `time_entries.date`. Not by the deal's date, which does not exist, and not by one
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
    if query.date_to < query.date_from:
        raise ValidationFailed(
            ENTITY, "to", "intervallo invertito", expected="una data non anteriore a 'from'"
        )

    # `legal_entity_id` is one more predicate on every read (REB-630, spec 2026-10-03
    # §1.9) and nothing else: the arithmetic below does not know it exists.
    legal_entity = query.legal_entity_id
    revenue = repo.revenue_in_range(
        query.date_from, query.date_to, query.customer_id, query.base, legal_entity
    )
    per_deal_costs, general = repo.costs_in_range(
        query.date_from, query.date_to, query.customer_id, legal_entity
    )
    labour = repo.labour_cost_in_range(
        query.date_from, query.date_to, query.customer_id, legal_entity
    )
    # Slice 6 §5's three rows, from the same aggregate `unbilled_backlog` uses and
    # merely bounded to the period: one definition of "ore fatturabili non fatturate",
    # not one per screen. They are informative and enter no margin, which is why they
    # sit outside both `PnlTotals` columns rather than inside either.
    backlog_hours, value_backlog, without_rate, _ = repo.unbilled_backlog(
        query.date_from, query.date_to, query.customer_id, legal_entity
    )

    deals = repo.deals_in_range(
        query.date_from, query.date_to, query.customer_id, query.base, legal_entity
    )
    # The state comes from the same rule the deal's own P&L gets it from, so the two
    # screens can never disagree about which column a deal belongs in -- and, in
    # particular, both read "already invoiced" as the *invoice state* through
    # `billed_entry_ids`, never as the link-based `invoiced` list filter. An hour bound
    # to a line of a draft invoice is not revenue yet.
    states = deal_states(session, [deal.id for deal in deals])
    closed: list[tuple[Decimal, Decimal, Decimal]] = []
    in_progress: list[tuple[Decimal, Decimal, Decimal]] = []
    for deal in deals:
        row = (
            revenue.get(deal.id, ZERO_MONEY),
            per_deal_costs.get(deal.id, ZERO_MONEY),
            labour.get(deal.id, ZERO_MONEY),
        )
        (closed if states[deal.id] == "closed" else in_progress).append(row)

    locks = PeriodLockService(session)
    # A window is only as closed as its least-closed month: reporting a quarter as
    # closed because one of its months is would be the wrong reassurance in the one
    # place it matters.
    closed_period = all(
        locks.is_closed(date(year, month, 1)) is not None
        for year, month in months_between(query.date_from, query.date_to)
    )

    return PeriodPnl(
        date_from=query.date_from,
        date_to=query.date_to,
        customer_id=query.customer_id,
        base=query.base,
        legal_entity_id=legal_entity,
        closed=_totals(closed),
        in_progress=_totals(in_progress),
        # Never apportioned onto any deal (§7.4), and absent entirely under a
        # customer filter, because a general expense belongs to no customer.
        general_expenses=general,
        accrued_value=value_backlog,
        billable_uninvoiced_hours=backlog_hours,
        hours_without_rate=without_rate,
        closed_period=closed_period,
        # Free: a COUNT over two columns that already exist, and the one thing a
        # reader most needs to know about an open period (§6.4).
        written_entries_overdue=repo.late_entry_count(query.date_from, query.date_to, legal_entity),
    )
