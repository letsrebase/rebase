"""`italian_amount`: the web app's `machineAmount` rule, in Python (REB-485)."""

from decimal import Decimal

import pytest

from rebase_core.amounts import NotAnAmount, italian_amount


@pytest.mark.parametrize(
    ("typed", "amount"),
    [
        ("12.000", Decimal("12000")),
        ("1.500", Decimal("1500")),
        ("1.234,50", Decimal("1234.50")),
        ("480", Decimal("480")),
        ("480,50", Decimal("480.50")),
        ("480.50", Decimal("480.50")),
        ("480.5", Decimal("480.5")),
        ("0,5", Decimal("0.5")),
        (" 12.000 ", Decimal("12000")),
        # What the API answers with, sent back unchanged by a form that showed it.
        ("1500.00", Decimal("1500.00")),
    ],
)
def test_an_amount_is_read_the_italian_way(typed: str, amount: Decimal) -> None:
    assert italian_amount(typed) == amount


def test_the_decimal_keeps_its_cents_exactly() -> None:
    assert str(italian_amount("1.234,50")) == "1234.50"


@pytest.mark.parametrize(
    "typed",
    ["", "   ", "tanto", "12abc", "1,2,3", "1.2.3,4,5", "NaN", "Infinity", "1e3", "1_000", "١٢"],
)
def test_what_is_not_an_amount_is_refused(typed: str) -> None:
    with pytest.raises(NotAnAmount):
        italian_amount(typed)


def test_the_refusal_is_a_value_error_a_caller_can_catch_as_one() -> None:
    with pytest.raises(ValueError):
        italian_amount("tanto")
