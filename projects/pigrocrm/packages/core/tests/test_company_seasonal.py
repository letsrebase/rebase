"""REB-1090: the seasonal 70% of art. 11 c. 4-octies D.Lgs. 446/1997 as a pure rule over a
worker's contract history. Each case is a sentence of Circolare 20/E of 18 May 2016, § 2 (read
2026-11-12). Pure: no database."""

import datetime as dt

from pigrocrm.core.fiscal.basis_schemas import BasisSeasonalContract
from pigrocrm.core.fiscal.company_seasonal import seasonal_outcome


def _contract(start: dt.date, end: dt.date | None, days: dict[int, int]) -> BasisSeasonalContract:
    return BasisSeasonalContract(start_date=start, end_date=end, worked_days=days)


def _outcome(contracts: list[BasisSeasonalContract] | None, year: int = 2026):  # type: ignore[no-untyped-def]
    return seasonal_outcome(contracts, year, min_days=120, window_years=2)


def test_no_history_is_a_missing_fact() -> None:
    assert _outcome(None).outcome == "unknown"
    assert _outcome([]).outcome == "unknown"


def test_the_first_contract_earns_nothing() -> None:
    first = _contract(dt.date(2026, 6, 1), dt.date(2026, 9, 30), {2026: 100})
    result = _outcome([first])
    assert result.outcome == "not_due" and "secondo" in result.reason


def test_the_second_contract_is_due_when_the_two_periods_reach_120_days() -> None:
    earlier = _contract(dt.date(2025, 6, 1), dt.date(2025, 9, 30), {2025: 80})
    later = _contract(dt.date(2026, 6, 1), dt.date(2026, 9, 30), {2026: 60})
    result = _outcome([later, earlier])
    assert (result.outcome, result.days) == ("due", 60)
    # The same history for 2025: the first contract's year, nothing due.
    assert _outcome([earlier, later], 2025).outcome == "not_due"


def test_the_days_of_two_periods_below_120_are_not_enough() -> None:
    earlier = _contract(dt.date(2025, 6, 1), dt.date(2025, 9, 30), {2025: 80})
    later = _contract(dt.date(2026, 6, 1), dt.date(2026, 9, 30), {2026: 30})
    result = _outcome([earlier, later])
    assert result.outcome == "not_due" and "110" in result.reason


def test_a_contract_more_than_two_years_after_the_previous_one_ended_starts_a_new_chain() -> None:
    earlier = _contract(dt.date(2023, 6, 1), dt.date(2023, 9, 30), {2023: 100})
    later = _contract(dt.date(2026, 6, 1), dt.date(2026, 9, 30), {2026: 100})
    assert _outcome([earlier, later]).outcome == "not_due"
    # Two years to the day after the end still chains.
    edge = _contract(dt.date(2025, 9, 30), dt.date(2026, 9, 30), {2025: 1, 2026: 150})
    assert _outcome([earlier, edge]).outcome == "due"


def test_a_third_contract_counts_the_best_earlier_period_and_its_own_days() -> None:
    first = _contract(dt.date(2024, 6, 1), dt.date(2024, 9, 30), {2024: 70})
    second = _contract(dt.date(2025, 6, 1), dt.date(2025, 9, 30), {2025: 30})
    third = _contract(dt.date(2026, 6, 1), dt.date(2026, 9, 30), {2026: 60})
    result = _outcome([first, second, third])
    assert (result.outcome, result.days) == ("due", 60)
    # With only 20 days earlier in the best period the 120 is not reached.
    short = _contract(dt.date(2024, 6, 1), dt.date(2024, 9, 30), {2024: 20})
    assert _outcome([short, second, third]).outcome == "not_due"


def test_two_contracts_in_the_same_tax_year_are_one_period() -> None:
    first = _contract(dt.date(2026, 3, 1), dt.date(2026, 4, 30), {2026: 50})
    second = _contract(dt.date(2026, 7, 1), dt.date(2026, 9, 30), {2026: 80})
    result = _outcome([first, second])
    assert result.outcome == "not_due" and "altro periodo" in result.reason


def test_a_year_the_history_does_not_state_is_missing_never_zero() -> None:
    spanning = _contract(dt.date(2025, 11, 1), dt.date(2026, 2, 28), {2025: 60})
    later = _contract(dt.date(2026, 6, 1), dt.date(2026, 9, 30), {2026: 100})
    result = _outcome([spanning, later])
    assert result.outcome == "unknown" and "2026" in result.reason


def test_a_year_with_no_contract_is_not_due() -> None:
    earlier = _contract(dt.date(2024, 6, 1), dt.date(2024, 9, 30), {2024: 80})
    later = _contract(dt.date(2025, 6, 1), dt.date(2025, 9, 30), {2025: 80})
    assert _outcome([earlier, later], 2026).outcome == "not_due"
