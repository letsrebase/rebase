"""The invariant of the conversion (spec 2026-11-03 §8): the value of a deal's lines and the value
of the rate cards they become are the same figures, over random mixes of lines with two-place
prices and quantities. Pure: no database, the cards are the schemas the service writes."""

import random
from datetime import date, timedelta
from decimal import Decimal

from pigrocrm.core.contracts.from_deal import (
    card_from_term,
    proposed_end,
    proposed_start,
    terms_of,
)
from pigrocrm.core.contracts.schemas import RateCardCreate
from pigrocrm.core.contracts.value import value_summary
from pigrocrm.core.deals.models import DealValueLine
from pigrocrm.core.deals.value import line_term, summarize

MIXES = 600


def _money(rng: random.Random, high: int) -> Decimal:
    return Decimal(rng.randrange(1, high * 100)) / 100


def _date(rng: random.Random) -> date:
    # Spread over leap and ordinary years and every day of the month, 29 to 31 included:
    # month-end clamping is where two ways of counting occurrences would part.
    return date(2024, 1, 1) + timedelta(days=rng.randrange(0, 365 * 4))


def _line(rng: random.Random) -> DealValueLine:
    unit = rng.choice(["flat", "flat", "day", "hour"])
    period = rng.choice(["one_off", "monthly", "annual"])
    quantity = Decimal(1) if unit == "flat" else _money(rng, 30)
    months = rng.choice([None, None, 1, 2, 3, 6, 12, 18, 24, 36])
    if unit == "flat" and period == "one_off":
        months = None
    return DealValueLine(
        unit=unit,
        delivery_period=period,
        unit_price=_money(rng, 5000),
        quantity=quantity,
        start=rng.choice([None, _date(rng)]),
        duration_months=months,
    )


def test_the_deals_figures_and_the_contracts_are_the_same_over_random_mixes() -> None:
    rng = random.Random(20261103)
    for mix in range(MIXES):
        lines = [_line(rng) for _ in range(rng.randrange(1, 6))]
        close_date = _date(rng)
        start = proposed_start(lines, _date(rng))
        terms = terms_of(lines, start)
        end = proposed_end(terms)
        cards = [card_from_term(term) for term in terms]

        on_the_deal = summarize(line_term(line, close_date) for line in lines)
        on_the_contract = value_summary(cards, end)

        assert on_the_contract == on_the_deal, (mix, lines, start, end)


def test_a_shorter_term_changes_the_contract_value_on_purpose() -> None:
    line = DealValueLine(
        unit="flat",
        delivery_period="monthly",
        unit_price=Decimal("500"),
        quantity=Decimal(1),
        start=date(2026, 1, 1),
        duration_months=12,
    )
    cards = [card_from_term(term) for term in terms_of([line], date(2026, 1, 1))]
    assert value_summary(cards, date(2026, 12, 31)).total == Decimal("6000.00")
    assert value_summary(cards, date(2026, 6, 30)).total == Decimal("3000.00")


def test_a_day_card_with_no_forecast_and_a_quarterly_card_are_read_for_what_they_state() -> None:
    hand_made_day = RateCardCreate(
        valid_from=date(2026, 1, 1), type="daily", amount=Decimal("600"), unit="day"
    )
    quarterly = RateCardCreate(
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        type="fixed_recurring",
        amount=Decimal("900"),
        unit="month",
        delivery_period="quarterly",
    )
    summary = value_summary([hand_made_day, quarterly], date(2026, 12, 31))
    assert summary.total == Decimal("3600.00")
    assert summary.monthly_recurring == Decimal("300.00")
