"""How a won deal's value lines become a contract's rate cards (spec 2026-11-03 §8).

Pure functions over lines and dates, so the service stays a sequence of checks and writes and
the invariant (the contract's figures equal the deal's) is tested on these alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from pigrocrm.core.contracts.schemas import ProposedRateCard
from pigrocrm.core.deals.value import ValueLineLike, ValueTerm, line_term

_CARD_TYPE = {"day": "daily", "hour": "hourly"}


def terms_of(lines: Sequence[ValueLineLike], contract_start: date) -> list[ValueTerm]:
    """Each line as a term. A line with no start of its own takes the contract's, and the
    dates are fixed from here on: the card keeps them whatever the deal does afterwards.
    Raises `ValueError` or `OverflowError` for a date beyond the calendar."""
    return [line_term(line, contract_start) for line in lines]


def proposed_start(lines: Sequence[ValueLineLike], today: date) -> date:
    """The earliest start a line states, else `today`."""
    starts = [line.start for line in lines if line.start is not None]
    return min(starts) if starts else today


def proposed_end(terms: Sequence[ValueTerm]) -> date | None:
    """`None` when any term has no end (an open-ended recurring line, or a day or hour line
    with no duration), else the latest last day among them. A flat one-off line has no end of
    its own and never sets one, so a deal of lump sums alone proposes none and the person
    enters the delivery date."""
    ends: list[date] = []
    for term in terms:
        if term.unit == "flat" and term.delivery_period == "one_off":
            continue
        if term.end is None:
            return None
        ends.append(term.end)
    return max(ends, default=None)


def card_from_term(term: ValueTerm) -> ProposedRateCard:
    """The card one line becomes, valid from the term's start and until its last day when it
    has one. A flat one-off card is closed on its own day, so a renewal does not carry an
    upfront fee into the next term."""
    if term.unit == "flat":
        if term.delivery_period == "one_off":
            return ProposedRateCard(
                valid_from=term.start,
                valid_to=term.start,
                type="one_off",
                amount=term.unit_price,
                unit="flat",
            )
        annual = term.delivery_period == "annual"
        return ProposedRateCard(
            valid_from=term.start,
            valid_to=term.end,
            type="fixed_recurring",
            amount=term.unit_price,
            unit="year" if annual else "month",
            delivery_period="annual" if annual else "monthly",
        )
    return ProposedRateCard(
        valid_from=term.start,
        valid_to=term.end,
        type=_CARD_TYPE[term.unit],  # type: ignore[arg-type]
        amount=term.unit_price,
        unit=term.unit,
        delivery_period=term.delivery_period,
        expected_quantity=term.quantity,
    )
