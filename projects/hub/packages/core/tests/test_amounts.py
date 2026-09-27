"""`italian_amount`: the web app's amount rule, in Python (REB-485).

Both sides run the same table, `apps/web/src/lib/amount_cases.json`: it lives in the web
package because the web image builds and type-checks only that package, and
`amount.test.ts` beside it reads it too, so the two readings cannot drift. A row's
`amount` is `null` for a refusal."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from rebase_core.amounts import NotAnAmount, italian_amount

# `.../projects/hub/packages/core/tests/test_amounts.py`: three levels up is `projects/hub`.
TABLE = Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "lib" / "amount_cases.json"
CASES = json.loads(
    TABLE.read_text(encoding="utf-8"),
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


CENT = Decimal("0.01")


@pytest.mark.parametrize(
    "amount",
    [
        row["amount"]
        for row in CASES
        if row["amount"] is not None
        and Decimal(row["amount"]) == Decimal(row["amount"]).quantize(CENT)
    ],
    ids=str,
)
def test_what_the_web_sends_is_read_back_as_the_same_amount(amount: Decimal | int) -> None:
    """The web sends every accepted amount as two decimals and a dot, no grouping
    (`sentAmount`, «1,5» as "1.50"): the one form this reader cannot take for another
    number, where «1.500» sent as such would come back as 1500."""
    assert italian_amount(f"{amount:.2f}") == amount


def test_the_decimal_keeps_its_cents_exactly() -> None:
    assert str(italian_amount("1.234,50")) == "1234.50"


def test_the_refusal_is_a_value_error_a_caller_can_catch_as_one() -> None:
    with pytest.raises(ValueError):
        italian_amount("tanto")
