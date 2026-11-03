"""The due date of an invoice from the terms agreed with its customer (REB-326).

Two numbers describe every term this product knows: the days after the invoice date,
and whether the result then slides to the end of its month. «30 giorni data fattura fine
mese», the most common Italian spelling, is `(30, True)`; the fiscal profile's own
`due_days` is `(n, False)`. The service reads the pair from the customer, falling
back to the profile's days, and calls this; nothing else in the product computes a due
date, so the digest, the reminders, the calendar and the XML all read one arithmetic.
"""

from datetime import date, timedelta

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
