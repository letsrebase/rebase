"""The term of a renewed contract, and which rate cards go across (REB-763).

Pure, with no session: the interesting arithmetic is a month boundary, and a month
boundary is where a model or a hurried query is a day off with nothing downstream able
to tell. Ported from mastro's `contract-renewal-term.ts` (#815, `renewedTerm` and
`rateCardsForRenewedTerm` in `~/projects/personal/mastro`), translated onto this schema's
own columns and with one change: mastro read a duration off a signed renewal message, so
its anchor was the predecessor's expiry; here a person asks for a renewal on the page,
so the term is anchored on its own first day. The two agree whenever the successor starts
the day after the expiry, which is the default and the case that matters.

The successor starts the day after the predecessor's `fine`, because a renewal is
contiguous with the term it renews, and a gap would be a new engagement. A term is a
count of days or of calendar months from its first day: `1 Jan` plus twelve months is
`31 Dec`, the day before the same date a year on, and a month that does not have the day
(`31 Jan` plus one month) steps onto its last day rather than overflowing.
"""

import calendar
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal, Protocol

RenewalUnit = Literal["giorni", "settimane", "mesi", "anni"]


class RenewalRefused(ValueError):
    """A term that cannot be computed; the message is the sentence the person reads and
    `field` names the request field it is about."""

    def __init__(self, field: str, message: str, expected: str) -> None:
        super().__init__(message)
        self.field = field
        self.expected = expected


def add_months(giorno: date, months: int) -> date:
    """`giorno` moved by whole calendar months, clamped to the target month's last day."""
    total = giorno.year * 12 + (giorno.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(giorno.day, calendar.monthrange(year, month + 1)[1]))


def _span(start: date, count: int, unit: RenewalUnit) -> date:
    """The last day of a term of `count` `unit`s that begins on `start`."""
    if unit == "giorni":
        return start + timedelta(days=count - 1)
    if unit == "settimane":
        return start + timedelta(days=count * 7 - 1)
    months = count if unit == "mesi" else count * 12
    return add_months(start, months) - timedelta(days=1)


def _whole_months(inizio: date, fine: date) -> int | None:
    """How many calendar months `inizio`..`fine` is, when it is exactly that many: it
    begins on the 1st and ends on the last day of a month. `None` otherwise."""
    if inizio.day != 1 or fine.day != calendar.monthrange(fine.year, fine.month)[1]:
        return None
    return (fine.year * 12 + fine.month) - (inizio.year * 12 + inizio.month) + 1


def renewed_term(
    *,
    inizio: date,
    fine: date | None,
    nuovo_inizio: date | None = None,
    nuova_fine: date | None = None,
    durata: tuple[int, RenewalUnit] | None = None,
) -> tuple[date, date]:
    """The `(inizio, fine)` of the contract that renews one running `inizio`..`fine`.

    The start is the day after `fine` unless `nuovo_inizio` names one, which has to be
    later than `fine`: a renewal that overlaps the term it renews is a second contract
    for the same days. The end is `nuova_fine`, or `durata` counted from the start, or,
    when neither is given, as long as the term renewed: the same number of calendar months
    when that was whole months and the new start is a 1st, else the same number of days.
    A contract with no end has no expiry to renew from.
    """
    if fine is None:
        raise RenewalRefused(
            "fine",
            "un contratto senza scadenza non ha un termine da rinnovare",
            "un contratto con una data di fine",
        )
    start = nuovo_inizio if nuovo_inizio is not None else fine + timedelta(days=1)
    if start <= fine:
        raise RenewalRefused(
            "inizio",
            "il contratto rinnovato deve cominciare dopo la fine di quello che rinnova",
            f"> {fine.isoformat()}",
        )
    if nuova_fine is not None:
        if nuova_fine < start:
            raise RenewalRefused("fine", "non può precedere inizio", f">= {start.isoformat()}")
        return start, nuova_fine
    if durata is not None:
        count, unit = durata
        return start, _span(start, count, unit)
    months = _whole_months(inizio, fine)
    if months is not None and start.day == 1:
        return start, _span(start, months, "mesi")
    return start, start + (fine - inizio)


class _Period(Protocol):
    valido_da: date
    valido_a: date | None


def carried_period(card: _Period, inizio: date) -> tuple[date, date | None] | None:
    """Where `card` stands on a term that begins on `inizio`, or `None` when it does not
    reach it. A card that closed before the renewed term began priced a period that is
    over, and copying it would put a dead window on a live contract. One that began
    earlier is carried from the term's own first day: the rate is the same, and the date it
    became applicable to this term is the day this term began. One that begins later keeps
    its own date: a rate change recorded for the future is a fact about the price and
    survives the renewal."""
    if card.valido_a is not None and card.valido_a < inizio:
        return None
    return max(card.valido_da, inizio), card.valido_a


def carried_cards[Card: _Period](
    cards: Sequence[Card], inizio: date
) -> list[tuple[Card, date, date | None]]:
    """Each rate card the renewed term carries, with the validity it carries it on."""
    carried = []
    for card in cards:
        period = carried_period(card, inizio)
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
