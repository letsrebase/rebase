"""The arithmetic of a deal's value: what its lines add up to, and when the money falls.

Pure: no session, no clock, no import from `contracts`. A deal's lines and (later) a
contract's rate cards are both turned into `ValueTerm`s and fed to the same functions, so a
deal and the contract it becomes cannot read two different prices off the same lines.

Every term is rounded with `round_money` on its own and the rounded terms are summed, as
the weighted value of the pipeline already does.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from pigrocrm.core.db.clock import add_months, stepped_dates
from pigrocrm.core.money import ZERO_MONEY, round_money

Unit = Literal["flat", "day", "hour"]
DeliveryPeriod = Literal["one_off", "monthly", "quarterly", "annual"]

# An open-ended recurring term is counted at this many months.
OPEN_ENDED_MONTHS = 12

_STEP_MONTHS: dict[str, int] = {"monthly": 1, "quarterly": 3, "annual": 12}


@dataclass(frozen=True, slots=True)
class ValueTerm:
    """One priced term. `end` is the inclusive last day, `None` when open-ended; a
    `one_off` flat term ignores it."""

    unit: Unit
    delivery_period: DeliveryPeriod
    unit_price: Decimal
    quantity: Decimal
    start: date
    end: date | None


@dataclass(frozen=True, slots=True)
class ValueSummary:
    total: Decimal
    one_off: Decimal
    monthly_recurring: Decimal


class ValueLineLike(Protocol):
    """What `line_term` reads: a `DealValueLine` row or its schema (read-only, so a schema's
    narrower `Literal` types satisfy it)."""

    @property
    def unit(self) -> str: ...
    @property
    def delivery_period(self) -> str: ...
    @property
    def unit_price(self) -> Decimal: ...
    @property
    def quantity(self) -> Decimal: ...
    @property
    def start(self) -> date | None: ...
    @property
    def duration_months(self) -> int | None: ...


def line_term(line: ValueLineLike, fallback_start: date) -> ValueTerm:
    """A deal line as a term: it starts on its own `start`, else on `fallback_start` (the
    deal's expected close date), and runs `duration_months` when it has them."""
    start = line.start or fallback_start
    end = (
        add_months(start, line.duration_months) - timedelta(days=1)
        if line.duration_months
        else None
    )
    return ValueTerm(
        unit=line.unit,  # type: ignore[arg-type]
        delivery_period=line.delivery_period,  # type: ignore[arg-type]
        unit_price=line.unit_price,
        quantity=line.quantity,
        start=start,
        end=end,
    )


def _last_day(term: ValueTerm) -> date:
    return (
        term.end
        if term.end is not None
        else add_months(term.start, OPEN_ENDED_MONTHS) - (timedelta(days=1))
    )


def occurrences(term: ValueTerm) -> list[date]:
    """The dates the term falls due. A one-off falls once, on its start; a recurring term
    on its start and every step after it up to its last day (an open-ended one is counted
    at 12 months)."""
    if term.delivery_period == "one_off":
        return [term.start]
    return stepped_dates(term.start, _last_day(term), _STEP_MONTHS[term.delivery_period])


def terms_overlap(a: ValueTerm, b: ValueTerm) -> bool:
    """Whether two terms are in force together: each from its start to its end, or with no
    end when it has none. What decides that a deal, or a contract, has at most one day or hour
    rate in force on a date."""
    return (a.end is None or b.start <= a.end) and (b.end is None or a.start <= b.end)


def term_total(term: ValueTerm) -> Decimal:
    return round_money(term.unit_price * term.quantity * len(occurrences(term)))


def _monthly_figure(term: ValueTerm) -> Decimal:
    amount = term.unit_price * term.quantity
    match term.delivery_period:
        case "monthly":
            return round_money(amount)
        case "annual":
            return round_money(amount / 12)
        case "quarterly":
            return round_money(amount / 3)
    return ZERO_MONEY


def summarize(terms: Iterable[ValueTerm]) -> ValueSummary:
    total = one_off = monthly = ZERO_MONEY
    for term in terms:
        line_total = term_total(term)
        total += line_total
        if term.delivery_period == "one_off":
            one_off += line_total
        monthly += _monthly_figure(term)
    return ValueSummary(total=total, one_off=one_off, monthly_recurring=monthly)


def _share_dates(term: ValueTerm) -> list[tuple[date, Decimal]]:
    """The dated amounts a term contributes to a period. A one-off day or hour term with
    a window is worked across it, so its total is spread evenly over one slot per month of
    the window; every other term falls on its occurrences. The per-slot amount is rounded
    and the remainder lands on the last slot, so the slots add up to `term_total` exactly."""
    if term.unit != "flat" and term.delivery_period == "one_off" and term.end is not None:
        days = stepped_dates(term.start, term.end, 1)
        each = round_money(term_total(term) / len(days))
    else:
        days = occurrences(term)
        each = round_money(term.unit_price * term.quantity)
    if not days:
        return []
    amounts = [each] * (len(days) - 1)
    amounts.append(term_total(term) - sum(amounts, ZERO_MONEY))
    return list(zip(days, amounts, strict=True))


def share_in(terms: Sequence[ValueTerm], date_from: date, date_to: date) -> Decimal:
    """The part of the terms' value dated within `date_from..date_to`, both inclusive."""
    share = ZERO_MONEY
    for term in terms:
        for day, amount in _share_dates(term):
            if date_from <= day <= date_to:
                share += amount
    return share
