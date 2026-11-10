"""REB-375's own "what the implementing issue must test": the recurring-fee
occurrence schedule, the irrevocability window, and the renewal-assumption proration
that together produce the genuine "projected" figure the Done-when criterion names --
distinct from PigroCRM's existing draft-based `CashOverview.projected` (never
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
    project,
    renewal_assumption_contribution,
    schedule_end,
)
from pigrocrm.core.money import ZERO_MONEY


def _contract(**overrides: object) -> Contract:
    payload: dict[str, object] = {
        "id": uuid4(),
        "customer_id": uuid4(),
        "title": "Consulenza CTO",
        "start": date(2026, 1, 1),
        "end": None,
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
    }
    payload.update(overrides)
    return Contract(**payload)  # type: ignore[arg-type]


def _rate_card(**overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "valid_from": date(2026, 1, 1),
        "valid_to": None,
        "type": "fixed_recurring",
        "amount": Decimal("1000.00"),
        "unit": "month",
        "delivery_period": "monthly",
    }
    payload.update(overrides)
    return RateCard(**payload)  # type: ignore[arg-type]


def _assumption(**overrides: object) -> RenewalAssumption:
    payload: dict[str, object] = {
        "probability": 50,
        "expected_volume": Decimal("36500.00"),
        "horizon_until": date(2027, 12, 31),
    }
    payload.update(overrides)
    return RenewalAssumption(**payload)  # type: ignore[arg-type]


# ---- generate_recurring_fee_occurrences --------------------------------------------


def test_monthly_cadence_generates_one_occurrence_per_month_through_the_horizon() -> None:
    card = _rate_card(delivery_period="monthly", valid_from=date(2026, 1, 1))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2026, 4, 1))
    assert [o.date for o in occurrences] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
    ]
    assert all(o.amount == Decimal("1000.00") for o in occurrences)


def test_quarterly_cadence_steps_three_months() -> None:
    card = _rate_card(delivery_period="quarterly", valid_from=date(2026, 1, 1))
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert [o.date for o in occurrences] == [
        date(2026, 1, 1),
        date(2026, 4, 1),
        date(2026, 7, 1),
        date(2026, 10, 1),
    ]


def test_annual_cadence_steps_twelve_months_clamping_the_day_of_month() -> None:
    """31 January plus twelve months lands on 31 January the next year; the clamp
    only matters when the target month is shorter, exercised by the monthly test
    below via a day-31 start stepping into February."""
    card = _rate_card(delivery_period="annual", valid_from=date(2026, 1, 31))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2028, 1, 31))
    assert [o.date for o in occurrences] == [
        date(2026, 1, 31),
        date(2027, 1, 31),
        date(2028, 1, 31),
    ]


def test_a_day_31_start_clamps_into_a_short_month_and_returns_to_the_31st() -> None:
    card = _rate_card(delivery_period="monthly", valid_from=date(2026, 1, 31))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2026, 4, 30))
    assert [o.date for o in occurrences] == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
    ]


def test_one_off_disbursement_produces_exactly_one_occurrence() -> None:
    card = _rate_card(delivery_period="one_off", valid_from=date(2026, 3, 15))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2028, 1, 1))
    assert [o.date for o in occurrences] == [date(2026, 3, 15)]


def test_one_off_starting_after_the_horizon_produces_nothing() -> None:
    card = _rate_card(delivery_period="one_off", valid_from=date(2029, 1, 1))
    occurrences = generate_recurring_fee_occurrences(_contract(), [card], through=date(2028, 1, 1))
    assert occurrences == []


def test_no_delivery_period_recorded_contributes_nothing() -> None:
    """A recurring-fee card with no cadence recorded projects nothing -- never a
    guessed cadence standing in for a missing one."""
    card = _rate_card(delivery_period=None)
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert occurrences == []


def test_a_non_recurring_card_type_is_never_expanded() -> None:
    card = _rate_card(type="hourly", delivery_period=None, unit="ora")
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert occurrences == []


def test_the_cards_own_valid_to_bounds_its_occurrences() -> None:
    card = _rate_card(valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 1))
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert [o.date for o in occurrences] == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_the_contracts_own_fine_bounds_occurrences_even_for_an_open_ended_card() -> None:
    contract = _contract(end=date(2026, 3, 1))
    card = _rate_card(valid_from=date(2026, 1, 1), valid_to=None)
    occurrences = generate_recurring_fee_occurrences(contract, [card], through=date(2026, 12, 31))
    assert [o.date for o in occurrences] == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_a_card_starting_after_the_horizon_contributes_nothing() -> None:
    card = _rate_card(valid_from=date(2027, 1, 1))
    occurrences = generate_recurring_fee_occurrences(
        _contract(), [card], through=date(2026, 12, 31)
    )
    assert occurrences == []


# ---- schedule_end -------------------------------------------------------------------


def test_schedule_end_is_the_fixed_terms_own_fine_regardless_of_occurrences() -> None:
    contract = _contract(end=date(2026, 12, 31))
    occurrences = [RecurringFeeOccurrence(date=date(2026, 3, 1), amount=Decimal("1.00"))]
    assert schedule_end(contract, date(2026, 7, 1), occurrences) == date(2026, 12, 31)


def test_schedule_end_for_an_open_ended_contract_is_its_last_occurrence() -> None:
    contract = _contract(end=None)
    occurrences = [
        RecurringFeeOccurrence(date=date(2026, 3, 1), amount=Decimal("1.00")),
        RecurringFeeOccurrence(date=date(2026, 9, 1), amount=Decimal("1.00")),
    ]
    assert schedule_end(contract, date(2026, 1, 1), occurrences) == date(2026, 9, 1)


def test_schedule_end_for_an_open_ended_contract_with_no_occurrences_is_the_window_end() -> None:
    contract = _contract(end=None)
    assert schedule_end(contract, date(2026, 7, 1), []) == date(2026, 7, 1)


# ---- renewal_assumption_contribution ------------------------------------------------

_FIXED_TERM = _contract(end=date(2026, 12, 31), termination_notice_days=30)


def test_no_assumption_contributes_nothing() -> None:
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, None, [], date(2026, 6, 1), date(2027, 1, 1), date(2028, 1, 1)
    )
    assert contribution == ZERO_MONEY


def test_an_already_ended_contract_contributes_nothing_even_with_an_assumption() -> None:
    ended = _contract(end=date(2025, 12, 31), termination_notice_days=30)
    assumption = _assumption()
    contribution = renewal_assumption_contribution(
        ended, assumption, [], date(2026, 6, 1), date(2026, 1, 1), date(2027, 1, 1)
    )
    assert contribution == ZERO_MONEY


def test_an_already_elapsed_horizon_contributes_nothing() -> None:
    assumption = _assumption(horizon_until=date(2026, 12, 31))  # before schedule_end + 1 day
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, assumption, [], date(2026, 6, 1), date(2026, 1, 1), date(2028, 1, 1)
    )
    assert contribution == ZERO_MONEY


def test_a_query_window_entirely_before_the_assumption_span_contributes_nothing() -> None:
    assumption = _assumption(horizon_until=date(2027, 12, 31))
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, assumption, [], date(2026, 6, 1), date(2026, 1, 1), date(2027, 1, 1)
    )
    assert contribution == ZERO_MONEY


def test_full_overlap_returns_the_whole_probability_weighted_volume() -> None:
    """schedule_end (2026-12-31) + 1 day = 2027-01-01; horizon 2027-12-31 -> 365 days
    at 50% of 36500.00 = 18250.00, spread evenly is 50.00/day, and the query window
    covers the whole span."""
    assumption = _assumption(probability=50, expected_volume=Decimal("36500.00"))
    contribution = renewal_assumption_contribution(
        _FIXED_TERM, assumption, [], date(2026, 6, 1), date(2027, 1, 1), date(2028, 1, 1)
    )
    assert contribution == Decimal("18250.00")


def test_a_partial_overlap_is_prorated_by_the_exact_day_count() -> None:
    """The first half of the same span (2027-01-01..2027-07-01, 181 days) at
    50.00/day is 9050.00."""
    assumption = _assumption(probability=50, expected_volume=Decimal("36500.00"))
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
    contract = _contract(end=date(2026, 12, 31), termination_notice_days=30)
    card = _rate_card(valid_from=date(2026, 1, 1), delivery_period="monthly")
    assumption = _assumption(probability=50, expected_volume=Decimal("36500.00"))

    result = project(
        contract,
        [card],
        assumption,
        as_of=date(2026, 6, 1),
        date_from=date(2026, 1, 1),
        date_to=date(2027, 6, 1),
    )

    assert result.contract_id == contract.id
    assert result.irrevocability_window_until_to == date(2026, 7, 1)
    # 2026-08-01 through 2026-12-01: five monthly occurrences beyond the window and
    # within the contract's own term.
    assert result.scheduled == Decimal("5000.00")
    # 2027-01-01..2027-06-01 (151 days) of the assumption's own 2027-01-01..2027-12-31
    # span, at 50.00/day.
    assert result.from_renewal == Decimal("7550.00")
    assert result.total == Decimal("12550.00")


def test_project_with_no_renewal_assumption_is_the_schedule_alone() -> None:
    contract = _contract(end=date(2026, 12, 31), termination_notice_days=30)
    card = _rate_card(valid_from=date(2026, 1, 1), delivery_period="monthly")

    result = project(
        contract,
        [card],
        None,
        as_of=date(2026, 6, 1),
        date_from=date(2026, 1, 1),
        date_to=date(2027, 6, 1),
    )

    assert result.scheduled == Decimal("5000.00")
    assert result.from_renewal == ZERO_MONEY
    assert result.total == Decimal("5000.00")


def test_project_with_no_rate_cards_and_no_assumption_is_zero() -> None:
    contract = _contract(end=date(2026, 12, 31), termination_notice_days=30)
    result = project(
        contract,
        [],
        None,
        as_of=date(2026, 6, 1),
        date_from=date(2026, 1, 1),
        date_to=date(2027, 6, 1),
    )
    assert result.scheduled == ZERO_MONEY
    assert result.from_renewal == ZERO_MONEY
    assert result.total == ZERO_MONEY


def test_an_open_ended_contracts_projection_does_not_depend_on_where_the_window_stops() -> None:
    """Two adjacent windows add up to the window that spans both: the schedule an
    open-ended contract's renewal assumption starts after is its own, not the last
    occurrence the query happened to ask for."""
    contract = _contract(end=None, termination_notice_days=30)
    card = _rate_card(valid_from=date(2026, 1, 1), delivery_period="monthly")
    assumption = _assumption(probability=50, expected_volume=Decimal("36500.00"))
    kwargs = {"as_of": date(2026, 6, 1)}

    whole = project(
        contract, [card], assumption, date_from=date(2026, 8, 1), date_to=date(2028, 1, 1), **kwargs
    )
    first = project(
        contract,
        [card],
        assumption,
        date_from=date(2026, 8, 1),
        date_to=date(2027, 3, 15),
        **kwargs,
    )
    second = project(
        contract,
        [card],
        assumption,
        date_from=date(2027, 3, 15),
        date_to=date(2028, 1, 1),
        **kwargs,
    )

    assert first.total + second.total == whole.total
