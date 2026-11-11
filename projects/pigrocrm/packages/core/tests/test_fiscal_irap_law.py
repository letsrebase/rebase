"""REB-1065: the IRAP base, its deductions, the territorial rule and the 2026 regional rates of
an srl, as data in the `it-company` pack (taxes design § 1). Pure lookups: no database.

Every figure under test is a row of the frame's A5 register, verified on 2026-11-11 against
D.Lgs. 446/1997 art. 4, 5 and 11 (Normattiva) and the Dipartimento delle Finanze's 2026 table
of the regional IRAP rates.
"""

from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.law_irap import IRAP_LAW, IrapLaw
from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK, IT_STANDARD_PACK


def _law() -> IrapLaw:
    company = IT_COMPANY_PACK.law_for(2026)
    assert company is not None and company.company_tax is not None
    assert company.company_tax.irap is not None
    return company.company_tax.irap


def test_the_company_pack_carries_the_irap_law_and_the_ordinary_pack_does_not() -> None:
    assert _law() is IRAP_LAW
    standard = IT_STANDARD_PACK.law_for(2026)
    assert standard is not None and standard.company_tax is None


# --- the base: art. 5 c. 1 ---------------------------------------------------------------


def test_the_base_leaves_out_exactly_the_items_art_5_names() -> None:
    """«con esclusione delle voci di cui ai numeri 9), 10), lettere c) e d), 12) e 13)»."""
    excluded = {i.code for i in IRAP_LAW.base_items if i.treatment == "excluded"}
    assert excluded == {"B9", "B10c", "B10d", "B12", "B13"}


def test_the_base_adds_section_a_and_deducts_the_rest_of_section_b() -> None:
    positive = {i.code for i in IRAP_LAW.base_items if i.treatment == "positive"}
    negative = {i.code for i in IRAP_LAW.base_items if i.treatment == "negative"}
    assert positive == {"A1", "A2", "A3", "A4", "A5"}
    assert negative == {"B6", "B7", "B8", "B10a", "B10b", "B11", "B14"}


def test_every_item_of_section_a_and_b_of_art_2425_is_accounted_for_once() -> None:
    codes = [i.code for i in IRAP_LAW.base_items]
    assert len(codes) == len(set(codes))
    expected = {f"A{n}" for n in range(1, 6)} | {f"B{n}" for n in (6, 7, 8, 9, 11, 12, 13, 14)}
    expected |= {"B10a", "B10b", "B10c", "B10d"}
    assert set(codes) == expected
    assert IRAP_LAW.item("B9").treatment == "excluded"
    with pytest.raises(KeyError):
        IRAP_LAW.item("C17")


# --- the deductions: art. 11 --------------------------------------------------------------


def test_permanent_staff_cost_is_deducted_in_full_and_seasonal_staff_up_to_70_percent() -> None:
    staff = IRAP_LAW.permanent_staff
    assert staff.permanent_percent == Decimal(100)
    assert staff.seasonal_percent == Decimal(70)
    assert staff.seasonal_min_days == 120


def test_the_flat_deduction_per_non_permanent_employee() -> None:
    flat = IRAP_LAW.per_employee
    assert (flat.amount, flat.max_employees, flat.components_limit) == (
        Decimal(1850),
        5,
        Decimal(400000),
    )


@pytest.mark.parametrize(
    ("base", "deduction"),
    [
        ("0", "0"),
        ("-5", "0"),
        ("1000", "1000"),  # up to the base itself
        ("7999.99", "7999.99"),
        ("8000", "8000"),
        ("180759.91", "8000"),
        ("180759.92", "6000"),
        ("180839.91", "6000"),
        ("180839.92", "4000"),
        ("180919.91", "4000"),
        ("180919.92", "2000"),
        ("180999.91", "2000"),
        ("180999.92", "0"),
        ("5000000", "0"),
    ],
)
def test_the_small_base_deduction_follows_the_table_of_art_11_c_4_bis(
    base: str, deduction: str
) -> None:
    assert IRAP_LAW.small_base_deduction(Decimal(base)) == Decimal(deduction)


# --- the territory: art. 4 ----------------------------------------------------------------


def test_the_territorial_rule_is_the_registered_office_region_for_one_site() -> None:
    territory = IRAP_LAW.territory
    assert territory.single_site == "registered_office_region"
    assert territory.multi_region_key == "staff_pay_by_region"
    assert territory.minimum_months == 3
    assert territory.fallback == "domicile_region"


# --- the 2026 regional rates ------------------------------------------------------------


EXPECTED_2026 = {
    "01": "3.90",  # Piemonte
    "02": "3.90",  # Valle d'Aosta
    "03": "3.90",  # Lombardia
    "BZ": "3.90",
    "TN": "2.68",
    "05": "4.08",  # Veneto
    "06": "3.90",  # Friuli-Venezia Giulia
    "07": "3.90",  # Liguria
    "08": "3.90",  # Emilia-Romagna
    "09": "3.90",  # Toscana
    "10": "4.30",  # Umbria
    "11": "4.73",  # Marche
    "12": "4.82",  # Lazio
    "13": "4.82",  # Abruzzo
    "14": "4.97",  # Molise
    "15": "4.97",  # Campania
    "16": "4.82",  # Puglia
    "17": "3.90",  # Basilicata
    "18": "4.82",  # Calabria
    "19": "3.90",  # Sicilia
    "20": "2.93",  # Sardegna
}


def test_every_region_and_autonomous_province_has_its_2026_rate() -> None:
    (table,) = IRAP_LAW.regional_rates
    assert table.year == 2026
    assert {row.key: row.rate for row in table.rates} == {
        key: Decimal(rate) for key, rate in EXPECTED_2026.items()
    }
    assert len(table.rates) == 21  # 19 regions (Trentino-Alto Adige split in two provinces)


# (ID_IRAP, DATA_PUBBLICAZIONE) of the dataset's record for each key.
PUBLICATIONS_2026 = {
    "01": (2313, "2026-04-01"),
    "02": (2401, "2026-06-05"),
    "03": (2312, "2026-03-30"),
    "BZ": (2501, "2026-10-12"),
    "TN": (2302, "2026-03-20"),
    "05": (2421, "2026-06-09"),
    "06": (2383, "2026-05-21"),
    "07": (2303, "2026-03-20"),
    "08": (2320, "2026-04-02"),
    "09": (2318, "2026-04-13"),
    "10": (2481, "2026-07-30"),
    "11": (2307, "2026-03-20"),
    "12": (2361, "2026-04-14"),
    "13": (2309, "2026-03-26"),
    "14": (2441, "2026-07-20"),
    "15": (2325, "2026-04-01"),
    "16": (2461, "2026-07-23"),
    "17": (2317, "2026-04-13"),
    "18": (2306, "2026-03-20"),
    "19": (2305, "2026-03-30"),
    "20": (2381, "2026-05-13"),
}


def test_every_row_cites_its_publication() -> None:
    (table,) = IRAP_LAW.regional_rates
    assert {row.key: (row.mef_id, row.published) for row in table.rates} == PUBLICATIONS_2026
    assert all(row.norm and row.name for row in table.rates)


def test_rate_lookup_by_region_key_and_year() -> None:
    assert IRAP_LAW.rate_for(2026, "03") == Decimal("3.90")  # Lombardia
    assert IRAP_LAW.rate_for(2026, "12") == Decimal("4.82")  # Lazio
    assert IRAP_LAW.rate_for(2026, "15") == Decimal("4.97")  # Campania
    assert IRAP_LAW.rate_for(2026, "05") == Decimal("4.08")  # Veneto
    assert IRAP_LAW.rate_for(2026, "BZ") == Decimal("3.90")
    assert IRAP_LAW.rate_for(2026, " tn ") == Decimal("2.68")
    assert IRAP_LAW.rate_for(2026, "20") == Decimal("2.93")  # Sardegna


def test_rate_lookup_never_guesses() -> None:
    assert IRAP_LAW.rate_for(2025, "03") is None
    assert IRAP_LAW.rate_for(2027, "03") is None
    assert IRAP_LAW.rate_for(2026, "04") is None  # Trentino-Alto Adige is BZ and TN
    assert IRAP_LAW.rate_for(2026, "ZZ") is None
