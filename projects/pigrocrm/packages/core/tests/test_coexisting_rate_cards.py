"""A contract holds an upfront fee, a monthly fee and a day rate together (REB-932).

Only the day and hour cards are the rate a worked day is priced by, so only they exclude
each other in time; every reader that asks "the card in force on this date" must look past
the fees that overlap it, and `close_previous` must never end a fee.
"""

from datetime import date
from decimal import Decimal

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy.orm import Session
from test_contracts_service import _create_payload

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.projection import project
from pigrocrm.core.contracts.rate_card_service import RateCardService
from pigrocrm.core.contracts.repository import ContractRepository, RateCardRepository
from pigrocrm.core.contracts.schemas import ContractRenew, RateCardCreate, RateCardUpdate
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.work_units.day_mapping import propose_day_mapping, resolve_rate_card
from pigrocrm.core.work_units.invoicing import unbilled_work_unit_lines
from pigrocrm.core.work_units.models import WorkUnit

ADMIN = Actor(id=None, type="system", role="admin")


def _card(**overrides: object) -> RateCardCreate:
    payload: dict[str, object] = {
        "valid_from": date(2026, 1, 1),
        "type": "daily",
        "amount": Decimal("500.00"),
        "unit": "day",
    }
    payload.update(overrides)
    return RateCardCreate(**payload)  # type: ignore[arg-type]


def _setup(db_session: Session) -> tuple[Contract, RateCardService]:
    """A contract with a one_off fee, a monthly fee and a day rate, all open from January."""
    contract = ContractService(db_session).create(
        _create_payload(make_customer(db_session).id), ADMIN
    )
    cards = RateCardService(db_session)
    cards.create(
        contract.id,
        _card(type="one_off", unit="flat", amount=Decimal("3000.00"), valid_to=date(2026, 1, 1)),
        ADMIN,
    )
    cards.create(
        contract.id,
        _card(
            type="fixed_recurring",
            unit="month",
            amount=Decimal("1000.00"),
            delivery_period="monthly",
        ),
        ADMIN,
    )
    cards.create(contract.id, _card(), ADMIN)
    model = ContractRepository(db_session).get(contract.id)
    assert model is not None
    return model, cards


def _day(db_session: Session, contract: Contract, day: date) -> WorkUnit:
    work_unit = WorkUnit(
        contract_id=contract.id,
        date=day,
        quantity=Decimal("1.00"),
        description="Giornata",
        status="worked",
    )
    db_session.add(work_unit)
    db_session.flush()
    return work_unit


def test_the_day_rate_is_the_card_in_force_whatever_fees_overlap_it(db_session: Session) -> None:
    contract, _ = _setup(db_session)
    stored = RateCardRepository(db_session).list_for_contract(contract.id)
    assert {c.type for c in stored} == {"one_off", "fixed_recurring", "daily"}

    # The fees are listed first by validity, yet the lookup returns the day rate.
    in_force = resolve_rate_card(stored, date(2026, 1, 1))
    assert in_force is not None and in_force.type == "daily"

    work_unit = _day(db_session, contract, date(2026, 1, 1))
    proposal = propose_day_mapping(
        Decimal("1"), Decimal("500.00"), date(2026, 1, 31), [work_unit], stored
    )
    assert proposal is not None and proposal.match_amounts is True


def test_invoicing_groups_the_days_by_the_day_rate_not_by_a_fee(db_session: Session) -> None:
    contract, _ = _setup(db_session)
    _day(db_session, contract, date(2026, 3, 5))
    _day(db_session, contract, date(2026, 3, 12))

    lines, groups = unbilled_work_unit_lines(db_session, contract.id)

    assert len(lines) == 1
    assert (lines[0].quantity, lines[0].unit_price) == (Decimal("2.00"), Decimal("500.00"))
    assert len(groups[0]) == 2


def test_the_projection_still_projects_the_monthly_fee_beside_a_day_rate(
    db_session: Session,
) -> None:
    contract, _ = _setup(db_session)
    stored = RateCardRepository(db_session).list_for_contract(contract.id)

    projected = project(
        contract, stored, None, date(2026, 1, 1), date(2026, 2, 1), date(2026, 5, 1)
    )

    # February, March and April: three monthly fees. Neither the one_off nor the day rate
    # contributes a recurring occurrence.
    assert projected.scheduled == Decimal("3000.00")


def test_a_second_day_rate_overlapping_the_first_is_a_conflict_but_a_fee_is_not(
    db_session: Session,
) -> None:
    contract, cards = _setup(db_session)

    with pytest.raises(Conflict):
        cards.create(contract.id, _card(valid_from=date(2026, 6, 1)), ADMIN)
    with pytest.raises(Conflict):
        cards.create(
            contract.id, _card(type="hourly", unit="hour", valid_from=date(2026, 6, 1)), ADMIN
        )

    cards.create(
        contract.id,
        _card(
            type="fixed_recurring",
            unit="month",
            valid_from=date(2026, 6, 1),
            delivery_period="annual",
        ),
        ADMIN,
    )


def test_close_previous_ends_the_day_rate_and_leaves_every_fee_alone(
    db_session: Session,
) -> None:
    contract, cards = _setup(db_session)

    cards.create(
        contract.id,
        _card(valid_from=date(2026, 7, 1), amount=Decimal("600.00")),
        ADMIN,
        close_previous=True,
    )

    by_type = {c.type: c for c in cards.list_for_contract(contract.id, ADMIN) if c.amount != 600}
    assert by_type["daily"].valid_to == date(2026, 6, 30)
    assert by_type["fixed_recurring"].valid_to is None
    assert by_type["one_off"].valid_to == date(2026, 1, 1)


def test_close_previous_on_a_fee_is_refused_and_changes_nothing(db_session: Session) -> None:
    contract, cards = _setup(db_session)

    with pytest.raises(ValidationFailed) as refused:
        cards.create(
            contract.id,
            _card(
                type="fixed_recurring",
                unit="month",
                valid_from=date(2026, 7, 1),
                delivery_period="monthly",
            ),
            ADMIN,
            close_previous=True,
        )

    assert refused.value.details["field"] == "close_previous"
    listed = cards.list_for_contract(contract.id, ADMIN)
    assert len(listed) == 3
    assert all(c.valid_to is None for c in listed if c.type != "one_off")


def test_a_day_rate_forecast_comes_as_a_pair_and_never_on_a_fee(db_session: Session) -> None:
    contract, cards = _setup(db_session)
    daily = next(c for c in cards.list_for_contract(contract.id, ADMIN) if c.type == "daily")

    with pytest.raises(ValidationFailed):
        cards.update(contract.id, daily.id, RateCardUpdate(expected_quantity=Decimal("8")), ADMIN)
    with pytest.raises(ValidationFailed):
        cards.update(contract.id, daily.id, RateCardUpdate(delivery_period="monthly"), ADMIN)
    with pytest.raises(ValidationFailed):
        cards.update(
            contract.id,
            daily.id,
            RateCardUpdate(delivery_period="quarterly", expected_quantity=Decimal("8")),
            ADMIN,
        )
    with pytest.raises(ValidationFailed) as on_fee:
        cards.create(
            contract.id,
            _card(
                type="fixed_recurring",
                unit="month",
                valid_from=date(2027, 1, 1),
                delivery_period="monthly",
                expected_quantity=Decimal("2"),
            ),
            ADMIN,
        )
    assert on_fee.value.details["field"] == "expected_quantity"

    updated = cards.update(
        contract.id,
        daily.id,
        RateCardUpdate(delivery_period="monthly", expected_quantity=Decimal("8")),
        ADMIN,
    )
    assert (updated.delivery_period, updated.expected_quantity) == ("monthly", Decimal("8.00"))


def test_renewal_carries_the_expected_quantity_of_a_day_rate(db_session: Session) -> None:
    contract = ContractService(db_session).create(
        _create_payload(
            make_customer(db_session).id,
            start=date(2026, 1, 1),
            end=date(2026, 12, 31),
            renewal_type="explicit",
            renewal_notice_days=30,
        ),
        ADMIN,
    )
    cards = RateCardService(db_session)
    cards.create(
        contract.id,
        _card(delivery_period="monthly", expected_quantity=Decimal("12.50")),
        ADMIN,
    )
    cards.create(
        contract.id,
        _card(type="fixed_recurring", unit="month", delivery_period="annual"),
        ADMIN,
    )

    successor = ContractService(db_session).renew(contract.id, ContractRenew(), ADMIN)

    carried = {c.type: c for c in cards.list_for_contract(successor.id, ADMIN)}
    assert carried["daily"].expected_quantity == Decimal("12.50")
    assert carried["daily"].delivery_period == "monthly"
    assert carried["fixed_recurring"].expected_quantity is None
    stored = RateCardRepository(db_session).list_for_contract(successor.id)
    assert all(isinstance(c, RateCard) for c in stored)
