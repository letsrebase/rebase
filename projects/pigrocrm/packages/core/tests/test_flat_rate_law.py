"""REB-1066: the income basis of each regime and the flat-rate regime's law, as pack data.

Each figure is a row of the frame's A5 register, verified on 2026-11-11 against L. 190/2014
art. 1 c. 54-89 on Normattiva (every text in force since 1 January 2015), the tax agency's
instructions for the 2026 individuals' return and its page on how IRPEF is paid. What is
asserted here is that the pack carries them as the sources state them; the estimate that reads
them is tested in `test_fiscal_estimate.py` and `test_tax_year_law.py`.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from pigrocrm.core.fiscal.law_flat_rate import ANNEX_4, FLAT_RATE_2026
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    NON_IT_PACK,
)

# --- the income basis ------------------------------------------------------------------


def test_each_regime_states_the_basis_its_income_is_measured_on() -> None:
    flat_rate = IT_FLAT_RATE_PACK.law_for(2026)
    person = IT_STANDARD_PACK.law_for(2026)
    company = IT_COMPANY_PACK.law_for(2026)
    assert flat_rate is not None and person is not None and company is not None
    assert flat_rate.income_basis is not None and flat_rate.income_basis.basis == "cash"
    assert person.income_basis is not None and person.income_basis.basis == "cash"
    assert company.income_basis is not None and company.income_basis.basis == "accrual"
    assert NON_IT_PACK.law_for(2026) is None


def test_every_basis_names_its_article_and_quotes_the_law() -> None:
    flat_rate = IT_FLAT_RATE_PACK.law_for(2026)
    person = IT_STANDARD_PACK.law_for(2026)
    company = IT_COMPANY_PACK.law_for(2026)
    assert flat_rate is not None and person is not None and company is not None
    assert flat_rate.income_basis is not None
    assert person.income_basis is not None
    assert company.income_basis is not None
    # Art. 54 c. 1 TUIR: what was received and what was spent in the period.
    assert "art. 54 c. 1" in person.income_basis.source
    assert "percepiti nel periodo di imposta" in person.income_basis.wording
    # Art. 83 c. 1 and art. 109 c. 1 TUIR: the income statement and the year of competence.
    assert "art. 83 c. 1" in company.income_basis.source
    assert "art. 109 c. 1" in company.income_basis.source
    assert "conto economico" in company.income_basis.wording
    assert "esercizio di competenza" in company.income_basis.wording
    # L. 190/2014 art. 1 c. 64: revenue and fees collected.
    assert "art. 1 c. 64" in flat_rate.income_basis.source
    assert "ricavi o dei compensi percepiti" in flat_rate.income_basis.wording
    assert "principio di cassa" in flat_rate.income_basis.wording


# --- annex 4 ---------------------------------------------------------------------------


def test_annex_4_is_the_nine_groups_with_the_coefficients_the_law_prints() -> None:
    assert [group.number for group in ANNEX_4] == list(range(1, 10))
    assert [group.coefficient for group in ANNEX_4] == [
        Decimal(value) for value in (40, 40, 40, 54, 86, 62, 40, 78, 67)
    ]
    assert len({group.key for group in ANNEX_4}) == 9
    # The group names are the annex's own, so a later reader can check them against the law.
    assert ANNEX_4[0].name == "Industrie alimentari e delle bevande"
    assert ANNEX_4[7].name.startswith("Attività professionali, scientifiche, tecniche")
    assert ANNEX_4[8].name == "Altre attività economiche"


def test_annex_4_codes_are_the_ateco_2007_codes_of_each_group() -> None:
    by_number = {group.number: group.ateco_2007 for group in ANNEX_4}
    assert by_number[1] == ("10", "11")
    assert by_number[2] == ("45", "46.2-46.9", "47.1-47.7", "47.9")
    assert by_number[3] == ("47.81",)
    assert by_number[4] == ("47.82", "47.89")
    assert by_number[5] == ("41", "42", "43", "68")
    assert by_number[6] == ("46.1",)
    assert by_number[7] == ("55", "56")
    assert by_number[8] == ("64", "65", "66", "69", "70", "71", "72", "73", "74", "75") + (
        "85",
        "86",
        "87",
        "88",
    )
    # The last group lists 63 divisions: 01-03, 05-09, 12-33, 35, 36-39, 49-53, 58-63, 77-82,
    # 84, 90-93, 94-96, 97-98 and 99.
    other = by_number[9]
    assert len(other) == 63
    assert other[:8] == ("01", "02", "03", "05", "06", "07", "08", "09")
    assert other[-1] == "99"
    # No division sits in two groups.
    divisions = [code for group in ANNEX_4 for code in group.ateco_2007 if "." not in code]
    assert len(divisions) == len(set(divisions))


def test_every_year_of_the_regime_has_the_same_table() -> None:
    """The annex's coefficients and codes are the same in the texts in force from 2015, 2016
    and 2019 (they differ in the turnover thresholds the 2019 text dropped)."""
    for year in (2015, 2016, 2019, 2026, 2040):
        law = IT_FLAT_RATE_PACK.law_for(year)
        assert law is not None and law.flat_rate is not None
        assert law.flat_rate.coefficients == ANNEX_4
        assert law.flat_rate.ateco_edition == "ATECO 2007"


def test_a_coefficient_is_listed_when_it_is_one_of_the_annexs() -> None:
    law = FLAT_RATE_2026
    assert law.coefficient_values == {Decimal(v) for v in (40, 54, 62, 67, 78, 86)}
    # The profile stores `67.00`; the comparison is numeric, not textual.
    assert law.lists_coefficient(Decimal("67.00"))
    assert law.lists_coefficient(Decimal("40"))
    assert not law.lists_coefficient(Decimal("70.00"))
    assert not law.lists_coefficient(Decimal("0.00"))
    assert not law.lists_coefficient(Decimal("66.99"))


def test_a_group_is_found_by_its_key() -> None:
    assert FLAT_RATE_2026.group("construction_and_real_estate").coefficient == Decimal("86")
    with pytest.raises(KeyError):
        FLAT_RATE_2026.group("no_such_group")


# --- the substitute tax rates and the start-up relief (c. 64 and c. 65) ----------------


def test_the_substitute_tax_is_15_percent_and_5_percent_for_a_start_up_from_2016() -> None:
    for year in (2015, 2016, 2026):
        law = IT_FLAT_RATE_PACK.law_for(year)
        assert law is not None and law.flat_rate is not None
        assert law.flat_rate.standard_rate_percent == Decimal("15")
    law_2016 = IT_FLAT_RATE_PACK.law_for(2016)
    assert law_2016 is not None and law_2016.flat_rate is not None
    # c. 65 as in force from 1-1-2016: the year the activity starts and the four after it.
    assert law_2016.flat_rate.startup_rate_percent == Decimal("5")
    assert law_2016.flat_rate.startup_following_years == 4
    assert law_2016.flat_rate.startup_income_reduction is None
    assert IT_FLAT_RATE_PACK.law_for(2030) is not None
    assert IT_FLAT_RATE_PACK.law_for(2030).flat_rate.startup_rate_percent == Decimal("5")  # type: ignore[union-attr]


def test_in_2015_the_start_up_relief_was_a_third_off_the_income_and_not_a_rate() -> None:
    """c. 65 as first enacted (Normattiva, text in force 1-1-2015 to 31-12-2015): the income
    reduced by a third for the year of start and the two after it, at the ordinary 15%. The
    5% rate came with L. 208/2015 art. 1 c. 111."""
    law = IT_FLAT_RATE_PACK.law_for(2015)
    assert law is not None and law.flat_rate is not None
    assert law.flat_rate.startup_rate_percent is None
    assert law.flat_rate.startup_income_reduction == Fraction(1, 3)
    assert law.flat_rate.startup_following_years == 2


def test_the_start_up_conditions_are_the_three_of_c_65() -> None:
    conditions = FLAT_RATE_2026.startup_conditions
    assert len(conditions) == 3
    assert "three years before" in conditions[0] and "lett. a" in conditions[0]
    assert "mere continuation" in conditions[1] and "lett. b" in conditions[1]
    assert "limit of c. 54" in conditions[2] and "lett. c" in conditions[2]


# --- what each rate applies to ---------------------------------------------------------


def test_the_contributions_are_deducted_before_the_substitute_tax_and_not_after_it() -> None:
    """L. 190/2014 art. 1 c. 64: the contributions «si deducono dal reddito determinato ai
    sensi del presente comma», and the Agenzia's return takes the substitute tax on the income
    net of them (row LM36 then LM39). They are owed on the income itself (c. 77)."""
    for law in IT_FLAT_RATE_PACK.laws:
        assert law.flat_rate is not None
        assert law.flat_rate.contribution_base == "flat_rate_income"
        assert law.flat_rate.substitute_tax_base == "flat_rate_income_net_of_contributions"


# --- the substitute tax advances -------------------------------------------------------


def test_the_advances_are_stated_from_2026_only() -> None:
    """The Agenzia's page was read in 2026 and the pack states no earlier year's rule."""
    for year in (2015, 2016, 2025):
        law = IT_FLAT_RATE_PACK.law_for(year)
        assert law is not None and law.flat_rate is not None
        assert law.flat_rate.advances is None
    law = IT_FLAT_RATE_PACK.law_for(2026)
    assert law is not None and law.flat_rate is not None
    advances = law.flat_rate.advances
    assert advances is not None
    # Agenzia, «Come si paga l'Irpef» (12-3-2026) and art. 58 D.L. 124/2019.
    assert advances.advance_percent_of_base == Decimal("100")
    assert (advances.first_advance_percent, advances.second_advance_percent) == (50, 50)
    assert advances.first_advance_percent + advances.second_advance_percent == Decimal("100")
    assert advances.no_advance_at_or_below == Decimal("51.65")
    assert advances.single_november_payment_at_or_below == Decimal("206")
    assert advances.deferral_surcharge_percent == Decimal("0.40")
    assert advances.deferral_window_days == 30
