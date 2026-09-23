"""Pure date arithmetic read off a contract's own columns (REB-352 §1.5, §1.6): no
database, no session -- every case here is a fact about `date` objects. Database-backed
coverage of the same functions, wired through a real service call and a real
`cash_overview`, lives in `test_contracts_service.py` and `test_analytics_cash.py`.
"""

from datetime import date

import pytest

from pigrocrm.core.contracts.dates import (
    anniversary_year_bounds,
    irrevocability_window_end,
    renewal_deadline,
)
from pigrocrm.core.contracts.models import Contract


def _contract(**overrides: object) -> Contract:
    payload: dict[str, object] = {
        "titolo": "Consulenza CTO",
        "inizio": date(2026, 1, 1),
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "preavviso_rinnovo_giorni": None,
        "fine": None,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"tipo": "non_rimborsabile"},
    }
    payload.update(overrides)
    return Contract(**payload)  # type: ignore[arg-type]


# --- irrevocability_window_end ------------------------------------------------------


def test_irrevocability_window_runs_the_notice_period_from_as_of() -> None:
    contract = _contract(preavviso_disdetta_giorni=30, fine=None)
    assert irrevocability_window_end(contract, date(2026, 6, 1)) == date(2026, 7, 1)


def test_irrevocability_window_clips_to_the_contracts_own_end() -> None:
    contract = _contract(preavviso_disdetta_giorni=90, fine=date(2026, 6, 15))
    assert irrevocability_window_end(contract, date(2026, 6, 1)) == date(2026, 6, 15)


def test_irrevocability_window_is_none_once_the_contract_has_already_ended() -> None:
    contract = _contract(preavviso_disdetta_giorni=30, fine=date(2026, 5, 1))
    assert irrevocability_window_end(contract, date(2026, 6, 1)) is None


def test_irrevocability_window_still_guarantees_the_contracts_own_last_day() -> None:
    """A contract ending exactly on `as_of` is not "already ended" -- the boundary
    itself still counts."""
    contract = _contract(preavviso_disdetta_giorni=30, fine=date(2026, 6, 1))
    assert irrevocability_window_end(contract, date(2026, 6, 1)) == date(2026, 6, 1)


# --- renewal_deadline ----------------------------------------------------------------


def test_renewal_deadline_counts_back_from_the_contracts_own_end() -> None:
    contract = _contract(
        tipo_rinnovo="tacito", preavviso_rinnovo_giorni=60, fine=date(2026, 12, 31)
    )
    assert renewal_deadline(contract) == date(2026, 11, 1)


def test_renewal_deadline_is_none_with_no_renewal_clause() -> None:
    contract = _contract(
        tipo_rinnovo="nessuno", preavviso_rinnovo_giorni=None, fine=date(2026, 12, 31)
    )
    assert renewal_deadline(contract) is None


def test_renewal_deadline_is_none_with_no_known_end_date() -> None:
    contract = _contract(tipo_rinnovo="tacito", preavviso_rinnovo_giorni=60, fine=None)
    assert renewal_deadline(contract) is None


# --- anniversary_year_bounds ---------------------------------------------------------


def test_anniversary_year_bounds_start_on_the_contracts_own_first_day() -> None:
    assert anniversary_year_bounds(date(2026, 3, 10), date(2026, 3, 10)) == (
        date(2026, 3, 10),
        date(2027, 3, 9),
    )


def test_anniversary_year_bounds_stay_in_the_window_the_day_before_the_next_anniversary() -> None:
    assert anniversary_year_bounds(date(2025, 3, 10), date(2026, 3, 9)) == (
        date(2025, 3, 10),
        date(2026, 3, 9),
    )


def test_anniversary_year_bounds_roll_over_on_the_next_anniversary_itself() -> None:
    assert anniversary_year_bounds(date(2025, 3, 10), date(2026, 3, 10)) == (
        date(2026, 3, 10),
        date(2027, 3, 9),
    )


def test_anniversary_year_bounds_handle_a_29_february_anchor_in_a_non_leap_year() -> None:
    """Neither 2026 nor 2027 is a leap year: the anchor falls back to 28 February on
    both sides of the window rather than raising."""
    da, a = anniversary_year_bounds(date(2024, 2, 29), date(2026, 3, 1))
    assert (da, a) == (date(2026, 2, 28), date(2027, 2, 27))


def test_anniversary_year_bounds_reject_a_reference_date_before_the_contract_started() -> None:
    with pytest.raises(ValueError):
        anniversary_year_bounds(date(2026, 3, 10), date(2026, 1, 1))
