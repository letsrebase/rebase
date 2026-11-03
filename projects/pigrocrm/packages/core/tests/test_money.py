"""The two rounding rules of §6.2, pinned. They disagree by cents, and the
disagreement is decided here rather than discovered in front of a client: the row
value is what the timesheet prints next to each entry, so an aggregate is the sum of
the printed rows, never the rounding of the exact sum."""

from decimal import Decimal

import pytest

from pigrocrm.core.money import (
    line_value,
    percent_to_share,
    percentage_of,
    round_hours,
    round_money,
    share,
    sum_hours,
    sum_money,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.005", "0.01"),  # half-up, not half-even: banker's rounding gives 0.00
        ("0.015", "0.02"),  # half-even gives 0.02 here too -- kept for contrast
        ("0.025", "0.03"),  # half-even gives 0.02: this is the case that separates them
        ("-0.025", "-0.03"),  # away from zero on the half, symmetrically
        ("2.344", "2.34"),
        ("2.345", "2.35"),
    ],
)
def test_round_money_is_half_up(raw: str, expected: str) -> None:
    assert round_money(Decimal(raw)) == Decimal(expected)


def test_line_value_is_a_rounded_product() -> None:
    assert line_value(Decimal("3.00"), Decimal("33.333333")) == Decimal("100.00")
    assert line_value(Decimal("0.10"), Decimal("33.333333")) == Decimal("3.33")


def test_line_value_without_a_factor_is_none_not_zero() -> None:
    """A silent fall back to 0.00 would say "this work was free", which is a lie that
    sums. `None` is excluded from the aggregate and counted separately as "ore senza
    tariffa" (§5.1)."""
    assert line_value(Decimal("8.00"), None) is None


def test_an_aggregate_is_the_sum_of_rounded_rows_and_diverges_from_the_exact_sum() -> None:
    """1000 rows of 0.10 h at 33.333333 EUR/h. `Σ ROUND(...)` is 3330.00;
    `ROUND(Σ exact)` is 3333.33. The rule is the first, and this test exhibits the
    difference rather than asserting the rule in prose (criterion 4)."""
    hours, rate = Decimal("0.10"), Decimal("33.333333")
    rows = [line_value(hours, rate) for _ in range(1000)]
    assert sum_money(rows) == Decimal("3330.00")
    assert round_money(hours * rate * 1000) == Decimal("3333.33")
    assert sum_money(rows) != round_money(hours * rate * 1000)


def test_the_same_thousand_rows_summed_as_floats_diverge_from_the_decimal_total() -> None:
    """The float control the spec's criterion 4 asks for by name: the test must
    compute the naive sum too, and show it differs."""
    naive = sum(0.10 * 33.333333 for _ in range(1000))
    assert Decimal(repr(round(naive, 2))) != sum_money(
        [line_value(Decimal("0.10"), Decimal("33.333333")) for _ in range(1000)]
    )


def test_sums_skip_none_and_return_a_typed_zero_when_empty() -> None:
    assert sum_money([]) == Decimal("0.00")
    assert sum_money([None, Decimal("1.00"), None]) == Decimal("1.00")
    assert sum_hours([Decimal("1.50"), Decimal("2.25")]) == Decimal("3.75")
    assert sum_hours([]) == Decimal("0.00")


def test_round_hours_keeps_two_places() -> None:
    assert round_hours(Decimal("1.005")) == Decimal("1.01")


def test_percentage_of_zero_is_none_not_zero() -> None:
    """`0.00` per cent means "everything I earned went out in costs"; here nothing has
    been earned at all. Two different facts, and the report does not flatten them
    (criterion 6)."""
    assert percentage_of(Decimal("-500.00"), Decimal("0.00")) is None
    assert percentage_of(Decimal("250.00"), Decimal("1000.00")) == Decimal("25.00")
    assert percentage_of(Decimal("1.00"), Decimal("3.00")) == Decimal("33.33")


def test_share_is_zero_when_there_is_nothing_to_scale_against() -> None:
    assert share(Decimal("25"), Decimal("100")) == 0.25
    assert share(Decimal("25"), Decimal("0")) == 0.0
    assert share(Decimal("25"), Decimal("-1")) == 0.0


def test_percent_to_share_reads_a_stored_percentage_as_a_ratio() -> None:
    assert percent_to_share(Decimal("50.00")) == 0.5
