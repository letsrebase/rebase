"""What an engagement pays the freelancer, and when, as pure functions (collaborators design
`2026-11-09-collaborators-in-the-crm-design.md` § 4, «Due items» and «The payment rule»).

`billings` says the billing points the letter sets and the amount of each; `due_terms` says how
each is dated. Nothing here reads a session: `forecast.py` writes the due items from them.

* A **day** or **hour** letter bills each month at month end, for the days the engagement's
  `days_per_week` commits in it (a week is five working days), never past `expected_days`
  in total. An hour is `HOURS_PER_DAY` of a day, the one place hours become days. With no
  `days_per_week` nothing can be estimated, so nothing is forecast.
* A **flat** letter bills its fee once, at the end of the month its `ends_on` falls in; with no
  end there is no date to say.
* `fixed_term` is dated by `due_date_from_terms` from the billing date.
* `on_collection` is `after_settlement` with `collection_days` and **no fixed-term floor** (the
  printed clause names none, design § 4), capped by `not_after`: the 60 days of law 81/2017
  art. 3 c. 1, counted from the billing date, which stands for the day the invoice or receipt
  arrives until a document says otherwise.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import NAMESPACE_OID, UUID, uuid5

from pigrocrm.core.collaborators.models import PAYMENT_DAYS_LIMIT, Engagement
from pigrocrm.core.db.clock import month_bounds
from pigrocrm.core.due_items.schemas import DueRule
from pigrocrm.core.invoices.due_date import due_date_from_terms

SOURCE_KIND = "engagement"
SOURCE_RULE = "engagement_payment"
HOURS_PER_DAY = Decimal(8)
WORKING_DAYS_PER_WEEK = 5
# How far ahead an open-ended engagement is forecast, in whole months after the current one.
HORIZON_MONTHS = 12
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Billing:
    """One billing point: the days it covers, when the freelancer bills them and for how much."""

    period_start: dt.date
    period_end: dt.date
    billing_date: dt.date
    amount: Decimal


@dataclass(frozen=True)
class Terms:
    """How a billing point is dated: the date the document would state, and the rule."""

    own_date: dt.date | None
    rule: DueRule


def forecast_key(engagement_id: UUID, billing_date: dt.date) -> UUID:
    """The identity of one engagement forecast: found again by the same engagement and billing
    date, whatever its amount becomes."""
    return uuid5(NAMESPACE_OID, f"{SOURCE_KIND}:{engagement_id}:{billing_date.isoformat()}")


def horizon_end(today: dt.date) -> dt.date:
    """The last day of the month `HORIZON_MONTHS` after today's."""
    index = today.year * 12 + today.month - 1 + HORIZON_MONTHS
    return month_bounds(index // 12, index % 12 + 1)[1]


def _working_days(first: dt.date, last: dt.date) -> int:
    days = (last - first).days + 1
    return sum(1 for n in range(days) if (first + dt.timedelta(days=n)).weekday() < 5)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def billings(engagement: Engagement, today: dt.date) -> list[Billing]:
    """Every billing point of the engagement, in date order, up to its end or the horizon.

    Elapsed ones are included: which of them still matter is `forecast.py`'s window."""
    if engagement.fee_unit == "flat":
        if engagement.ends_on is None:
            return []
        end = engagement.ends_on
        billing_date = month_bounds(end.year, end.month)[1]
        return [Billing(engagement.starts_on, end, billing_date, _cents(engagement.fee))]
    if engagement.days_per_week is None:
        return []
    last_day = engagement.ends_on or horizon_end(today)
    last_day = min(last_day, horizon_end(today))
    unit = HOURS_PER_DAY if engagement.fee_unit == "hour" else Decimal(1)
    remaining = engagement.expected_days
    out: list[Billing] = []
    year, month = engagement.starts_on.year, engagement.starts_on.month
    while dt.date(year, month, 1) <= last_day:
        first_of_month, last_of_month = month_bounds(year, month)
        first = max(engagement.starts_on, first_of_month)
        last = min(last_day, last_of_month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        weekdays = _working_days(first, last)
        days = engagement.days_per_week * weekdays / WORKING_DAYS_PER_WEEK
        if remaining is not None:
            days = min(days, remaining)
            remaining -= days
        amount = _cents(engagement.fee * days * unit)
        if amount > 0:
            out.append(Billing(first, last, last_of_month, amount))
        if remaining is not None and remaining <= 0:
            break
    return out


def due_terms(engagement: Engagement, billing_date: dt.date) -> Terms:
    """How the freelancer's item for `billing_date` is dated, from the engagement's rule."""
    if engagement.payment_rule == "fixed_term":
        assert engagement.payment_days is not None
        due = due_date_from_terms(
            billing_date, engagement.payment_days, month_end=engagement.payment_end_of_month
        )
        return Terms(due, DueRule(kind="fixed"))
    assert engagement.collection_days is not None
    return Terms(
        None,
        DueRule(
            kind="after_settlement",
            offset_days=engagement.collection_days,
            not_after=billing_date + dt.timedelta(days=PAYMENT_DAYS_LIMIT),
        ),
    )
