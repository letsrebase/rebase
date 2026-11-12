"""REB-1074: what the packs say an income tax owes in advance and when (`fiscal/advances.py`).

Pure lookups, no session: the thresholds, the split and the single-payment rule of a person, of the
flat-rate regime and of an srl, the company dates that follow the fiscal year end, and the refusal
to guess when a parameter or a date is missing. The figures are the verified rows of the frame's
A5 register (`fiscal/law.py`, `law_flat_rate.py`, `pack.py` carry each source).
"""

from datetime import date
from decimal import Decimal

import pytest

from pigrocrm.core.fiscal.advances import (
    AdvanceDates,
    AdvanceRule,
    Missing,
    PaymentDates,
    advance_amounts,
    advance_dates,
    advance_rule,
    balance_date,
    company_dates,
    fiscal_year_end,
)
from pigrocrm.core.fiscal.calendar import tax_calendar
from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK, IT_FLAT_RATE_PACK, IT_STANDARD_PACK

D = Decimal


def _rule(pack: object, year: int, tax: str, isa: bool | None) -> AdvanceRule:
    law = pack.law_for(year)  # type: ignore[attr-defined]
    rule = advance_rule(law, tax, isa)  # type: ignore[arg-type]
    assert isinstance(rule, AdvanceRule), rule
    return rule


def _split(rule: AdvanceRule, base: str) -> tuple[str, str, str, bool]:
    amounts = advance_amounts(rule, D(base))
    return (str(amounts.total), str(amounts.first), str(amounts.second), amounts.single)


# --- a person (IRPEF): 51.65, 257.52, 40/60 and 50/50 under ISA -------------------------------


def test_a_person_owes_no_advance_at_or_below_51_65() -> None:
    rule = _rule(IT_STANDARD_PACK, 2026, "irpef", False)
    assert _split(rule, "51.65") == ("0.00", "0.00", "0.00", False)
    assert _split(rule, "0") == ("0.00", "0.00", "0.00", False)
    assert _split(rule, "51.66") == ("51.66", "0.00", "51.66", True)


def test_a_person_pays_once_in_november_below_257_52_and_in_40_and_60_from_it() -> None:
    rule = _rule(IT_STANDARD_PACK, 2026, "irpef", False)
    assert _split(rule, "257.51") == ("257.51", "0.00", "257.51", True)
    # 40% of 257.52 is 103.008: the second instalment takes the cent the first rounded away.
    assert _split(rule, "257.52") == ("257.52", "103.01", "154.51", False)
    assert _split(rule, "1000") == ("1000.00", "400.00", "600.00", False)


def test_under_isa_the_split_is_50_and_50_and_a_total_up_to_206_is_paid_once() -> None:
    rule = _rule(IT_STANDARD_PACK, 2026, "irpef", True)
    assert _split(rule, "1000") == ("1000.00", "500.00", "500.00", False)
    assert _split(rule, "206") == ("206.00", "0.00", "206.00", True)
    assert _split(rule, "206.01") == ("206.01", "103.01", "103.00", False)
    assert _split(rule, "51.65") == ("0.00", "0.00", "0.00", False)


# --- the flat-rate regime: 50/50, 51.65 and 206 ---------------------------------------------


def test_the_substitute_tax_is_50_and_50_whatever_the_isa_flag_says() -> None:
    for isa in (None, True, False):
        rule = _rule(IT_FLAT_RATE_PACK, 2026, "substitute_tax", isa)
        assert _split(rule, "1000") == ("1000.00", "500.00", "500.00", False)
    rule = _rule(IT_FLAT_RATE_PACK, 2026, "substitute_tax", None)
    assert _split(rule, "51.65") == ("0.00", "0.00", "0.00", False)
    assert _split(rule, "51.66") == ("51.66", "0.00", "51.66", True)
    assert _split(rule, "206") == ("206.00", "0.00", "206.00", True)
    assert _split(rule, "206.01") == ("206.01", "103.01", "103.00", False)


def test_the_flat_rate_regime_has_no_advance_rule_before_2026() -> None:
    law = IT_FLAT_RATE_PACK.law_for(2025)
    assert law is not None
    missing = advance_rule(law, "substitute_tax", None)
    assert isinstance(missing, Missing) and "no advance rule" in missing.reason


# --- an srl: 20.66, 103, 40/60 and 50/50 under ISA --------------------------------------------


def test_a_company_owes_no_ires_or_irap_advance_when_the_tax_does_not_exceed_20_66() -> None:
    for tax in ("ires", "irap"):
        rule = _rule(IT_COMPANY_PACK, 2026, tax, False)
        assert _split(rule, "20.66")[0] == "0.00", tax
        assert _split(rule, "20.67")[0] == "20.67", tax


def test_an_ires_advance_is_paid_once_when_its_first_instalment_is_not_above_103() -> None:
    """Art. 17 c. 3 D.P.R. 435/2001: the 103 euro is a ceiling on the first instalment (40%),
    not on the whole advance, so an advance of 200 (first instalment 80) is one payment."""
    rule = _rule(IT_COMPANY_PACK, 2026, "ires", False)
    assert _split(rule, "200") == ("200.00", "0.00", "200.00", True)
    assert _split(rule, "257.51") == ("257.51", "0.00", "257.51", True)  # first: 103.00
    assert _split(rule, "257.52") == ("257.52", "103.01", "154.51", False)
    assert _split(rule, "10000") == ("10000.00", "4000.00", "6000.00", False)
    isa = _rule(IT_COMPANY_PACK, 2026, "ires", True)  # 50/50: the ceiling is an advance of 206
    assert _split(isa, "206") == ("206.00", "0.00", "206.00", True)
    assert _split(isa, "206.02") == ("206.02", "103.01", "103.01", False)
    assert _split(isa, "10000") == ("10000.00", "5000.00", "5000.00", False)


def test_an_irap_advance_follows_the_same_first_instalment_test() -> None:
    law = IT_COMPANY_PACK.law_for(2026)
    assert law is not None and law.company_advances is not None
    assert law.company_advances.first_instalment_single_payment_at_or_below == Decimal("103")
    rule = _rule(IT_COMPANY_PACK, 2026, "irap", False)
    assert _split(rule, "50") == ("50.00", "0.00", "50.00", True)
    assert _split(rule, "257.51") == ("257.51", "0.00", "257.51", True)
    assert _split(rule, "300") == ("300.00", "120.00", "180.00", False)


def test_a_person_and_a_company_with_no_isa_flag_have_no_rule_and_say_why() -> None:
    for pack, tax in ((IT_STANDARD_PACK, "irpef"), (IT_COMPANY_PACK, "ires")):
        missing = advance_rule(pack.law_for(2026), tax, None)  # type: ignore[arg-type]
        assert isinstance(missing, Missing) and "ISA" in missing.reason


def test_a_pack_that_owes_no_such_tax_has_no_rule() -> None:
    assert isinstance(advance_rule(IT_STANDARD_PACK.law_for(2026), "ires", True), Missing)
    assert isinstance(advance_rule(IT_COMPANY_PACK.law_for(2026), "irpef", True), Missing)
    assert isinstance(advance_rule(None, "irpef", True), Missing)


# --- the dates --------------------------------------------------------------------------------


def test_a_calendar_year_company_has_the_calendars_dates() -> None:
    law = IT_COMPANY_PACK.law_for(2026)
    dates = company_dates(law, 2026, 12, 31)
    assert dates == PaymentDates(
        balance=date(2027, 6, 30),
        first_advance=date(2026, 6, 30),
        second_advance=date(2026, 11, 30),
    )
    calendar = {
        (entry.tax, entry.kind): entry.due_date
        for entry in tax_calendar(IT_COMPANY_PACK, 2026) or []
    }
    assert calendar[("ires", "balance_and_first_advance")] == dates.first_advance  # type: ignore[union-attr]
    assert calendar[("ires", "second_advance")] == dates.second_advance  # type: ignore[union-attr]
    assert calendar[("irap", "balance_and_first_advance")] == dates.first_advance  # type: ignore[union-attr]
    # The balance of the year before falls on the calendar's 30 June of this one.
    before = company_dates(law, 2025, 12, 31)
    assert isinstance(before, PaymentDates) and before.balance == date(2026, 6, 30)


def test_a_company_whose_year_ends_on_30_june_pays_on_31_december_and_31_may() -> None:
    law = IT_COMPANY_PACK.law_for(2026)
    # The tax year starting on 1 July 2026 ends on 30 June 2027: its balance falls on the last day
    # of the sixth month after that, its first advance on the balance's day of the year before
    # and its second on the last day of the eleventh month of the period (A5 row «Company
    # dates»).
    assert fiscal_year_end(2026, 6, 30) == date(2027, 6, 30)
    assert company_dates(law, 2026, 6, 30) == PaymentDates(
        balance=date(2027, 12, 31),
        first_advance=date(2026, 12, 31),
        second_advance=date(2027, 5, 31),
    )


def test_a_year_end_on_a_weekend_moves_to_the_next_working_day() -> None:
    law = IT_COMPANY_PACK.law_for(2026)
    # A year ending 31 March: the first advance of the tax year is due on 30 September 2026 (a
    # Wednesday); the second advance, the last day of February 2027, is a Sunday and moves to
    # Monday 1 March.
    dates = company_dates(law, 2026, 3, 31)
    assert isinstance(dates, PaymentDates)
    assert dates.second_advance == date(2027, 3, 1)
    assert dates.first_advance == date(2026, 9, 30)


def test_a_year_end_that_is_not_a_month_end_has_no_dates_and_says_why() -> None:
    missing = company_dates(IT_COMPANY_PACK.law_for(2026), 2026, 2, 15)
    assert isinstance(missing, Missing) and "last day of a month" in missing.reason


def test_a_company_has_no_dates_when_the_pack_has_no_company_law_for_the_year() -> None:
    assert isinstance(company_dates(IT_COMPANY_PACK.law_for(2025), 2025, 12, 31), Missing)
    assert isinstance(company_dates(IT_STANDARD_PACK.law_for(2026), 2026, 12, 31), Missing)


def test_a_persons_and_the_flat_rate_regimes_dates_are_the_packs_calendar() -> None:
    law = IT_STANDARD_PACK.law_for(2026)
    person = advance_dates(IT_STANDARD_PACK, law, "irpef", 2026, 12, 31)
    assert person == AdvanceDates(date(2026, 6, 30), date(2026, 11, 30))
    assert balance_date(IT_STANDARD_PACK, law, "irpef", 2026, 12, 31) == date(2027, 6, 30)
    flat_law = IT_FLAT_RATE_PACK.law_for(2026)
    assert advance_dates(IT_FLAT_RATE_PACK, flat_law, "substitute_tax", 2026, 12, 31) == person
    assert balance_date(IT_FLAT_RATE_PACK, flat_law, "substitute_tax", 2026, 12, 31) == date(
        2027, 6, 30
    )


def test_a_year_the_calendar_has_no_entry_for_has_no_dates_never_another_years() -> None:
    early = advance_dates(IT_STANDARD_PACK, None, "irpef", 2024, 12, 31)
    assert isinstance(early, Missing) and "no dates for 2024" in early.reason
    # The balance of 2025 is paid in 2026, which the calendar covers, whatever it says of 2025.
    assert balance_date(IT_STANDARD_PACK, None, "irpef", 2025, 12, 31) == date(2026, 6, 30)


@pytest.mark.parametrize("year", [2026, 2027, 2030])
def test_every_company_advance_figure_names_its_source_in_the_law_text(year: int) -> None:
    law = IT_COMPANY_PACK.law_for(year)
    assert law is not None and law.company_advances is not None
    assert law.company_advances.__class__.__doc__ is not None
    assert "Agenzia delle Entrate" in law.company_advances.__class__.__doc__
    assert "art. 58 D.L. 124/2019" in law.company_advances.__class__.__doc__
    assert (
        law.company_advances.balance_months_after_year_end,
        law.company_advances.second_advance_month_of_year,
    ) == (6, 11)
