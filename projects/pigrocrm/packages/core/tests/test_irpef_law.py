"""The IRPEF brackets and the regional surcharges of tax year 2026 (REB-1062).

The figures are the sources' (`fiscal/irpef_2026.py` cites each); the worked examples below
apply them by hand. The 13,700 euro at 50,000 is the Agenzia delle Entrate's own, on its
«Aliquote e calcolo dell'Irpef» page (13 January 2026).
"""

from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.irpef_2026 import IRPEF_2026, REGIONAL_SURCHARGES_2026
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    NON_IT_PACK,
)

D = Decimal


def law():
    irpef = IT_STANDARD_PACK.law_for(2026)
    assert irpef is not None and irpef.irpef is not None
    return irpef.irpef


@pytest.mark.parametrize(
    ("income", "tax"),
    [
        ("0", "0.00"),
        ("-500", "0.00"),
        ("10000", "2300.00"),
        ("28000", "6440.00"),
        # Agenzia: «l'imposta dovuta è pari a 13.700 euro (non più 14.140) + il 43% sul
        # reddito eccedente i 50.000 euro».
        ("50000", "13700.00"),
        ("60000", "18000.00"),
        ("200000", "78200.00"),
        ("28000.01", "6440.00"),
        ("33333.33", "8200.00"),
    ],
)
def test_gross_irpef_2026(income: str, tax: str) -> None:
    assert law().gross_tax(D(income)) == D(tax)


def test_the_pack_answers_for_2026_only_and_only_the_ordinary_person() -> None:
    assert IT_STANDARD_PACK.law_for(2026) is not None
    assert law() is IRPEF_2026
    assert IT_STANDARD_PACK.law_for(2025) is None
    for other in (IT_FLAT_RATE_PACK, IT_COMPANY_PACK, NON_IT_PACK):
        governing = other.law_for(2026)
        assert governing is None or governing.irpef is None


def test_every_region_and_autonomous_province_has_one_rule() -> None:
    keys = [rule.key for rule in REGIONAL_SURCHARGES_2026]
    istat = {f"{n:02d}" for n in range(1, 21)} - {"04"}
    assert set(keys) == istat | {"TN", "BZ"}
    assert len(keys) == len(set(keys)) == 21
    for rule in REGIONAL_SURCHARGES_2026:
        assert rule.brackets[-1].up_to is None
        assert rule.source
        ups = [b.up_to for b in rule.brackets[:-1]]
        assert ups == sorted(u for u in ups if u is not None)


def test_an_unknown_region_gives_no_figure() -> None:
    assert law().regional_surcharge("04", D(40000)) is None
    assert law().regional_surcharge("", D(40000)) is None
    assert law().regional_surcharge("XX", D(40000)) is None


@pytest.mark.parametrize(
    ("region", "income", "amount"),
    [
        # by slice: 15000 x 1.23% + 13000 x 1.58% + 22000 x 1.72% + 10000 x 1.73%
        ("03", "60000", "941.30"),
        # single rate
        ("17", "40000", "492.00"),
        ("18", "40000", "692.00"),
        # Molise (the 19 June 2026 table): 15000 x 2.03% + 5000 x 2.23%
        ("14", "20000", "416.00"),
        ("01", "0", "0.00"),
        # Friuli Venezia Giulia: 0.70% up to 15,000, 1.23% on the whole income above
        ("06", "15000", "105.00"),
        ("06", "15001", "184.51"),
        ("06", "60000", "738.00"),
        # Valle d'Aosta: exempt up to 15,000, the ordinary rate on all of it above
        ("02", "15000", "0.00"),
        ("02", "15001", "184.51"),
        # Lazio: 1.73% on the whole income up to 28,000; above, by slice; 60 euro off
        # between 28,001 and 30,000
        ("12", "28000", "484.40"),
        ("12", "29000", "665.70"),
        ("12", "31000", "792.30"),
        # Umbria: the base 1.23% up to 28,000; above, by slice, 150 euro off up to 50,000
        ("10", "28000", "344.40"),
        ("10", "40000", "876.50"),
        ("10", "60000", "1671.50"),
        # Trento: the 30,000 euro deduction up to 30,000 euro of income, lost above it
        ("TN", "30000", "0.00"),
        ("TN", "30001", "369.01"),
        ("TN", "60000", "788.00"),
        # Bolzano: 430.50 euro off up to 90,000, never a credit
        ("BZ", "30000", "0.00"),
        ("BZ", "40000", "61.50"),
        ("BZ", "90000", "876.50"),
        ("BZ", "90001", "1307.02"),
    ],
)
def test_regional_surcharge_2026(region: str, income: str, amount: str) -> None:
    assert law().regional_surcharge(region, D(income)) == D(amount)


def test_a_surcharge_is_never_negative_and_never_falls_with_income() -> None:
    for rule in REGIONAL_SURCHARGES_2026:
        previous = D(0)
        for euro in range(0, 400_001, 250):
            amount = rule.surcharge(D(euro))
            assert amount >= 0
            assert amount >= previous, (rule.key, euro)
            previous = amount
