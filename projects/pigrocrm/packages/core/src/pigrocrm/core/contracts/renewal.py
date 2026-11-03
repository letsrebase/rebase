"""The term of a renewed contract, and which rate cards go across (REB-763).

Pure, with no session: the interesting arithmetic is a month boundary, and a month
boundary is where a model or a hurried query is a day off with nothing downstream able
to tell. Ported from mastro's `contract-renewal-term.ts` (#815, `renewedTerm` and
`rateCardsForRenewedTerm` in `~/projects/personal/mastro`), translated onto this schema's
own columns and with one change: mastro read a duration off a signed renewal message, so
its anchor was the predecessor's expiry; here a person asks for a renewal on the page,
so the term is anchored on its own first day. The two agree whenever the successor starts
the day after the expiry, which is the default and the case that matters.

The successor starts the day after the predecessor's `end`, because a renewal is
contiguous with the term it renews, and a gap would be a new engagement. A term is a
count of days or of calendar months from its first day: `1 Jan` plus twelve months is
`31 Dec`, the day before the same date a year on, and a month that does not have the day
(`31 Jan` plus one month) steps onto its last day rather than overflowing.
"""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal, Protocol

from pigrocrm.core.db.clock import month_bounds

RenewalUnit = Literal["days", "weeks", "months", "years"]


class RenewalRefused(ValueError):
    """A term that cannot be computed; the message is the sentence the person reads and
    `field` names the request field it is about."""

    def __init__(self, field: str, message: str, expected: str) -> None:
        super().__init__(message)
        self.field = field
        self.expected = expected


def add_months(day: date, months: int) -> date:
    """`day` moved by whole calendar months, clamped to the target month's last day."""
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(day.day, month_bounds(year, month + 1)[1].day))


def _span(start: date, count: int, unit: RenewalUnit) -> date:
    """The last day of a term of `count` `unit`s that begins on `start`."""
    if unit == "days":
        return start + timedelta(days=count - 1)
    if unit == "weeks":
        return start + timedelta(days=count * 7 - 1)
    months = count if unit == "months" else count * 12
    return add_months(start, months) - timedelta(days=1)


def _whole_months(start: date, end: date) -> int | None:
    """How many calendar months `start`..`end` is, when it is exactly that many: it
    begins on the 1st and ends on the last day of a month. `None` otherwise."""
    if start.day != 1 or end != month_bounds(end.year, end.month)[1]:
        return None
    return (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1


def renewed_term(
    *,
    start: date,
    end: date | None,
    new_start: date | None = None,
    new_end: date | None = None,
    duration: tuple[int, RenewalUnit] | None = None,
) -> tuple[date, date]:
    """The `(inizio, fine)` of the contract that renews one running `start`..`end`.

    The start is the day after `end` unless `new_start` names one, which has to be
    later than `end`: a renewal that overlaps the term it renews is a second contract
    for the same days. The end is `new_end`, or `duration` counted from the start, or,
    when neither is given, as long as the term renewed: the same number of calendar months
    when that was whole months and the new start is a 1st, else the same number of days.
    A contract with no end has no expiry to renew from.
    """
    try:
        return _renewed_term(start, end, new_start, new_end, duration)
    except RenewalRefused:
        raise
    except (OverflowError, ValueError) as error:
        raise RenewalRefused(
            "end", "la data cade fuori dall'intervallo ammesso", "una data prima del 9999"
        ) from error


def _renewed_term(
    start: date,
    end: date | None,
    new_start: date | None,
    new_end: date | None,
    duration: tuple[int, RenewalUnit] | None,
) -> tuple[date, date]:
    if end is None:
        raise RenewalRefused(
            "end",
            "un contratto senza scadenza non ha un termine da rinnovare",
            "un contratto con una data di fine",
        )
    renewed_start = new_start if new_start is not None else end + timedelta(days=1)
    if renewed_start <= end:
        raise RenewalRefused(
            "start",
            "il contratto rinnovato deve cominciare dopo la fine di quello che rinnova",
            f"> {end.isoformat()}",
        )
    if new_end is not None:
        if new_end < renewed_start:
            raise RenewalRefused(
                "end", "non può precedere inizio", f">= {renewed_start.isoformat()}"
            )
        return renewed_start, new_end
    if duration is not None:
        count, unit = duration
        return renewed_start, _span(renewed_start, count, unit)
    months = _whole_months(start, end)
    if months is not None and renewed_start.day == 1:
        return renewed_start, _span(renewed_start, months, "months")
    return renewed_start, renewed_start + (end - start)


class _Period(Protocol):
    valid_from: date
    valid_to: date | None


def carried_period(card: _Period, start: date) -> tuple[date, date | None] | None:
    """Where `card` stands on a term that begins on `start`, or `None` when it does not
    reach it. A card that closed before the renewed term began priced a period that is
    over, and copying it would put a dead window on a live contract. One that began
    earlier is carried from the term's own first day: the rate is the same, and the date it
    became applicable to this term is the day this term began. One that begins later keeps
    its own date: a rate change recorded for the future is a fact about the price and
    survives the renewal."""
    if card.valid_to is not None and card.valid_to < start:
        return None
    return max(card.valid_from, start), card.valid_to


def carried_cards[Card: _Period](
    cards: Sequence[Card], start: date
) -> list[tuple[Card, date, date | None]]:
    """Each rate card the renewed term carries, with the validity it carries it on."""
    carried = []
    for card in cards:
        period = carried_period(card, start)
        if period is not None:
            carried.append((card, *period))
    return carried


__all__ = [
    "RenewalRefused",
    "RenewalUnit",
    "add_months",
    "carried_cards",
    "carried_period",
    "renewed_term",
]
