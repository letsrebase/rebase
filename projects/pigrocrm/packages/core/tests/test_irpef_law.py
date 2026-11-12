"""The IRPEF brackets and the regional surcharges of tax year 2026 (REB-1062).

The figures are the sources' (`fiscal/irpef_2026.py` cites each); the worked examples below
apply them by hand. The 13,700 euro at 50,000 is the Agenzia delle Entrate's own, on its
«Aliquote e calcolo dell'Irpef» page (13 January 2026).
"""

from datetime import date
from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.household import Household, HouseholdMember
from pigrocrm.core.fiscal.irpef_2026 import (
    FAMILY_DETRACTIONS_2026,
    IRPEF_2026,
    REGIONAL_SURCHARGES_2026,
    SELF_EMPLOYMENT_DETRACTION_2026,
)
from pigrocrm.core.fiscal.law_irpef import region_key
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    NON_IT_PACK,
)

D = Decimal


def law():
    irpef = IT_STANDARD_PACK.irpef_for(2026)
    assert irpef is not None
    return irpef


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
        assert other.irpef_for(2026) is None


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
        # Bolzano: 430.50 euro off up to 90,000, never a credit, and from 50,000 euro a second
        # detraction growing to 125 euro at 75,000 and given up to 90,000 (l.p. 9/1998 art.
        # 21-sexiesdecies c. 4-bis; above 90,000 its literal reading is doubtful, so none)
        ("BZ", "30000", "0.00"),
        ("BZ", "40000", "61.50"),
        ("BZ", "90000", "751.50"),
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


@pytest.mark.parametrize(
    ("region_code", "province", "key"),
    [
        ("03", "MI", "03"),
        ("08", None, "08"),
        ("04", "BZ", "BZ"),
        ("04", " tn ", "TN"),
        ("04", None, None),
        ("04", "RE", None),
        (None, "MI", None),
    ],
)
def test_the_region_key_is_istats_code_and_for_trentino_alto_adige_the_province(
    region_code: str | None, province: str | None, key: str | None
) -> None:
    assert region_key(region_code, province) == key
    assert key is None or law().regional_law(key) is not None


def test_the_deduction_of_the_contributions_cites_art_10_of_the_tuir() -> None:
    deduction = law().social_security_deduction
    assert deduction is not None
    assert "art. 10 c. 1 lett. e)" in deduction.source
    assert "contributi previdenziali ed assistenziali versati" in deduction.wording


# --- art. 13 c. 5 TUIR: the detraction for self-employment income ---------------------------


@pytest.mark.parametrize(
    ("income", "amount"),
    [
        # a) 1,265 up to 5,500
        ("0", "1265.00"),
        ("5500", "1265.00"),
        # b) 500 + 765 x (28,000 - income) / 22,500, the ratio cut at four decimals: 18000 / 22500
        # is 0.8 exactly, 13000 / 22500 is 0.57777... and is 0.5777
        ("10000", "1112.00"),
        ("15000", "941.94"),
        ("20000", "771.96"),
        ("28000", "500.00"),
        # b-bis) 500 x (50,000 - income) / 22,000: 10000 / 22000 is 0.4545
        ("40000", "227.25"),
        ("50000", "0.00"),
        ("50000.01", "0.00"),
        ("120000", "0.00"),
    ],
)
def test_the_self_employment_detraction_follows_art_13_c_5(income: str, amount: str) -> None:
    assert SELF_EMPLOYMENT_DETRACTION_2026.amount(D(income)) == D(amount)


def test_the_self_employment_detraction_is_data_with_its_words_and_source() -> None:
    detraction = law().self_employment_detraction
    assert detraction is SELF_EMPLOYMENT_DETRACTION_2026
    assert "1.265 euro, se il reddito complessivo non supera 5.500 euro" in detraction.wording
    assert "art. 13 c. 5" in detraction.source and "§ 21.4" in detraction.source


# --- art. 12 TUIR: the family detractions ---------------------------------------------------


def _child(birth: date, **kwargs: object) -> HouseholdMember:
    return HouseholdMember(kind="child", birth_date=birth, **kwargs)  # type: ignore[arg-type]


def _household(*members: HouseholdMember, **kwargs: object) -> Household:
    return Household(year=2026, members=list(members), **kwargs)  # type: ignore[arg-type]


def _family(household: Household, income: str) -> dict[str, Decimal]:
    return dict(FAMILY_DETRACTIONS_2026.for_household(household, D(income)))


@pytest.mark.parametrize(
    ("income", "amount"),
    [
        # lett. a) 1): 800 - 110 x 10,000 / 15,000
        ("10000", "726.67"),
        ("15000", "690.00"),
        ("29000", "690.00"),
        # lett. b): +10, +20, +30, +20, +10 in the bands from 29,000 to 35,200
        ("29100", "700.00"),
        ("29300", "710.00"),
        ("34800", "720.00"),
        ("35050", "710.00"),
        ("35150", "700.00"),
        ("35200", "700.00"),
        ("35201", "690.00"),
        # lett. a) 3): 690 x (80,000 - 60,000) / 40,000
        ("60000", "345.00"),
        ("80000", "0.00"),
    ],
)
def test_the_spouse_detraction_follows_art_12_c_1_lett_a(income: str, amount: str) -> None:
    household = _household(HouseholdMember(kind="spouse"))
    given = _family(household, income)
    assert given.get("spouse", D("0.00")) == D(amount)


def test_the_spouse_detraction_is_prorated_by_months() -> None:
    household = _household(HouseholdMember(kind="spouse", months=6))
    assert _family(household, "60000") == {"spouse": D("172.50")}


def test_a_child_of_21_to_29_gives_950_euro_by_the_ratio_of_95000() -> None:
    """Born in 2001, 25 all year, at a reference income of 30,000: 950 x (95,000 - 30,000) /
    95,000, the ratio 0.68421 cut at four decimals, so 950 x 0.6842."""
    assert _family(_household(_child(date(2001, 6, 15))), "30000") == {"children": D("649.99")}


def test_the_limit_of_95000_grows_by_15000_for_every_child_after_the_first() -> None:
    """Two counted children: 110,000 for both, so 950 x 0.7272 = 690.84 each."""
    two = _household(_child(date(2001, 6, 15)), _child(date(1998, 1, 1)))
    assert _family(two, "30000") == {"children": D("1381.68")}


def test_a_child_under_21_gives_no_detraction_and_a_disabled_one_over_30_gives_it() -> None:
    assert _family(_household(_child(date(2008, 6, 15))), "30000") == {}
    assert _family(_household(_child(date(1990, 1, 1))), "30000") == {}
    disabled = _household(_child(date(1990, 1, 1), disabled=True))
    assert _family(disabled, "30000") == {"children": D("649.99")}
    # Disabled and under 21: the youngest have no detraction either.
    assert _family(_household(_child(date(2008, 6, 15), disabled=True)), "30000") == {}


def test_the_child_detraction_counts_the_months_from_the_21st_birthday() -> None:
    """Born 10 March 2005: 21 from March, so ten months of 2026 (the month the age is reached
    counts); born 10 March 1996: 30 in March, so only January and February (the month the
    upper age is reached does not count)."""
    turning_21 = _family(_household(_child(date(2005, 3, 10))), "30000")
    assert turning_21 == {"children": D("541.66")}
    turning_30 = _family(_household(_child(date(1996, 3, 10))), "30000")
    assert turning_30 == {"children": D("108.33")}


def test_the_share_and_the_months_of_the_child_prorate_the_detraction() -> None:
    household = _household(_child(date(2001, 6, 15), share_percent=50, months=6))
    assert _family(household, "30000") == {"children": D("162.50")}


def test_a_cohabiting_ascendant_gives_750_euro_by_the_ratio_of_80000() -> None:
    household = _household(HouseholdMember(kind="ascendant", share_percent=50))
    # 750 x (80,000 - 30,000) / 80,000 = 468.75, half of it
    assert _family(household, "30000") == {"ascendants": D("234.38")}
    assert _family(household, "80000") == {}


def test_no_family_detraction_is_due_at_an_income_of_zero_or_above_the_limits() -> None:
    household = _household(_child(date(2001, 6, 15)), HouseholdMember(kind="spouse"))
    assert _family(household, "0") == {"spouse": D("800.00")}
    assert _family(household, "95000") == {}


# --- the regions' household rules -----------------------------------------------------------


@pytest.mark.parametrize(
    ("income", "amount"),
    [
        # Bolzano's second detraction, read literally with no upper limit of income
        ("40000", "61.50"),  # 492.00 - 430.50
        ("60000", "307.50"),  # 788.00 - 430.50 - 50.00
        ("75000", "492.00"),  # 1,047.50 - 430.50 - 125.00
        ("100000", "1480.00"),  # neither detraction above 90,000
    ],
)
def test_bolzanos_second_detraction_grows_from_50000_to_125_at_75000_and_stops_at_90000(
    income: str, amount: str
) -> None:
    assert law().regional_surcharge("BZ", D(income)) == D(amount)


def _surcharge(region: str, income: str, household: Household | None = None) -> Decimal:
    amount = law().regional_surcharge(region, D(income), household)
    assert amount is not None
    return amount


def test_without_a_household_no_regional_household_rule_applies() -> None:
    for region in ("01", "05", "11", "15", "16", "20", "TN", "BZ"):
        assert _surcharge(region, "40000") == _surcharge(region, "40000", _household())


def test_piemonte_gives_100_euro_per_child_from_three_children_and_500_per_disabled() -> None:
    young = [_child(date(2016, 1, 1)), _child(date(2018, 1, 1))]
    assert _surcharge("01", "40000", _household(*young)) == D("988.60")  # two: none
    three = _household(*young, _child(date(2020, 1, 1)))
    assert _surcharge("01", "40000", three) == D("688.60")  # 988.60 - 3 x 100
    disabled = _household(*young, _child(date(2020, 1, 1), disabled=True))
    assert _surcharge("01", "40000", disabled) == D("188.60")  # 988.60 - 3 x 100 - 500


def test_veneto_charges_0_9_percent_for_a_disabled_taxpayer_up_to_50000() -> None:
    disabled = _household(taxpayer_disabled=True)
    assert _surcharge("05", "40000", disabled) == D("360.00")
    assert _surcharge("05", "50000", disabled) == D("450.00")
    assert _surcharge("05", "50001", disabled) == D("615.01")  # 1.23% above the limit


def test_a_disabled_dependant_of_several_people_needs_the_others_income() -> None:
    """Veneto: the sum of the taxable incomes of everyone the dependant depends on is at most
    50,000. With the others' income not stated the answer is open (`undetermined`)."""
    rule = law().regional_law("05")
    assert rule is not None
    dependant = HouseholdMember(kind="ascendant", disabled=True)
    open_answer = _household(dependant)
    assert rule.undetermined(D(40000), open_answer)
    alone = _household(dependant, other_supporters_income=D(0))
    assert not rule.undetermined(D(40000), alone) and rule.surcharge(D(40000), alone) == D("360.00")
    shared = _household(dependant, other_supporters_income=D(15000))
    assert rule.surcharge(D(40000), shared) == D("492.00")  # 55,000 together: the ordinary rate


def test_marche_drops_its_increases_for_a_disabled_child_up_to_50000() -> None:
    child = _child(date(2016, 1, 1), disabled=True)
    household = _household(child, other_supporters_income=D(0))
    assert _surcharge("11", "40000", household) == D("492.00")  # 1.23% on all of it
    assert _surcharge("11", "40000") == D("587.40")
    # A disabled spouse is not a disabled child.
    spouse = _household(HouseholdMember(kind="spouse", disabled=True), other_supporters_income=D(0))
    assert _surcharge("11", "40000", spouse) == D("587.40")


def test_campania_gives_30_euro_per_child_from_two_and_40_per_disabled_up_to_28000() -> None:
    kids = [_child(date(2016, 1, 1)), _child(date(2018, 1, 1))]
    assert _surcharge("15", "25000", _household(kids[0])) == D("555.50")  # one: none
    assert _surcharge("15", "25000", _household(*kids)) == D("495.50")  # 555.50 - 60
    assert _surcharge("15", "29000", _household(*kids)) == D("676.30")  # above the limit
    # The 30 is left off a disabled child, which counts toward the two: 30 + 40.
    one_disabled = _household(kids[0], _child(date(2018, 1, 1), disabled=True))
    assert _surcharge("15", "25000", one_disabled) == D("485.50")


def test_puglia_gives_375_for_a_disabled_child_only_with_more_than_three_children() -> None:
    kids = [_child(date(2002, 1, 1)), _child(date(2003, 1, 1)), _child(date(2004, 1, 1))]
    four = _household(*kids, _child(date(1990, 1, 1), disabled=True))
    assert _surcharge("16", "40000", four) == D("409.00")  # 864.00 - 4 x 20 - 375
    three = _household(*kids[:2], _child(date(1990, 1, 1), disabled=True))
    assert _surcharge("16", "40000", three) == D("864.00")  # the literal reading: nothing


def test_puglia_counts_the_children_of_art_12_lett_c_and_not_the_younger_ones() -> None:
    """Its law increases «le detrazioni previste dall'articolo 12, comma 1, lettera c)»: four
    children under 21 have none, so nothing is increased."""
    young = [_child(date(2016 + n, 1, 1)) for n in range(4)]
    assert _surcharge("16", "40000", _household(*young)) == D("864.00")
    # A disabled child counts from 21 on, not before.
    assert _surcharge(
        "16", "40000", _household(*young[:3], _child(date(2010, 1, 1), disabled=True))
    ) == (D("864.00"))


def test_sardegna_counts_the_minors_of_1_january_by_the_months_of_minority() -> None:
    assert _surcharge("20", "30000", _household(_child(date(2015, 3, 10)))) == D("169.00")
    # 18 on 20 July: minor on the 15th of July, so seven months: 200 x 7 / 12.
    turning_18 = _household(_child(date(2008, 7, 20)))
    assert _surcharge("20", "30000", turning_18) == D("252.33")
    # Born in the year: not a minor on 1 January. A disabled minor adds 100.
    assert _surcharge("20", "30000", _household(_child(date(2026, 2, 1)))) == D("369.00")
    assert _surcharge("20", "30000", _household(_child(date(2015, 3, 10), disabled=True))) == D(
        "69.00"
    )
    assert _surcharge("20", "50001", _household(_child(date(2015, 3, 10)))) == D("615.01")


def test_trento_and_bolzano_give_a_detraction_per_child_at_any_age_up_to_their_limit() -> None:
    adult = _household(_child(date(2000, 1, 1)))
    assert _surcharge("TN", "40000", adult) == D("246.00")  # 492.00 - 246
    assert _surcharge("TN", "60000", adult) == D("788.00")  # above 50,000: none
    assert _surcharge("BZ", "40000", adult) == D("0.00")  # 61.50 - 340, never a credit
    assert _surcharge("BZ", "60000", adult) == D("0.00")  # 307.50 - 340
    assert _surcharge("BZ", "90001", adult) == D("1307.02")  # above 90,000: neither


def test_a_child_born_in_the_year_counts_from_its_birth_month_in_every_rule() -> None:
    """Born on 5 September 2026: four months (September to December), so Trento's 246 euro is
    82, and the 12 months a household may state are capped by it."""
    baby = _household(_child(date(2026, 9, 5)))
    assert _surcharge("TN", "40000", baby) == D("410.00")  # 492.00 - 246 x 4 / 12
    assert _surcharge("BZ", "60000", baby) == D("194.17")  # 307.50 - 340 x 4 / 12
