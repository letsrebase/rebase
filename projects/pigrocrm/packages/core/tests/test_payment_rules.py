"""REB-1067: the payment rules, the F24 codes and the filing dates the packs carry.

Every figure is a verified row of the frame's A5 register, read from its primary source on
2026-11-11. What is proved here is that the pack carries exactly those, that the lookups answer
the Agenzia delle Entrate's own worked examples (its «Rateazione» tables, its 2026 filing dates),
that a year or a form the pack has no entry for answers `None` and never borrows, and that a
number the law states twice is the same number in both places.
"""

from datetime import date
from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.law_payment import (
    ForecastAdvanceLaw,
    PaymentRulesLaw,
    ReturnFilingRule,
    commercial_days,
)
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    NON_IT_PACK,
    FiscalPack,
)
from pigrocrm.core.fiscal.payment_rules import (
    instalment_interest_percent,
    instalment_schedule,
    return_filing_date,
)


def _rules(pack: FiscalPack, year: int = 2026) -> PaymentRulesLaw:
    rules = pack.payment_rules_for(year)
    assert rules is not None
    return rules


def _codes(pack: FiscalPack) -> dict[str, tuple[str, str]]:
    return {code.line: (code.code, code.kind) for code in _rules(pack).f24_codes}


# --- which pack carries what, and for which year ----------------------------------------


def test_the_three_italian_packs_carry_rules_from_2026_and_a_year_before_has_none() -> None:
    for pack in (IT_STANDARD_PACK, IT_FLAT_RATE_PACK, IT_COMPANY_PACK):
        assert pack.payment_rules_for(2025) is None
        assert pack.payment_rules_for(2026) is pack.payment_rules[0]
        assert pack.payment_rules_for(2031) is pack.payment_rules[0]  # until a decree moves it
    assert NON_IT_PACK.payment_rules == ()
    assert NON_IT_PACK.payment_rules_for(2026) is None


def test_payment_rule_entries_must_be_in_increasing_year_order() -> None:
    first = _rules(IT_STANDARD_PACK)
    with pytest.raises(ValueError, match="increasing"):
        FiscalPack(
            id="x",
            version="1",
            ceilings=(),
            charges=(),
            payment_rules=(first, first),
        )


# --- instalments (art. 20 D.Lgs. 241/1997; DM 21 May 2009 art. 5) ------------------------


def test_the_instalment_rule_is_the_same_for_a_person_and_a_company() -> None:
    standard = _rules(IT_STANDARD_PACK).instalments
    assert standard == _rules(IT_FLAT_RATE_PACK).instalments == _rules(IT_COMPANY_PACK).instalments
    assert standard.may_be_paid_in_instalments("balance")
    assert standard.may_be_paid_in_instalments("first_advance")
    assert not standard.may_be_paid_in_instalments("second_advance")
    assert (standard.last_instalment.month, standard.last_instalment.day) == (12, 16)
    assert standard.instalment_day == 16
    assert standard.annual_interest_percent == Decimal("4.00")
    assert standard.later_instalment_interest_percent == Decimal("0.33")


def test_the_agenzias_rateazione_table_for_a_first_payment_on_30_june_2026() -> None:
    """Redditi PF 2026 fascicolo 1, «Rateazione», left column: instalment, payment day and
    interest, down to the seventh on 16 December."""
    law = _rules(IT_STANDARD_PACK).instalments
    schedule = instalment_schedule(law, date(2026, 6, 30), 2026)
    assert [(e.number, e.due_date, e.interest_percent) for e in schedule] == [
        (1, date(2026, 6, 30), Decimal("0.00")),
        (2, date(2026, 7, 16), Decimal("0.18")),
        (3, date(2026, 8, 20), Decimal("0.51")),
        (4, date(2026, 9, 16), Decimal("0.84")),
        (5, date(2026, 10, 16), Decimal("1.17")),
        (6, date(2026, 11, 16), Decimal("1.50")),
        (7, date(2026, 12, 16), Decimal("1.83")),
    ]


def test_the_agenzias_rateazione_table_for_a_first_payment_deferred_to_30_july_2026() -> None:
    """The right column: the second instalment falls on 20 August (the 16th is in the August
    window) and still carries 0.18%, counted to the nominal 16 August."""
    law = _rules(IT_STANDARD_PACK).instalments
    schedule = instalment_schedule(law, date(2026, 7, 30), 2026)
    assert [(e.number, e.due_date, e.interest_percent) for e in schedule] == [
        (1, date(2026, 7, 30), Decimal("0.00")),
        (2, date(2026, 8, 20), Decimal("0.18")),
        (3, date(2026, 9, 16), Decimal("0.51")),
        (4, date(2026, 10, 16), Decimal("0.84")),
        (5, date(2026, 11, 16), Decimal("1.17")),
        (6, date(2026, 12, 16), Decimal("1.50")),
    ]
    assert schedule[1].nominal_date == date(2026, 8, 16)


def test_an_instalment_on_a_non_working_day_moves_but_its_interest_does_not() -> None:
    law = _rules(IT_COMPANY_PACK).instalments
    schedule = instalment_schedule(law, date(2026, 12, 31), 2027)
    assert schedule[1].nominal_date == date(2027, 1, 16)  # a Saturday
    assert schedule[1].due_date == date(2027, 1, 18)
    assert schedule[1].interest_percent == Decimal("0.18")


def test_a_first_payment_on_the_last_day_of_february_counts_16_days_to_the_16th_of_march() -> None:
    """A year ending 31 August: the balance falls on 28 February (29 in 2028). The count runs from
    the day after, both ends counted: 1 to 16 March is 16 days and 0.18%, the length of February
    never enters (it was 18 days and 0.20% when the last day of February counted as the 28th)."""
    law = _rules(IT_COMPANY_PACK).instalments
    for first_due, nominal in (
        (date(2027, 2, 28), date(2027, 3, 16)),
        (date(2028, 2, 29), date(2028, 3, 16)),
        (date(2026, 6, 30), date(2026, 7, 16)),  # the Agenzia's own example
        (date(2026, 7, 31), date(2026, 8, 16)),  # the 31st counts as the 30th
        (date(2026, 4, 30), date(2026, 5, 16)),
    ):
        assert law.second_instalment_interest_percent(first_due, nominal) == Decimal("0.18")
    schedule = instalment_schedule(law, date(2027, 2, 28), 2027)
    assert [e.interest_percent for e in schedule[:4]] == [
        Decimal("0.00"),
        Decimal("0.18"),
        Decimal("0.51"),
        Decimal("0.84"),
    ]
    assert commercial_days(date(2027, 2, 28), date(2027, 3, 16)) == 16
    # A start in the middle of a month is counted by its own day.
    assert commercial_days(date(2026, 6, 10), date(2026, 7, 16)) == 36


def test_the_interest_of_an_instalment_is_looked_up_and_a_missing_one_is_none() -> None:
    law = _rules(IT_STANDARD_PACK).instalments
    assert instalment_interest_percent(law, date(2026, 6, 30), 1, 2026) == Decimal("0.00")
    assert instalment_interest_percent(law, date(2026, 6, 30), 7, 2026) == Decimal("1.83")
    assert instalment_interest_percent(law, date(2026, 6, 30), 8, 2026) is None
    # A first payment after the last day has no later instalment.
    assert [e.number for e in instalment_schedule(law, date(2026, 12, 16), 2026)] == [1]


def test_the_thirty_day_deferral_is_the_same_for_every_pack_and_agrees_with_the_advances() -> None:
    for pack in (IT_STANDARD_PACK, IT_FLAT_RATE_PACK, IT_COMPANY_PACK):
        deferral = _rules(pack).deferral
        assert (deferral.surcharge_percent, deferral.window_days) == (Decimal("0.40"), 30)
    standard = IT_STANDARD_PACK.law_for(2026)
    assert standard is not None and standard.advances is not None
    standard_deferral = _rules(IT_STANDARD_PACK).deferral
    assert standard.advances.deferral_surcharge_percent == standard_deferral.surcharge_percent
    assert standard.advances.deferral_window_days == standard_deferral.window_days


# --- the forecast method and a shortfall --------------------------------------------------


def test_the_forecast_method_conditions_are_art_4_c_2_of_dl_69_1989() -> None:
    person = _rules(IT_STANDARD_PACK).forecast
    assert person == _rules(IT_FLAT_RATE_PACK).forecast
    assert person == ForecastAdvanceLaw(
        first_instalment_min_percent=Decimal("40"),
        total_min_percent=Decimal("100"),
        omitted_regular_if_tax_not_above=Decimal("51.65"),
    )
    # 40,000 lire for IRES and IRAP.
    assert _rules(IT_COMPANY_PACK).forecast.omitted_regular_if_tax_not_above == Decimal("20.66")


def test_the_penalty_on_a_short_payment_is_art_13_dlgs_471_1997_as_amended_in_2024() -> None:
    shortfall = _rules(IT_STANDARD_PACK).shortfall
    assert shortfall == _rules(IT_FLAT_RATE_PACK).shortfall == _rules(IT_COMPANY_PACK).shortfall
    assert shortfall.penalty_percent == Decimal("25")
    assert shortfall.late_penalty_percent(0) == Decimal(0)
    assert shortfall.late_penalty_percent(1) == Decimal("0.8333")  # 12.5% / 15 a day
    assert shortfall.late_penalty_percent(6) == Decimal("5.0000")
    assert shortfall.late_penalty_percent(15) == Decimal("12.5000")
    assert shortfall.late_penalty_percent(16) == Decimal("12.5")  # half, up to ninety days
    assert shortfall.late_penalty_percent(90) == Decimal("12.5")
    assert shortfall.late_penalty_percent(91) == Decimal("25")


def test_a_payment_regularised_by_the_taxpayer_pays_a_tenth_then_a_ninth_up_to_90_days() -> None:
    """Art. 13 c. 1 lett. a) and a-bis) D.Lgs. 472/1997 applied to the penalty above; beyond 90
    days the pack states no tier (the eighth and the seventh turn on a boundary no source read
    settles), so it answers nothing rather than a figure."""
    shortfall = _rules(IT_STANDARD_PACK).shortfall
    assert shortfall.regularisation_penalty_percent(0) == Decimal(0)
    assert shortfall.regularisation_penalty_percent(1) == Decimal("0.0833")  # 1/10 of 0.8333
    assert shortfall.regularisation_penalty_percent(10) == Decimal("0.8333")
    assert shortfall.regularisation_penalty_percent(15) == Decimal("1.2500")
    assert shortfall.regularisation_penalty_percent(20) == Decimal("1.2500")  # 1/10 of 12.5%
    assert shortfall.regularisation_penalty_percent(30) == Decimal("1.2500")
    assert shortfall.regularisation_penalty_percent(31) == Decimal("1.3889")  # 1/9 of 12.5%
    assert shortfall.regularisation_penalty_percent(60) == Decimal("1.3889")
    assert shortfall.regularisation_penalty_percent(90) == Decimal("1.3889")
    assert shortfall.regularisation_penalty_percent(91) is None


def test_the_legal_interest_rate_is_the_one_fixed_for_each_year_and_never_guessed() -> None:
    shortfall = _rules(IT_STANDARD_PACK).shortfall
    assert shortfall.legal_interest_percent(2025) == Decimal("2.00")
    assert shortfall.legal_interest_percent(2026) == Decimal("1.60")
    assert shortfall.legal_interest_percent(2027) is None
    assert all(rate.source for rate in shortfall.legal_interest)


# --- the F24 codes ------------------------------------------------------------------------

_INPS = {
    "inps_gs_contribution": ("PXX", "inps_causale"),
    "inps_gs_contribution_other_cover": ("P10", "inps_causale"),
    "inps_gs_instalment": ("PXXR", "inps_causale"),
    "inps_gs_instalment_other_cover": ("P10R", "inps_causale"),
    "inps_gs_interest": ("DPPI", "inps_causale"),
    "inps_artigiani_interest": ("API", "inps_causale"),
    "inps_commercianti_interest": ("CPI", "inps_causale"),
    "inps_artigiani_minimum": ("AF", "inps_causale"),
    "inps_commercianti_minimum": ("CF", "inps_causale"),
    "inps_artigiani_excess": ("AP", "inps_causale"),
    "inps_commercianti_excess": ("CP", "inps_causale"),
    "inps_artigiani_excess_instalment": ("APR", "inps_causale"),
    "inps_commercianti_excess_instalment": ("CPR", "inps_causale"),
}


def test_the_ordinary_pack_carries_the_irpef_surcharge_interest_and_inps_codes() -> None:
    expected = {
        "irpef_balance": ("4001", "tax_code"),
        "irpef_first_advance": ("4033", "tax_code"),
        "irpef_second_advance": ("4034", "tax_code"),
        "regional_surcharge_balance": ("3801", "tax_code"),
        "municipal_surcharge_balance": ("3844", "tax_code"),
        "municipal_surcharge_advance": ("3843", "tax_code"),
        "instalment_interest_erario": ("1668", "tax_code"),
        "instalment_interest_regional": ("3805", "tax_code"),
        "instalment_interest_municipal": ("3857", "tax_code"),
        **_INPS,
    }
    assert _codes(IT_STANDARD_PACK) == expected


def test_the_flat_rate_pack_carries_the_substitute_tax_and_inps_codes() -> None:
    assert _codes(IT_FLAT_RATE_PACK) == {
        "substitute_tax_balance": ("1792", "tax_code"),
        "substitute_tax_first_advance": ("1790", "tax_code"),
        "substitute_tax_second_advance": ("1791", "tax_code"),
        "instalment_interest_erario": ("1668", "tax_code"),
        **_INPS,
    }


def test_the_company_pack_carries_ires_irap_and_chamber_fee_codes() -> None:
    assert _codes(IT_COMPANY_PACK) == {
        "ires_balance": ("2003", "tax_code"),
        "ires_first_advance": ("2001", "tax_code"),
        "ires_second_advance": ("2002", "tax_code"),
        "irap_balance": ("3800", "tax_code"),
        "irap_first_advance": ("3812", "tax_code"),
        "irap_second_advance": ("3813", "tax_code"),
        "chamber_fee": ("3850", "tax_code"),
        "instalment_interest_erario": ("1668", "tax_code"),
        "instalment_interest_regional": ("3805", "tax_code"),
    }


def test_a_line_a_regime_does_not_owe_has_no_code() -> None:
    with pytest.raises(KeyError):
        _rules(IT_COMPANY_PACK).f24_code("irpef_balance")
    with pytest.raises(KeyError):
        _rules(IT_FLAT_RATE_PACK).f24_code("regional_surcharge_balance")
    assert _rules(IT_STANDARD_PACK).f24_code("irpef_balance").code == "4001"


def test_every_code_names_what_it_is_and_where_it_was_read() -> None:
    for pack in (IT_STANDARD_PACK, IT_FLAT_RATE_PACK, IT_COMPANY_PACK):
        for code in _rules(pack).f24_codes:
            assert code.description
            if code.kind == "tax_code":
                assert code.source.startswith("Agenzia delle Entrate")
                assert code.code.isdigit() and len(code.code) == 4
            else:
                assert code.source.startswith("INPS")


def test_a_pack_cannot_carry_one_line_or_one_form_twice() -> None:
    rules = _rules(IT_STANDARD_PACK)
    with pytest.raises(ValueError, match="one code"):
        PaymentRulesLaw(
            from_year=2026,
            instalments=rules.instalments,
            deferral=rules.deferral,
            forecast=rules.forecast,
            shortfall=rules.shortfall,
            f24_codes=(rules.f24_codes[0], rules.f24_codes[0]),
            filings=(),
        )
    with pytest.raises(ValueError, match="one filing rule"):
        PaymentRulesLaw(
            from_year=2026,
            instalments=rules.instalments,
            deferral=rules.deferral,
            forecast=rules.forecast,
            shortfall=rules.shortfall,
            f24_codes=(),
            filings=(rules.filings[0], rules.filings[0]),
        )


# --- filing dates --------------------------------------------------------------------------


def test_a_person_files_by_31_october_which_in_2026_moves_to_2_november() -> None:
    for pack in (IT_STANDARD_PACK, IT_FLAT_RATE_PACK):
        found = return_filing_date(pack, "redditi_pf", date(2025, 12, 31))
        assert found is not None
        assert found.nominal_date == date(2026, 10, 31)  # a Saturday
        assert found.due_date == date(2026, 11, 2)
        assert found.source.startswith("D.P.R. 322/1998 art. 2 c. 1")


def test_a_company_files_by_the_last_day_of_the_tenth_month_after_its_period_closes() -> None:
    for form in ("redditi_sc", "irap"):
        calendar_year = return_filing_date(IT_COMPANY_PACK, form, date(2025, 12, 31))
        assert calendar_year is not None
        assert (calendar_year.nominal_date, calendar_year.due_date) == (
            date(2026, 10, 31),
            date(2026, 11, 2),
        )
        # The Agenzia's own example: the period 1 July 2025 - 30 June 2026 files by 30 April 2027.
        broken_year = return_filing_date(IT_COMPANY_PACK, form, date(2026, 6, 30))
        assert broken_year is not None
        assert broken_year.nominal_date == date(2027, 4, 30)
        assert broken_year.due_date == date(2027, 4, 30)
        # A period closing early in the year files in that same year.
        early = return_filing_date(IT_COMPANY_PACK, form, date(2026, 1, 31))
        assert early is not None and early.nominal_date == date(2026, 11, 30)


def test_a_form_the_regime_does_not_file_and_a_year_with_no_rule_have_no_date() -> None:
    assert return_filing_date(IT_STANDARD_PACK, "redditi_sc", date(2025, 12, 31)) is None
    assert return_filing_date(IT_STANDARD_PACK, "irap", date(2025, 12, 31)) is None
    assert return_filing_date(IT_COMPANY_PACK, "redditi_pf", date(2025, 12, 31)) is None
    assert return_filing_date(NON_IT_PACK, "redditi_pf", date(2025, 12, 31)) is None
    # The filing of tax year 2024 fell in 2025, before the entries start.
    assert return_filing_date(IT_STANDARD_PACK, "redditi_pf", date(2024, 12, 31)) is None


def test_a_filing_rule_has_a_fixed_day_or_a_number_of_months() -> None:
    with pytest.raises(ValueError, match="fixed day or a number of months"):
        ReturnFilingRule(form="redditi_pf", covers="x", source="y")


# --- the year a withholding suffered is credited ----------------------------------------


def test_a_withholding_is_credited_to_the_year_of_the_income_with_art_22_exceptions() -> None:
    credit = _rules(IT_STANDARD_PACK).withholding_credit
    assert credit is not None
    # Operated in the year of the income: that year, whatever the return.
    assert credit.credit_years(2026, date(2026, 12, 20), None) == (2026,)
    # Operated the year after, before the return of the income's year is filed: either year.
    assert credit.credit_years(2025, date(2026, 3, 10), None) == (2025, 2026)
    assert credit.credit_years(2025, date(2026, 3, 10), date(2026, 10, 30)) == (2025, 2026)
    # Operated after the return was filed: the year it was operated.
    assert credit.credit_years(2025, date(2026, 11, 20), date(2026, 10, 30)) == (2026,)
    # Operated two years later with the return not yet filed: neither exception, so the main rule,
    # the income's own year; once the return is filed, the year it was operated.
    assert credit.credit_years(2024, date(2026, 3, 10), None) == (2024,)
    assert credit.credit_years(2024, date(2026, 3, 10), date(2025, 10, 30)) == (2026,)


def test_only_a_person_carries_the_withholding_credit_rule() -> None:
    assert _rules(IT_FLAT_RATE_PACK).withholding_credit is None  # not withheld, L. 190/2014
    company = _rules(IT_COMPANY_PACK).withholding_credit
    assert company is not None and company.income_basis == "accrual"  # art. 79 c. 1, art. 109
    assert _rules(IT_STANDARD_PACK).withholding_credit.income_basis == "cash"  # type: ignore[union-attr]
