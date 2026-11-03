"""The year as cash, month by month, and the contract dates overlaid on it
(`AnalyticsService.cash_overview`)."""

from datetime import date
from uuid import UUID

from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import CashBase, CashMonth, CashOverview, ContractDateMarker
from pigrocrm.core.contracts.dates import irrevocability_window_end, renewal_deadline
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.db import today_local
from pigrocrm.core.money import ZERO_MONEY, round_money, share, sum_money


def contract_date_markers(
    contracts: ContractRepository, year: int, legal_entity_id: UUID | None = None
) -> list[ContractDateMarker]:
    """REB-352 §1.6's overlay: every irrevocability-window close and renewal
    deadline that falls inside `year`'s calendar, across every non-deleted
    contract. The irrevocability date is measured "as of" today regardless of
    `year` -- a notice period is a forward-looking promise, never a fact about a
    past year -- so a past `year` never grows one and a future one only does
    once today's window actually reaches into it. One azienda's calendar carries its
    own contracts' dates (REB-630).
    """
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    today = today_local()
    markers: list[ContractDateMarker] = []
    for contract in contracts.list_active(legal_entity_id=legal_entity_id):
        end = irrevocability_window_end(contract, today)
        if end is not None and year_start <= end <= year_end:
            markers.append(
                ContractDateMarker(
                    contract_id=contract.id,
                    title=contract.title,
                    customer_id=contract.customer_id,
                    type="irrevocability_end",
                    date=end,
                )
            )
        deadline = renewal_deadline(contract)
        if deadline is not None and year_start <= deadline <= year_end:
            markers.append(
                ContractDateMarker(
                    contract_id=contract.id,
                    title=contract.title,
                    customer_id=contract.customer_id,
                    type="renewal_due",
                    date=deadline,
                )
            )
    markers.sort(key=lambda m: (m.date, m.title))
    return markers


def cash_overview(
    repo: AnalyticsRepository,
    contracts: ContractRepository,
    year: int,
    base: CashBase,
    legal_entity_id: UUID | None,
) -> CashOverview:
    """The year as cash, month by month (`CashOverview`): every figure is a SUM the
    repository produced plus additions done once, here. `base` says which month a
    document falls in (ORB-133). The year's totals agree under the two readings except
    for a document whose declared period and whose money fall in different years: that
    one is in one year's view and not the other's. `legal_entity_id` narrows every sum to
    one azienda (REB-630, spec 2026-10-03 §1.9); without it the cash of every azienda
    adds up, which is what «tutte» exists to show, and a cost without a deal is counted
    there alone (§1.7)."""
    paid = repo.monthly_paid(year, base, legal_entity_id)
    unpaid = repo.monthly_unpaid(year, base, legal_entity_id)
    drafts = repo.monthly_drafts(year, base, legal_entity_id)
    costs = repo.monthly_costs(year, legal_entity_id)
    months = range(1, 13)
    zero = ZERO_MONEY
    stack_trend = max((paid.get(m, zero) + costs.get(m, zero) for m in months), default=zero)
    stack_projection = max(
        (
            paid.get(m, zero) + unpaid.get(m, zero) + drafts.get(m, zero) + costs.get(m, zero)
            for m in months
        ),
        default=zero,
    )

    mesi = [
        CashMonth(
            year=year,
            month=m,
            paid=paid.get(m, zero),
            unpaid=unpaid.get(m, zero),
            drafts=drafts.get(m, zero),
            costs=costs.get(m, zero),
            trend_stack=round_money(paid.get(m, zero) + costs.get(m, zero)),
            projection_stack=round_money(
                paid.get(m, zero) + unpaid.get(m, zero) + drafts.get(m, zero) + costs.get(m, zero)
            ),
            trend_shares={
                "paid": share(paid.get(m, zero), stack_trend),
                "costs": share(costs.get(m, zero), stack_trend),
            },
            projection_shares={
                "paid": share(paid.get(m, zero), stack_projection),
                "unpaid": share(unpaid.get(m, zero), stack_projection),
                "drafts": share(drafts.get(m, zero), stack_projection),
                "costs": share(costs.get(m, zero), stack_projection),
            },
        )
        for m in months
    ]
    tot_paid = sum_money(paid.values())
    tot_unpaid = sum_money(unpaid.values())
    tot_drafts = sum_money(drafts.values())
    tot_costs = sum_money(costs.values())
    projected = round_money(tot_paid + tot_unpaid + tot_drafts)
    return CashOverview(
        year=year,
        base=base,
        legal_entity_id=legal_entity_id,
        paid=tot_paid,
        unpaid=tot_unpaid,
        drafts=tot_drafts,
        projected=projected,
        costs=tot_costs,
        actual_gross=round_money(tot_paid - tot_costs),
        projected_gross=round_money(projected - tot_costs),
        months=mesi,
        contractual_due_dates=contract_date_markers(contracts, year, legal_entity_id),
    )
