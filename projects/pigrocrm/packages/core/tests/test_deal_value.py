"""The arithmetic of a deal's value lines (`pigrocrm.core.deals.value`): how many times a
line falls due, what it adds up to, and which part of it a period holds."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pigrocrm.core.deals.value import (
    ValueTerm,
    line_term,
    occurrences,
    share_in,
    summarize,
    term_total,
)


def term(
    delivery_period: str = "monthly",
    price: str = "100",
    quantity: str = "1",
    start: date = date(2026, 10, 1),
    end: date | None = None,
    unit: str = "flat",
) -> ValueTerm:
    return ValueTerm(
        unit=unit,  # type: ignore[arg-type]
        delivery_period=delivery_period,  # type: ignore[arg-type]
        unit_price=Decimal(price),
        quantity=Decimal(quantity),
        start=start,
        end=end,
    )


@dataclass
class Line:
    unit: str = "flat"
    delivery_period: str = "monthly"
    unit_price: Decimal = Decimal("500")
    quantity: Decimal = Decimal(1)
    start: date | None = None
    duration_months: int | None = None


def test_a_line_runs_for_its_duration_counted_in_occurrences() -> None:
    monthly_year = line_term(Line(duration_months=12), date(2026, 10, 1))
    annual_three_years = line_term(
        Line(delivery_period="annual", duration_months=36), date(2026, 10, 1)
    )
    annual_year_and_a_half = line_term(
        Line(delivery_period="annual", duration_months=18), date(2026, 10, 1)
    )
    assert len(occurrences(monthly_year)) == 12
    assert len(occurrences(annual_three_years)) == 3
    assert len(occurrences(annual_year_and_a_half)) == 2


def test_an_open_ended_fee_counts_twelve_months() -> None:
    open_ended = line_term(Line(), date(2026, 10, 1))
    assert open_ended.end is None
    assert len(occurrences(open_ended)) == 12
    assert term_total(open_ended) == Decimal("6000.00")


def test_a_line_without_a_start_follows_the_fallback_and_its_own_start_wins() -> None:
    assert line_term(Line(), date(2026, 3, 1)).start == date(2026, 3, 1)
    assert line_term(Line(start=date(2026, 5, 2)), date(2026, 3, 1)).start == date(2026, 5, 2)


def test_a_one_off_line_falls_once_on_its_start_and_ignores_its_end() -> None:
    lump = term("one_off", "30000", start=date(2026, 10, 15), end=date(2027, 10, 14))
    assert occurrences(lump) == [date(2026, 10, 15)]
    assert term_total(lump) == Decimal("30000.00")


def test_occurrences_are_counted_from_the_start_and_clamp_to_the_month_end() -> None:
    fee = term("monthly", start=date(2026, 1, 31), end=date(2026, 4, 30))
    # Stepping from the clamped 28 February would put March on the 28th.
    assert occurrences(fee) == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]


def test_a_term_ending_before_it_starts_never_falls_due() -> None:
    assert occurrences(term("monthly", start=date(2026, 10, 1), end=date(2026, 9, 30))) == []
    assert term_total(term("monthly", start=date(2026, 10, 1), end=date(2026, 9, 30))) == 0


def test_the_figures_of_a_mix_of_lines() -> None:
    summary = summarize(
        [
            term("one_off", "20000"),
            term("monthly", "500"),
            term("annual", "6000", end=date(2029, 9, 30)),
            term("monthly", "600", "4", unit="day", end=date(2027, 9, 30)),
        ]
    )
    # 20000 + 500 x 12 + 6000 x 3 + 600 x 4 x 12
    assert summary.total == Decimal("72800.00")
    assert summary.one_off == Decimal("20000.00")
    # 500 + 6000 / 12 + 600 x 4
    assert summary.monthly_recurring == Decimal("3400.00")


def test_no_lines_sum_to_zero() -> None:
    summary = summarize([])
    assert (summary.total, summary.one_off, summary.monthly_recurring) == (0, 0, 0)


def test_a_term_is_rounded_once_on_its_total_and_its_monthly_figure_on_its_own() -> None:
    # 33.33 x 1.5 x 12 = 599.94, where rounding each occurrence (49.995 -> 50.00) gives 600.00.
    fee = term("monthly", "33.33", "1.5", unit="day", end=date(2027, 9, 30))
    assert term_total(fee) == Decimal("599.94")
    # 100.01 a year over 12 months is 8.334166..., rounded half up at two places.
    assert summarize([term("annual", "100.01")]).monthly_recurring == Decimal("8.33")
    assert summarize([term("quarterly", "100.00")]).monthly_recurring == Decimal("33.33")


def test_a_monthly_fee_starting_in_october_has_three_months_in_that_year() -> None:
    fee = term("monthly", "500", start=date(2026, 10, 1))
    assert share_in([fee], date(2026, 1, 1), date(2026, 12, 31)) == Decimal("1500.00")
    assert share_in([fee], date(2027, 1, 1), date(2027, 12, 31)) == Decimal("4500.00")


def test_the_shares_of_every_year_add_up_to_the_total() -> None:
    fee = term(
        "monthly", "33.33", "1.5", unit="day", start=date(2026, 10, 1), end=date(2027, 9, 30)
    )
    shares = share_in([fee], date(2026, 1, 1), date(2026, 12, 31)) + share_in(
        [fee], date(2027, 1, 1), date(2027, 12, 31)
    )
    assert shares == term_total(fee)


def test_a_one_off_day_block_is_spread_over_the_months_of_its_window() -> None:
    # 5 days at 800 worked across December and January.
    block = term("one_off", "800", "5", unit="day", start=date(2026, 12, 1), end=date(2027, 1, 31))
    assert term_total(block) == Decimal("4000.00")
    assert share_in([block], date(2026, 1, 1), date(2026, 12, 31)) == Decimal("2000.00")
    assert share_in([block], date(2027, 1, 1), date(2027, 12, 31)) == Decimal("2000.00")


def test_a_spread_one_off_keeps_its_total_exact_with_the_remainder_on_the_last_month() -> None:
    block = term("one_off", "100", "1", unit="day", start=date(2026, 1, 1), end=date(2026, 3, 31))
    months = [share_in([block], date(2026, month, 1), date(2026, month, 28)) for month in (1, 2, 3)]
    assert months == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]


def test_a_flat_one_off_is_not_spread_and_a_one_off_day_without_a_window_falls_on_its_start() -> (
    None
):
    lump = term("one_off", "3000", start=date(2026, 12, 1), end=date(2027, 6, 30))
    assert share_in([lump], date(2027, 1, 1), date(2027, 12, 31)) == 0
    assert share_in([lump], date(2026, 12, 1), date(2026, 12, 31)) == Decimal("3000.00")
    open_block = term("one_off", "800", "5", unit="day", start=date(2026, 12, 1))
    assert share_in([open_block], date(2026, 12, 1), date(2026, 12, 31)) == Decimal("4000.00")


def test_a_period_bound_is_inclusive_on_both_sides() -> None:
    fee = term("monthly", "10", start=date(2026, 1, 15), end=date(2026, 3, 14))
    assert share_in([fee], date(2026, 2, 15), date(2026, 2, 15)) == Decimal("10.00")
    assert share_in([fee], date(2026, 1, 16), date(2026, 2, 14)) == 0
