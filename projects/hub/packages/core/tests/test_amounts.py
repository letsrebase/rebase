"""`italian_amount`: the web app's amount rule, in Python (REB-485).

Both sides run the same table, `amount_cases.json` beside this file: the web's
`apps/web/src/lib/amount.test.ts` reads it too, so the two readings cannot drift. A row's
`amount` is `null` for a refusal."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from rebase_core.amounts import NotAnAmount, italian_amount

CASES = json.loads(
    (Path(__file__).parent / "amount_cases.json").read_text(encoding="utf-8"),
    parse_float=Decimal,
)


@pytest.mark.parametrize(
    ("typed", "amount"), [(row["typed"], row["amount"]) for row in CASES], ids=repr
)
def test_every_row_of_the_shared_table(typed: str, amount: Decimal | int | None) -> None:
    if amount is None:
        with pytest.raises(NotAnAmount):
            italian_amount(typed)
    else:
        assert italian_amount(typed) == amount


def test_the_decimal_keeps_its_cents_exactly() -> None:
    assert str(italian_amount("1.234,50")) == "1234.50"


def test_the_refusal_is_a_value_error_a_caller_can_catch_as_one() -> None:
    with pytest.raises(ValueError):
        italian_amount("tanto")
