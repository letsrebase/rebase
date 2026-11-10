"""Estimate against actual, per deal (`AnalyticsService.budget_vs_actual`)."""

from decimal import Decimal

from sqlalchemy.orm import Session

from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.schemas import BudgetPage, BudgetQuery, BudgetVsActualRow
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.money import ZERO_HOURS, ZERO_MONEY, percentage_of, round_money, sum_money

ENTITY = "analytics"


def comparable(estimate: Decimal | None) -> Decimal | None:
    """The estimate itself when it can be compared against, `None` when it cannot.

    An estimate is comparable only when it is present **and** greater than zero. `None`
    means nobody estimated; `0` reads to any division as "zero hours estimated, infinite
    overrun". Both are refused as denominators here (the secondary defence §9.2 requires
    regardless of A14).

    Returns the narrowed value rather than a `bool` on purpose: a predicate would leave
    every call site holding a `Decimal | None` the type checker cannot follow, and the
    usual repair is a `cast` or an `assert`, an escape hatch standing exactly where the
    null this function exists to catch would come through.
    """
    if estimate is None or estimate <= 0:
        return None
    return estimate


def budget_vs_actual(session: Session, repo: AnalyticsRepository, query: BudgetQuery) -> BudgetPage:
    """Estimate against actual, per deal, with the pro-rata comparison beside the
    full one rather than instead of it.

    This is the payment on an investment made in advance: `deals.budgeted_hours`
    and `deals.budgeted_value` have existed since slice 1 and have never been read
    by anybody. The actuals are the one missing half.

    Keyset pagination on `(name, id)`: the cursor is the id of the last deal of the
    previous page, the deals after it are asked of the database, and only the page's
    deals are aggregated. A cursor naming no deal is refused, never read as "page one",
    which would let a client following `next_cursor` loop forever.
    """
    if query.date_to < query.date_from:
        raise ValidationFailed(
            ENTITY, "to", "intervallo invertito", expected="una data non anteriore a 'from'"
        )

    anchor = None
    if query.cursor is not None:
        anchor = session.get(Deal, query.cursor)
        if anchor is None:
            raise ValidationFailed(
                ENTITY,
                "cursor",
                "cursore sconosciuto",
                expected="il next_cursor di una pagina precedente",
            )
    deals = repo.deals_in_range(
        query.date_from,
        query.date_to,
        query.customer_id,
        after=(anchor.name, anchor.id) if anchor is not None else None,
        limit=query.limit + 1,
    )
    window = deals[: query.limit]
    next_cursor = window[-1].id if len(deals) > query.limit and window else None
    ids = [deal.id for deal in window]
    revenue = repo.revenue_in_range(query.date_from, query.date_to, query.customer_id, deal_ids=ids)
    hours = repo.hours_in_range(query.date_from, query.date_to, query.customer_id, deal_ids=ids)

    rows: list[BudgetVsActualRow] = []
    for deal in window:
        actuals_hours = hours.get(deal.id, ZERO_HOURS)
        revenue_total = revenue.get(deal.id, ZERO_MONEY)
        # Narrowed to "comparable or absent" once, here, so that below there is
        # nothing left to divide by that could be null or zero.
        hours_prev = comparable(deal.budgeted_hours)
        value_prev = comparable(deal.budgeted_value)

        progress = percentage_of(actuals_hours, hours_prev) if hours_prev is not None else None
        # The pro-rata needs BOTH estimate columns. With a value estimate and no hours
        # estimate there is no progress to derive it from, and inventing one from the
        # invoiced value would be circular -- the invoiced value is the quantity being
        # judged.
        pro_rata = (
            round_money(value_prev * progress / Decimal(100))
            if (value_prev is not None and progress is not None)
            else None
        )
        rows.append(
            BudgetVsActualRow(
                deal_id=deal.id,
                name=deal.name,
                # The stored column, not the narrowed one: a deal estimated at zero
                # hours should show the zero somebody typed. What `comparable`
                # governs is what may be divided by, never what may be displayed.
                budgeted_hours=deal.budgeted_hours,
                actuals_hours=actuals_hours,
                budgeted_value=deal.budgeted_value,
                revenue=revenue_total,
                hours_progress=progress,
                budget_pro_rata=pro_rata,
                # Measured against the pro-rata, never the full budget: at 40% of the
                # hours, being at 40% of the estimated value is on track, and
                # comparing with 100% would mark every job in progress as
                # underperforming.
                value_variance=(revenue_total - pro_rata) if pro_rata is not None else None,
                hours_variance=(actuals_hours - hours_prev if hours_prev is not None else None),
                average_budgeted_rate=(
                    round_money(value_prev / hours_prev)
                    if (hours_prev is not None and value_prev is not None)
                    else None
                ),
                # How much was realised per hour worked, comparable even between deals
                # of very different sizes. `None` with no hours logged, never `0.00`,
                # which would read as "realised nothing per hour" -- the opposite of the
                # truth on a deal invoiced without a timesheet.
                average_actual_rate=(
                    round_money(revenue_total / actuals_hours) if actuals_hours > 0 else None
                ),
                unbudgeted=hours_prev is None and value_prev is None,
                pro_rata_not_calculable=value_prev is not None and hours_prev is None,
            )
        )

    budgeted = [row for row in rows if not row.unbudgeted]
    return BudgetPage(
        items=rows,
        next_cursor=next_cursor,
        # Only over rows with an estimate: including the unestimated ones would make
        # the aggregate depend on how many deals nobody estimated.
        budgeted_total=sum_money([row.budgeted_value for row in budgeted]),
        revenue_total=sum_money([row.revenue for row in budgeted]),
        budgeted_deals=len(budgeted),
        unbudgeted_deals=len(rows) - len(budgeted),
    )
