"""A contract's value as its rate cards state it, in the terms `deals/value.py` prices a
deal with, so a deal and the contract it became can be compared figure by figure (spec
2026-11-03 §8, the invariant).

Pure: no session, so the property test feeds it plain cards.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Protocol

from pigrocrm.core.deals.value import ValueSummary, ValueTerm, summarize


class CardLike(Protocol):
    """What `card_term` reads: a `RateCard` row or its schema."""

    @property
    def type(self) -> str: ...
    @property
    def amount(self) -> Decimal: ...
    @property
    def valid_from(self) -> date: ...
    @property
    def valid_to(self) -> date | None: ...
    @property
    def delivery_period(self) -> str | None: ...
    @property
    def expected_quantity(self) -> Decimal | None: ...


def _earliest(*days: date | None) -> date | None:
    known = [day for day in days if day is not None]
    return min(known) if known else None


def card_term(card: CardLike, contract_end: date | None) -> ValueTerm | None:
    """The card as a priced term, ending on the earlier of its own `valid_to` and the
    contract's end, or `None` when it states no value: a day or hour card with no forecast
    (a hand-made rate prices worked days, it forecasts nothing) and a fee card with no
    period."""
    end = _earliest(card.valid_to, contract_end)
    match card.type:
        case "one_off":
            return ValueTerm("flat", "one_off", card.amount, Decimal(1), card.valid_from, None)
        case "fixed_recurring" if card.delivery_period is not None:
            return ValueTerm(
                "flat",
                card.delivery_period,  # type: ignore[arg-type]
                card.amount,
                Decimal(1),
                card.valid_from,
                end,
            )
        case "daily" | "hourly" if (
            card.delivery_period is not None and card.expected_quantity is not None
        ):
            return ValueTerm(
                "day" if card.type == "daily" else "hour",
                card.delivery_period,  # type: ignore[arg-type]
                card.amount,
                card.expected_quantity,
                card.valid_from,
                end,
            )
    return None


def value_summary(cards: Iterable[CardLike], contract_end: date | None) -> ValueSummary:
    """The total, the one-off and the monthly recurring figure of a contract's cards, over a
    term that ends on `contract_end` (`None` for an open-ended contract, whose recurring cards
    are counted at 12 months, as a deal's open-ended lines are).

    A `fixed_recurring` card is read by its period (quarterly included), a `daily` or
    `hourly` card by its forecast and ignored without one, and a `one_off` card counts
    once."""
    terms = (term for card in cards if (term := card_term(card, contract_end)) is not None)
    return summarize(terms)
