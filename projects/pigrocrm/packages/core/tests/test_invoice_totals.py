"""The arithmetic of spec 6.1, proven where it is observable.

Under the forfettario every tax is zero, so rules 2 and 3 -- "a group's taxable base
is the sum of the already-rounded line totals" and "a group's tax is computed from
the group, not summed from the lines" -- cannot be distinguished from real data. They
are exactly the rules the SdI checks and rejects a file over, so they are pinned here
with rates the shipped product never uses.
"""

from decimal import Decimal

import pytest

from pigrocrm.core.invoices.totals import (
    ComputedLine,
    build_recap,
    format_amount_2,
    format_amount_8,
    format_rate,
    line_total,
    sum_totals,
)
from pigrocrm.core.money import round_money


def _line(
    number: int,
    total_price: str,
    rate: str,
    vat_nature: str | None = None,
    reference: str | None = None,
) -> ComputedLine:
    return ComputedLine(
        line_number=number,
        description=f"riga {number}",
        quantity=Decimal("1.000000"),
        unit_of_measure=None,
        unit_price=Decimal(total_price),
        discount_percentage=None,
        discount_amount=None,
        total_price=Decimal(total_price),
        vat_rate=Decimal(rate),
        vat_nature=vat_nature,
        legal_reference=reference,
    )


def test_a_half_cent_rounds_up_not_to_even() -> None:
    """ROUND_HALF_UP, not Decimal's ROUND_HALF_EVEN default: Italian fiscal practice
    and the SdI's own arithmetic round a half up. Banker's rounding would send
    0.125 to 0.12 and 0.135 to 0.14, i.e. disagree with the SdI on alternate cents."""
    assert round_money(Decimal("0.125")) == Decimal("0.13")
    assert round_money(Decimal("0.135")) == Decimal("0.14")
    assert round_money(Decimal("-0.125")) == Decimal("-0.13")


def test_line_total_is_quantity_times_price_rounded_to_the_cent() -> None:
    assert line_total(
        quantity=Decimal("3.000000"),
        unit_price=Decimal("33.333333"),
        discount_percentage=None,
        discount_amount=None,
    ) == Decimal("100.00")


def test_line_total_applies_a_percentage_discount_then_a_fixed_one() -> None:
    assert line_total(
        quantity=Decimal("2.000000"),
        unit_price=Decimal("100.000000"),
        discount_percentage=Decimal("10.00"),
        discount_amount=Decimal("5.00"),
    ) == Decimal("175.00")


def test_a_negative_line_is_allowed_because_a_discount_is_a_line() -> None:
    assert line_total(
        quantity=Decimal("1.000000"),
        unit_price=Decimal("-50.000000"),
        discount_percentage=None,
        discount_amount=None,
    ) == Decimal("-50.00")


def test_a_group_base_is_the_sum_of_already_rounded_line_totals() -> None:
    """Spec 6.1 rule 2. Three lines of 0.005 each: the exact sum is 0.015, whose
    rounding is 0.02, but each printed line reads 0.01, so the SdI's own check
    (ImponibileImporto == sum of the group's PrezzoTotale) wants 0.03. Rounding the
    exact sum instead is a discarded file."""
    lines = [_line(1, "0.01", "22.00"), _line(2, "0.01", "22.00"), _line(3, "0.01", "22.00")]
    (group,) = build_recap(lines)
    assert group.taxable_amount == Decimal("0.03")


def test_group_tax_is_computed_from_the_group_not_summed_from_the_lines() -> None:
    """Spec 6.1 rule 3, and the point where rules 2 and 3 genuinely disagree.
    Three lines of 3.33 at 22%: per line the tax rounds to 0.73 each, summing to
    2.19, while the group's 9.99 x 22% is 2.1978 -> 2.20. The SdI checks the group
    product, so 2.20 is the only acceptable answer."""
    lines = [_line(1, "3.33", "22.00"), _line(2, "3.33", "22.00"), _line(3, "3.33", "22.00")]
    (group,) = build_recap(lines)
    assert group.taxable_amount == Decimal("9.99")
    assert group.vat_amount == Decimal("2.20")
    assert sum(round_money(Decimal("3.33") * Decimal("22") / 100) for _ in range(3)) == Decimal(
        "2.19"
    )


def test_two_rates_on_one_invoice_produce_one_group_each() -> None:
    """Spec 14.8, the synthetic RF01 case: 22% and 10% on the same invoice."""
    lines = [
        _line(1, "100.00", "22.00"),
        _line(2, "50.00", "10.00"),
        _line(3, "25.00", "22.00"),
    ]
    groups = build_recap(lines)
    assert [(g.vat_rate, g.taxable_amount, g.vat_amount) for g in groups] == [
        (Decimal("10.00"), Decimal("50.00"), Decimal("5.00")),
        (Decimal("22.00"), Decimal("125.00"), Decimal("27.50")),
    ]
    taxable_amount, vat_amount, total = sum_totals(groups)
    assert (taxable_amount, vat_amount, total) == (
        Decimal("175.00"),
        Decimal("32.50"),
        Decimal("207.50"),
    )


def test_same_rate_different_vat_nature_are_distinct_groups() -> None:
    """Spec 6.1 rule 5: the group key is (aliquota, natura), because the two carry
    different RiferimentoNormativo values and the SdI reads them per group."""
    lines = [
        _line(1, "100.00", "0.00", vat_nature="N2.2", reference="art. 1 L. 190/2014"),
        _line(2, "40.00", "0.00", vat_nature="N1", reference="art. 15 DPR 633/72"),
    ]
    groups = build_recap(lines)
    assert [(g.vat_nature, g.taxable_amount) for g in groups] == [
        ("N1", Decimal("40.00")),
        ("N2.2", Decimal("100.00")),
    ]


def test_the_flat_rate_shape_is_all_zeroes_and_still_sums() -> None:
    lines = [
        _line(1, "1500.00", "0.00", vat_nature="N2.2"),
        _line(2, "-100.00", "0.00", vat_nature="N2.2"),
    ]
    groups = build_recap(lines)
    taxable_amount, vat_amount, total = sum_totals(groups)
    assert (taxable_amount, vat_amount, total) == (
        Decimal("1400.00"),
        Decimal("0.00"),
        Decimal("1400.00"),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", "0.00"), ("1400", "1400.00"), ("1400.5", "1400.50"), ("-100.005", "-100.01")],
)
def test_amount_2_always_carries_exactly_two_decimals(value: str, expected: str) -> None:
    """FPR12's Amount2DecimalType is `[\\-]?[0-9]{1,11}\\.[0-9]{2}`: a bare "1400" is
    schema-invalid, so formatting is not cosmetic here."""
    assert format_amount_2(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("3", "3.000000"), ("33.333333", "33.333333"), ("-1.5", "-1.500000")],
)
def test_amount_8_carries_six_decimals_inside_the_schema_range(value: str, expected: str) -> None:
    """Amount8DecimalType allows 2 to 8 decimals; the columns are Numeric(12, 6), so
    six is both exact for the stored value and inside the schema's range."""
    assert format_amount_8(Decimal(value)) == expected


@pytest.mark.parametrize(("value", "expected"), [("0", "0.00"), ("22", "22.00"), ("4.5", "4.50")])
def test_rate_always_carries_exactly_two_decimals(value: str, expected: str) -> None:
    assert format_rate(Decimal(value)) == expected
