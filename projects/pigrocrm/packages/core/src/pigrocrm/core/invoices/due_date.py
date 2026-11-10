"""The due date of an invoice from the terms agreed with its customer (REB-326).

Two numbers describe every term this product knows: the days after the invoice date,
and whether the result then slides to the end of its month. «30 giorni data fattura fine
mese», the most common Italian spelling, is `(30, True)`; the fiscal profile's own
`due_days` is `(n, False)`. The service reads the pair from the customer, falling
back to the profile's days, and calls this; nothing else in the product computes a due
date, so the digest, the reminders, the calendar and the XML all read one arithmetic.
"""

from collections.abc import Collection
from datetime import date, timedelta
from typing import Protocol
from uuid import UUID

from pigrocrm.core.db.clock import month_bounds


def due_date_from_terms(issue_date: date, days: int, *, month_end: bool) -> date:
    """`issue_date` plus `days`, then the last day of that month when `month_end`.

    The days come first and the month is the one they land in: 22 September plus thirty
    is 22 October, so end of month is 31 October. Applying the slide before the days
    (end of September, then thirty days) would answer 30 October, which is not what the
    customer signed. The month's last day comes from `month_bounds`, not "day 30/31":
    February.
    """
    due_date = issue_date + timedelta(days=days)
    if not month_end:
        return due_date
    return month_bounds(due_date.year, due_date.month)[1]


class PaymentTerms(Protocol):
    """What `InvoiceRepository.payment_terms` answers, or anything that remembers its answers."""

    def payment_terms(
        self, customer_ids: Collection[UUID]
    ) -> dict[UUID, tuple[int | None, bool]]: ...


def due_date_for_customer(
    repo: PaymentTerms, issue_date: date, customer_id: UUID, profile_days: int
) -> date:
    """The due date the customer's terms give from `issue_date` (REB-326): the customer's days
    when it has them, the fiscal profile's `profile_days` otherwise; the end-of-month slide is
    the customer's alone. Emission (`InvoiceContext.due_date_from_terms`) and the forecasts of
    money in (`due_items/forecast.py`) both call this, so a forecast is dated as the invoice
    that replaces it will be."""
    days, month_end = repo.payment_terms({customer_id}).get(customer_id, (None, False))
    return due_date_from_terms(
        issue_date, profile_days if days is None else days, month_end=month_end
    )
