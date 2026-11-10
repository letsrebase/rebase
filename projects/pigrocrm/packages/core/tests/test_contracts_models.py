"""REB-358's own "what the implementing issues must test" for the foundations
issue: the renewal-notice CHECK, the payment-terms "together" CHECK, and the
non-overlapping-validity exclusion constraint -- refused by the database, not by a
service-level check.
"""

from datetime import date
from decimal import Decimal

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract, RateCard, RenewalAssumption
from pigrocrm.core.organizations.models import Organization


def _contract(db_session: Session, customer: Organization, **overrides: object) -> Contract:
    payload: dict[str, object] = {
        "customer_id": customer.id,
        "title": "Consulenza CTO",
        "start": date(2026, 1, 1),
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
    }
    payload.update(overrides)
    contract = Contract(**payload)  # type: ignore[arg-type]
    db_session.add(contract)
    db_session.flush()
    return contract


def test_a_contract_can_be_created_read_and_carries_its_defaults(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    assert contract.status == "draft"
    assert contract.currency == "EUR"
    assert contract.requires_prior_approval is False
    assert contract.applies_social_charge is False
    assert contract.custom_fields == {}
    assert contract.previous_contract_id is None


# ---- ck_contracts_renewal_type -----------------------------------------------------


def test_renewal_type_is_constrained_to_the_closed_set(db_session: Session) -> None:
    customer = make_customer(db_session)
    with pytest.raises(IntegrityError):
        _contract(db_session, customer, renewal_type="magic_renewal")


# ---- ck_contracts_notice_renewal_required ---------------------------------------


def test_renewal_notice_days_required_unless_renewal_type_is_none(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    with pytest.raises(IntegrityError):
        _contract(
            db_session,
            customer,
            renewal_type="explicit",
            renewal_notice_days=None,
        )


def test_renewal_notice_days_supplied_for_to_real_renewal_type_is_accepted(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = _contract(
        db_session,
        customer,
        renewal_type="explicit",
        renewal_notice_days=60,
    )
    assert contract.renewal_notice_days == 60


def test_renewal_notice_days_absent_is_fine_when_renewal_type_is_none(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer, renewal_type="none")
    assert contract.renewal_notice_days is None


# ---- ck_contracts_payment_terms_together -------------------------------------------


def test_payment_terms_half_set_is_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    with pytest.raises(IntegrityError):
        _contract(db_session, customer, payment_days=30, payment_end_of_month=None)


def test_payment_terms_the_other_half_set_is_also_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    with pytest.raises(IntegrityError):
        _contract(db_session, customer, payment_days=None, payment_end_of_month=True)


def test_payment_terms_fully_null_inherits_the_organizations_own_term(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    assert contract.payment_days is None
    assert contract.payment_end_of_month is None


def test_payment_terms_fully_set_is_accepted(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer, payment_days=30, payment_end_of_month=True)
    assert contract.payment_days == 30
    assert contract.payment_end_of_month is True


def test_contracts_has_a_gin_index_on_custom_fields(db_session: Session) -> None:
    indexes = inspect(db_session.get_bind()).get_indexes("contracts")
    assert any(index["name"] == "ix_contracts_custom_fields" for index in indexes)


# ---- rate_cards ---------------------------------------------------------------------


def _rate_card(db_session: Session, contract: Contract, **overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valid_from": date(2026, 1, 1),
        "type": "fixed_recurring",
        "amount": Decimal("1000.00"),
        "unit": "month",
    }
    payload.update(overrides)
    rate_card = RateCard(**payload)  # type: ignore[arg-type]
    db_session.add(rate_card)
    db_session.flush()
    return rate_card


def test_a_rate_card_can_be_created_with_its_own_defaults(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    rate_card = _rate_card(db_session, contract)
    assert rate_card.valid_to is None
    assert rate_card.allowed_fractions == [Decimal("1.00")]


# ---- ck_rate_cards_no_overlap (btree_gist exclusion constraint) --------------------


def test_two_overlapping_rate_cards_on_one_contract_are_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract, type="daily", unit="day", valid_to=date(2026, 6, 30))
    with pytest.raises(IntegrityError):
        _rate_card(
            db_session,
            contract,
            type="daily",
            unit="day",
            valid_from=date(2026, 6, 1),
            valid_to=date(2026, 12, 31),
        )


def test_two_adjacent_rate_cards_on_one_contract_are_accepted(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract, valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 30))
    second = _rate_card(
        db_session, contract, valid_from=date(2026, 7, 1), valid_to=date(2026, 12, 31)
    )
    assert second.valid_from == date(2026, 7, 1)


def test_two_open_ended_rate_cards_on_one_contract_overlap_and_are_refused(
    db_session: Session,
) -> None:
    """Mastro's own convention: `valido_a IS NULL` is the open, current card. Two
    of them on the same contract both claim "now onward", which the exclusion
    constraint must refuse -- `daterange(..., NULL, '[]')` is Postgres's own
    unbounded-on-this-side range, not a range with no members."""
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    _rate_card(db_session, contract, type="daily", unit="day", valid_to=None)
    with pytest.raises(IntegrityError):
        _rate_card(
            db_session,
            contract,
            type="daily",
            unit="day",
            valid_from=date(2026, 6, 1),
            valid_to=None,
        )


def test_overlapping_rate_cards_on_different_contracts_are_both_accepted(
    db_session: Session,
) -> None:
    """The exclusion constraint's own `contract_id WITH =` clause: overlap is
    refused only within one contract, never across two."""
    customer = make_customer(db_session)
    contract_a = _contract(db_session, customer)
    contract_b = _contract(db_session, customer)
    _rate_card(db_session, contract_a, valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31))
    second = _rate_card(
        db_session, contract_b, valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31)
    )
    assert second.contract_id == contract_b.id


def test_a_day_and_an_hour_rate_in_force_together_are_refused(db_session: Session) -> None:
    """The one rate a worked day is priced by: `daily` and `hourly` share the exclusion."""
    contract = _contract(db_session, make_customer(db_session))
    _rate_card(db_session, contract, type="daily", unit="day")
    with pytest.raises(IntegrityError):
        _rate_card(db_session, contract, type="hourly", unit="hour")


def test_fee_cards_overlap_each_other_and_a_day_rate(db_session: Session) -> None:
    contract = _contract(db_session, make_customer(db_session))
    _rate_card(db_session, contract, type="daily", unit="day")
    _rate_card(db_session, contract, type="fixed_recurring", delivery_period="monthly")
    _rate_card(db_session, contract, type="fixed_recurring", delivery_period="annual")
    one_off = _rate_card(db_session, contract, type="one_off", unit="flat")
    assert one_off.expected_quantity is None


def test_a_day_card_carries_a_forecast_as_a_pair_or_not_at_all(db_session: Session) -> None:
    contract = _contract(db_session, make_customer(db_session))
    card = _rate_card(
        db_session,
        contract,
        type="daily",
        unit="day",
        delivery_period="monthly",
        expected_quantity=Decimal("10.00"),
    )
    assert card.expected_quantity == Decimal("10.00")
    for lone in ({"delivery_period": "monthly"}, {"expected_quantity": Decimal("3.00")}):
        with pytest.raises(IntegrityError), db_session.begin_nested():
            _rate_card(
                db_session,
                contract,
                type="hourly",
                unit="hour",
                valid_from=date(2030, 1, 1),
                **lone,
            )


def test_a_day_card_forecast_period_is_one_off_monthly_or_annual(db_session: Session) -> None:
    contract = _contract(db_session, make_customer(db_session))
    with pytest.raises(IntegrityError):
        _rate_card(
            db_session,
            contract,
            type="daily",
            unit="day",
            delivery_period="quarterly",
            expected_quantity=Decimal("1.00"),
        )


def test_expected_quantity_is_refused_on_a_fee_card(db_session: Session) -> None:
    contract = _contract(db_session, make_customer(db_session))
    with pytest.raises(IntegrityError):
        _rate_card(
            db_session,
            contract,
            type="fixed_recurring",
            delivery_period="monthly",
            expected_quantity=Decimal("2.00"),
        )


# ---- ck_rate_cards_ore_minime_only_orario / ck_rate_cards_periodo_only_ricorrente --


def test_minimum_hours_is_refused_outside_to_type_hourly_card(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    with pytest.raises(IntegrityError):
        _rate_card(db_session, contract, type="daily", minimum_hours=Decimal("4.00"))


def test_minimum_hours_is_accepted_on_to_type_hourly_card(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    rate_card = _rate_card(
        db_session, contract, type="hourly", unit="hour", minimum_hours=Decimal("4.00")
    )
    assert rate_card.minimum_hours == Decimal("4.00")


def test_delivery_period_is_refused_outside_to_type_fixed_recurring_card(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    with pytest.raises(IntegrityError):
        _rate_card(db_session, contract, type="one_off", delivery_period="monthly")


def test_delivery_period_is_accepted_on_to_type_fixed_recurring_card(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    rate_card = _rate_card(db_session, contract, type="fixed_recurring", delivery_period="monthly")
    assert rate_card.delivery_period == "monthly"


def test_rate_cards_has_an_index_on_contract_id(db_session: Session) -> None:
    indexes = inspect(db_session.get_bind()).get_indexes("rate_cards")
    assert any(index["name"] == "ix_rate_cards_contract_id" for index in indexes)


# ---- renewal_assumptions (REB-375) --------------------------------------------------


def _renewal_assumption(
    db_session: Session, contract: Contract, **overrides: object
) -> RenewalAssumption:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "probability": 50,
        "expected_volume": Decimal("36500.00"),
        "horizon_until": date(2027, 12, 31),
    }
    payload.update(overrides)
    assumption = RenewalAssumption(**payload)  # type: ignore[arg-type]
    db_session.add(assumption)
    db_session.flush()
    return assumption


def test_a_renewal_assumption_can_be_created_and_read(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    assumption = _renewal_assumption(db_session, contract)
    assert assumption.contract_id == contract.id
    assert assumption.probability == 50
    assert assumption.expected_volume == Decimal("36500.00")
    assert assumption.horizon_until == date(2027, 12, 31)


def test_probability_below_zero_is_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    with pytest.raises(IntegrityError):
        _renewal_assumption(db_session, contract, probability=-1)


def test_probability_above_to_hundred_is_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    with pytest.raises(IntegrityError):
        _renewal_assumption(db_session, contract, probability=101)


def test_to_negative_expected_volume_is_refused(db_session: Session) -> None:
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    with pytest.raises(IntegrityError):
        _renewal_assumption(db_session, contract, expected_volume=Decimal("-1.00"))


def test_a_second_renewal_assumption_on_the_same_contract_is_refused(
    db_session: Session,
) -> None:
    """One row per contract (Done-when: a human revises the belief in place, this
    table never accumulates a history the database would have to disambiguate)."""
    customer = make_customer(db_session)
    contract = _contract(db_session, customer)
    _renewal_assumption(db_session, contract)
    with pytest.raises(IntegrityError):
        _renewal_assumption(db_session, contract)


def test_renewal_assumptions_on_different_contracts_are_both_accepted(
    db_session: Session,
) -> None:
    customer = make_customer(db_session)
    contract_a = _contract(db_session, customer, title="Contratto A")
    contract_b = _contract(db_session, customer, title="Contratto B")
    first = _renewal_assumption(db_session, contract_a)
    second = _renewal_assumption(db_session, contract_b)
    assert first.contract_id != second.contract_id
