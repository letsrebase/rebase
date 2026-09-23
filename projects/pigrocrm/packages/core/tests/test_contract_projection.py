"""REB-375's own "what the implementing issue must test": the recurring-fee
occurrence schedule, the irrevocability window, and the renewal-assumption proration
that together produce the genuine "projected" figure the Done-when criterion names --
distinct from PigroCRM's existing draft-based `CashOverview.proiettato` (never
imported here, and never touched by `contracts/projection.py`).

Every function under test is pure (no session, no clock), so every object below is a
plain, unpersisted model instance -- constructing one costs nothing and needs no
`db_session` fixture.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from pigrocrm.core.contracts.models import Contract, RateCard, RenewalAssumption
from pigrocrm.core.contracts.projection import (
    RecurringFeeOccurrence,
    generate_recurring_fee_occurrences,
    irrevocability_window_end,
    project,
    renewal_assumption_contribution,
    schedule_end,
)
from pigrocrm.core.money import ZERO_MONEY


def _contract(**overrides: object) -> Contract:
    payload: dict[str, object] = {
        "id": uuid4(),
        "customer_id": uuid4(),
        "titolo": "Consulenza CTO",
        "inizio": date(2026, 1, 1),
        "fine": None,
        "tipo_rinnovo": "nessuno",
        "preavviso_disdetta_giorni": 30,
        "cadenza_fatturazione": "mensile",
        "politica_spese": {"tipo": "non_rimborsabile"},
    }
    payload.update(overrides)
    return Contract(**payload)  # type: ignore[arg-type]


def _rate_card(**overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "valido_da": date(2026, 1, 1),
        "valido_a": None,
        "tipo": "ricorrente_fisso",
        "importo": Decimal("1000.00"),
        "unita": "mese",
        "periodo_erogazione": "mensile",
    }
    payload.update(overrides)
    return RateCard(**payload)  # type: ignore[arg-type]


def _assumption(**overrides: object) -> RenewalAssumption:
    payload: dict[str, object] = {
        "probabilita": 50,
        "volume_atteso": Decimal("36500.00"),
        "orizzonte_al": date(2027, 12, 31),
    }
    payload.update(overrides)
    return RenewalAssumption(**payload)  # type: ignore[arg-type]


# ---- generate_recurring_fee_occurrences --------------------------------------------


def test_monthly_cadence_generates_one_occurrence_per_month_through_the_horizon() -> None:
    card = _rate_card(periodo_erogazione="mensile", valido_da=date(2026, 1, 1))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2026, 4, 1))
    assert [o.data for o in occurrences] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
    ]
    assert all(o.importo == Decimal("1000.00") for o in occurrences)


def test_quarterly_cadence_steps_three_months() -> None:
    card = _rate_card(periodo_erogazione="trimestrale", valido_da=date(2026, 1, 1))
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert [o.data for o in occurrences] == [
        date(2026, 1, 1),
        date(2026, 4, 1),
        date(2026, 7, 1),
        date(2026, 10, 1),
    ]


def test_annual_cadence_steps_twelve_months_clamping_the_day_of_month() -> None:
    """31 January plus twelve months lands on 31 January the next year; the clamp
    only matters when the target month is shorter, exercised by the monthly test
    below via a day-31 start stepping into February."""
    card = _rate_card(periodo_erogazione="annuale", valido_da=date(2026, 1, 31))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2028, 1, 31))
    assert [o.data for o in occurrences] == [
        date(2026, 1, 31),
        date(2027, 1, 31),
        date(2028, 1, 31),
    ]


def test_a_day_31_start_stepping_monthly_clamps_into_february() -> None:
    card = _rate_card(periodo_erogazione="mensile", valido_da=date(2026, 1, 31))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2026, 4, 30))
    assert [o.data for o in occurrences] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 28),
        date(2026, 4, 28),
    ]


def test_una_tantum_disbursement_produces_exactly_one_occurrence() -> None:
    card = _rate_card(periodo_erogazione="una_tantum", valido_da=date(2026, 3, 15))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2028, 1, 1))
    assert [o.data for o in occurrences] == [date(2026, 3, 15)]


def test_una_tantum_starting_after_the_horizon_produces_nothing() -> None:
    card = _rate_card(periodo_erogazione="una_tantum", valido_da=date(2029, 1, 1))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2028, 1, 1))
    assert occurrences == []


def test_no_periodo_erogazione_recorded_contributes_nothing() -> None:
    """A recurring-fee card with no cadence recorded projects nothing -- never a
    guessed cadence standing in for a missing one."""
    card = _rate_card(periodo_erogazione=None)
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert occurrences == []


def test_a_non_recurring_card_type_is_never_expanded() -> None:
    card = _rate_card(tipo="orario", periodo_erogazione=None, unita="ora")
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert occurrences == []


def test_the_cards_own_valido_a_bounds_its_occurrences() -> None:
    card = _rate_card(valido_da=date(2026, 1, 1), valido_a=date(2026, 3, 1))
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert [o.data for o in occurrences] == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_the_contracts_own_fine_bounds_occurrences_even_for_an_open_ended_card() -> None:
    contract = _contract(fine=date(2026, 3, 1))
    card = _rate_card(valido_da=date(2026, 1, 1), valido_a=None)
    occurrences = generate_recurring_fee_occurrences(contract, [card], through=date(2026, 12, 31))
    assert [o.data for o in occurrences] == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_a_card_starting_after_the_horizon_contributes_nothing() -> None:
    card = _rate_card(valido_da=date(2027, 1, 1))
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert occurrences == []


# ---- irrevocability_window_end -----------------------------------------------------


def test_window_end_is_the_notice_period_from_today_for_an_open_ended_contract() -> None:
    contract = _contract(fine=None, preavviso_disdetta_giorni=30)
    assert irrevocability_window_end(contract, date(2026, 6, 1)) == date(2026, 7, 1)


def test_window_end_clips_to_a_fixed_terms_own_fine() -> None:
    contract = _contract(fine=date(2026, 6, 15), preavviso_disdetta_giorni=30)
    assert irrevocability_window_end(contract, date(2026, 6, 1)) == date(2026, 6, 15)


def test_window_end_is_none_once_the_contract_has_already_ended() -> None:
    contract = _contract(fine=date(2025, 12, 31), preavviso_disdetta_giorni=30)
    assert irrevocability_window_end(contract, date(2026, 6, 1)) is None


def test_a_contract_ending_exactly_today_is_not_yet_considered_over() -> None:
    contract = _contract(fine=date(2026, 6, 1), preavviso_disdetta_giorni=30)
    assert irrevocability_window_end(contract, date(2026, 6, 1)) == date(2026, 6, 1)


# ---- schedule_end -------------------------------------------------------------------


def test_schedule_end_is_the_fixed_terms_own_fine_regardless_of_occurrences() -> None:
    contract = _contract(fine=date(2026, 12, 31))
    occurrences = [RecurringFeeOccurrence(data=date(2026, 3, 1), importo=Decimal("1.00"))]
    assert schedule_end(contract, date(2026, 7, 1), occurrences) == date(2026, 12, 31)


def test_schedule_end_for_an_open_ended_contract_is_its_last_occurrence() -> None:
    contract = _contract(fine=None)
    occurrences = [
        RecurringFeeOccurrence(data=date(2026, 3, 1), importo=Decimal("1.00")),
        RecurringFeeOccurrence(data=date(2026, 9, 1), importo=Decimal("1.00")),
    ]
    assert schedule_end(contract, date(2026, 1, 1), occurrences) == date(2026, 9, 1)


def test_schedule_end_for_an_open_ended_contract_with_no_occurrences_is_the_window_end() -> None:
    contract = _contract(fine=None)
    assert schedule_end(contract, date(2026, 7, 1), []) == date(2026, 7, 1)


# ---- renewal_assumption_contribution ------------------------------------------------

_FIXED_TERM = _contract(fine=date(2026, 12, 31), preavviso_disdetta_giorni=30)


def test_no_assumption_contributes_nothing() -> None:
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, None, [], date(2026, 6, 1), date(2027, 1, 1), date(2028, 1, 1)
    )
    assert contribution == ZERO_MONEY


def test_an_already_ended_contract_contributes_nothing_even_with_an_assumption() -> None:
    ended = _contract(fine=date(2025, 12, 31), preavviso_disdetta_giorni=30)
    assumption = _assumption()
    contribution = renewal_assumption_contribution(
        ended, assumption, [], date(2026, 6, 1), date(2026, 1, 1), date(2027, 1, 1)
    )
    assert contribution == ZERO_MONEY


def test_an_already_elapsed_horizon_contributes_nothing() -> None:
    assumption = _assumption(orizzonte_al=date(2026, 12, 31))  # before schedule_end + 1 day
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, assumption, [], date(2026, 6, 1), date(2026, 1, 1), date(2028, 1, 1)
    )
    assert contribution == ZERO_MONEY


def test_a_query_window_entirely_before_the_assumption_span_contributes_nothing() -> None:
    assumption = _assumption(orizzonte_al=date(2027, 12, 31))
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, assumption, [], date(2026, 6, 1), date(2026, 1, 1), date(2027, 1, 1)
    )
    assert contribution == ZERO_MONEY


def test_full_overlap_returns_the_whole_probability_weighted_volume() -> None:
    """schedule_end (2026-12-31) + 1 day = 2027-01-01; horizon 2027-12-31 -> 365 days
    at 50% of 36500.00 = 18250.00, spread evenly is 50.00/day, and the query window
    covers the whole span."""
    assumption = _assumption(probabilita=50, volume_atteso=Decimal("36500.00"))
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, assumption, [], date(2026, 6, 1), date(2027, 1, 1), date(2028, 1, 1)
    )
    assert contribution == Decimal("18250.00")


def test_a_partial_overlap_is_prorated_by_the_exact_day_count() -> None:
    """The first half of the same span (2027-01-01..2027-07-01, 181 days) at
    50.00/day is 9050.00."""
    assumption = _assumption(probabilita=50, volume_atteso=Decimal("36500.00"))
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, assumption, [], date(2026, 6, 1), date(2027, 1, 1), date(2027, 7, 1)
    )
    assert contribution == Decimal("9050.00")


def test_a_query_window_ending_exactly_when_the_assumption_starts_contributes_nothing() -> None:
    """Half-open [da, a): a window that stops the day the assumption's own span
    begins never touches it."""
    assumption = _assumption()
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, assumption, [], date(2026, 6, 1), date(2026, 1, 1), date(2027, 1, 1)
    )
    assert contribution == ZERO_MONEY


# ---- project (the composed figure) --------------------------------------------------


def test_project_combines_the_schedule_and_the_renewal_assumption() -> None:
    """The Done-when criterion itself: a contract's own recurring-fee schedule and
    renewal assumption produce one genuine "projected" figure. Every number here
    divides evenly so the assertion is exact."""
    contract = _contract(fine=date(2026, 12, 31), preavviso_disdetta_giorni=30)
    card = _rate_card(valido_da=date(2026, 1, 1), periodo_erogazione="mensile")
    assumption = _assumption(probabilita=50, volume_atteso=Decimal("36500.00"))

    result = project(
        contract,
        [card],
        assumption,
        come_di=date(2026, 6, 1),
        da=date(2026, 1, 1),
        a=date(2027, 6, 1),
    )

    assert result.contract_id == contract.id
    assert result.finestra_irrevocabilita_fino_al == date(2026, 7, 1)
    # 2026-08-01 through 2026-12-01: five monthly occurrences beyond the window and
    # within the contract's own term.
    assert result.programmato == Decimal("5000.00")
    # 2027-01-01..2027-06-01 (151 days) of the assumption's own 2027-01-01..2027-12-31
    # span, at 50.00/day.
    assert result.da_rinnovo == Decimal("7550.00")
    assert result.totale == Decimal("12550.00")


def test_project_with_no_renewal_assumption_is_the_schedule_alone() -> None:
    contract = _contract(fine=date(2026, 12, 31), preavviso_disdetta_giorni=30)
    card = _rate_card(valido_da=date(2026, 1, 1), periodo_erogazione="mensile")

    result = project(
        contract, [card], None, come_di=date(2026, 6, 1), da=date(2026, 1, 1), a=date(2027, 6, 1)
    )

    assert result.programmato == Decimal("5000.00")
    assert result.da_rinnovo == ZERO_MONEY
    assert result.totale == Decimal("5000.00")


def test_project_with_no_rate_cards_and_no_assumption_is_zero() -> None:
    contract = _contract(fine=date(2026, 12, 31), preavviso_disdetta_giorni=30)
    result = project(
        contract, [], None, come_di=date(2026, 6, 1), da=date(2026, 1, 1), a=date(2027, 6, 1)
    )
    assert result.programmato == ZERO_MONEY
    assert result.da_rinnovo == ZERO_MONEY
    assert result.totale == ZERO_MONEY
