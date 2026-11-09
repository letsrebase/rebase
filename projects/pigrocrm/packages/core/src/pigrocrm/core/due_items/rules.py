"""The four due rules and the one function that dates an item from them (spec 2026-11-09,
due items, § 1.3).

Pure: it reads what the dependency looks like and nothing else, so the service can apply it
to a whole chain and a test can drive it without a database.

* `fixed`: the item's own date.
* `after_settlement`: the dependency's settlement date plus `offset_days`, or never earlier
  than the item's own date; never later than `not_after`. A freelancer paid when the client
  pays, capped by the legal backstop of 60 days from receipt.
* `month_after_settlement_on_day`: that day of the month after the dependency's settlement
  (a withholding is due on the 16th of the month after the payment).
* `on_settlement`: due when the dependency settles.

While the dependency is not settled, the date is **projected** from its own due date and the
result is flagged `estimated`, so the schedule draws it lighter. A dependency counts as
settled only when it is settled in full: a part-paid item does not date its dependents
(collaborators design, « Pairing »). With nothing to project from, `not_after` still dates
the item: it waits, and the backstop says when it stops waiting.
"""

import datetime as dt
from dataclasses import dataclass

from pigrocrm.core.due_items.models import LATEST_DAY_OF_MONTH


@dataclass(frozen=True)
class Anchor:
    """What a dependent reads of the item it depends on."""

    state: str
    # The dependency's own due date, itself projected when it follows another item.
    due_date: dt.date | None
    # The date of the settlement that completed it, once it is settled.
    settled_on: dt.date | None


@dataclass(frozen=True)
class Resolved:
    date: dt.date | None
    estimated: bool


def month_after(day: dt.date, day_of_month: int) -> dt.date:
    """`day_of_month` of the month after `day`'s. The day is at most 28 (a CHECK), so every
    month has it."""
    if not 1 <= day_of_month <= LATEST_DAY_OF_MONTH:
        raise ValueError(f"day of month {day_of_month} is not in 1..{LATEST_DAY_OF_MONTH}")
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return dt.date(year, month, day_of_month)


def resolve(
    kind: str,
    *,
    own_date: dt.date | None,
    offset_days: int,
    day_of_month: int | None,
    not_after: dt.date | None,
    anchor: Anchor | None,
) -> Resolved:
    """The date an item falls due on, and whether it is only an estimate."""
    if kind == "fixed":
        return Resolved(own_date, False)

    settled = anchor is not None and anchor.state == "settled" and anchor.settled_on is not None
    reference: dt.date | None = None
    if anchor is not None and anchor.state != "dropped":
        reference = anchor.settled_on if settled else anchor.due_date

    projected: dt.date | None = None
    if reference is not None:
        if kind == "after_settlement":
            projected = reference + dt.timedelta(days=offset_days)
        elif kind == "month_after_settlement_on_day":
            if day_of_month is None:
                raise ValueError("month_after_settlement_on_day needs a day of the month")
            projected = month_after(reference, day_of_month)
        elif kind == "on_settlement":
            projected = reference
        else:
            raise ValueError(f"unknown due rule {kind!r}")
    elif kind not in ("after_settlement", "month_after_settlement_on_day", "on_settlement"):
        raise ValueError(f"unknown due rule {kind!r}")

    # Only `after_settlement` carries a date of its own: the later of the two.
    if kind == "after_settlement":
        known = [day for day in (own_date, projected) if day is not None]
        date = max(known) if known else None
    else:
        date = projected

    if not_after is not None and (date is None or date > not_after):
        date = not_after
    return Resolved(date, not settled)
