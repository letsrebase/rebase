"""A won deal becomes a contract (spec 2026-11-03 §8, REB-933): what it is refused for, what the
contract and its cards carry, and the lock the deal's lines are under afterwards."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_deal_value_lines import line

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import RateCard
from pigrocrm.core.contracts.renewal_service import ContractRenewalService
from pigrocrm.core.contracts.schemas import ContractFromDeal, ContractRenew
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealCreate, DealDetailRead, DealUpdate, DealValueLineIn
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    PipelineService(db_session).seed_defaults(ADMIN)
    return CustomerService(db_session).create(CustomerCreate(legal_name="ACME"), ADMIN).id


def _won_stage(db_session: Session) -> UUID:
    return next(s.id for s in PipelineService(db_session).list() if s.type == "won")


def _deal(
    db_session: Session,
    customer_id: UUID,
    *lines: DealValueLineIn,
    won: bool = True,
    **fields: object,
) -> DealDetailRead:
    deals = DealService(db_session)
    deal = deals.create(
        DealCreate(name="Piattaforma", customer_id=customer_id, value_lines=list(lines), **fields),  # type: ignore[arg-type]
        ADMIN,
    )
    if won:
        deals.move_stage(deal.id, _won_stage(db_session), ADMIN)
    return deals.get(deal.id, ADMIN)


def _confirm(**overrides: object) -> ContractFromDeal:
    payload: dict[str, object] = {
        "title": "Piattaforma",
        "start": date(2026, 1, 1),
        "end": None,
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
    }
    payload.update(overrides)
    return ContractFromDeal(**payload)  # type: ignore[arg-type]


def _cards(db_session: Session, contract_id: UUID) -> list[RateCard]:
    return list(
        db_session.scalars(
            select(RateCard)
            .where(RateCard.contract_id == contract_id)
            .order_by(RateCard.created_at, RateCard.type)
        )
    )


def _mixed(db_session: Session, customer_id: UUID) -> DealDetailRead:
    """An upfront dated in February, an open-ended fee and a day rate for a year, the last two
    following the deal's expected close date."""
    return _deal(
        db_session,
        customer_id,
        line(price="20000", description="Sviluppo", start=date(2026, 2, 1)),
        line(period="monthly", price="500", description="Manutenzione"),
        line(unit="day", period="monthly", price="600", quantity="4", months=12),
        expected_close_date=date(2026, 3, 1),
    )


# ---- the proposal -------------------------------------------------------------------


def test_the_proposal_starts_on_the_earliest_line_and_has_no_end_while_a_line_is_open(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _mixed(db_session, customer_id)

    proposal = ContractService(db_session).proposal_from_deal(deal.id, ADMIN)

    assert (proposal.title, proposal.start, proposal.end) == ("Piattaforma", date(2026, 2, 1), None)
    assert proposal.customer_id == customer_id
    assert [(c.type, c.unit, c.amount, c.valid_from) for c in proposal.cards] == [
        ("one_off", "flat", Decimal("20000.00"), date(2026, 2, 1)),
        ("fixed_recurring", "month", Decimal("500.00"), date(2026, 2, 1)),
        ("daily", "day", Decimal("600.00"), date(2026, 2, 1)),
    ]


def test_the_proposed_end_is_the_latest_last_day_when_every_line_has_one(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _deal(
        db_session,
        customer_id,
        line(
            unit="day",
            period="monthly",
            price="450",
            quantity="18",
            start=date(2026, 1, 1),
            months=12,
        ),
        line(period="annual", price="6000", start=date(2026, 1, 1), months=36),
        line(price="3000", start=date(2026, 1, 1)),
    )

    proposal = ContractService(db_session).proposal_from_deal(deal.id, ADMIN)

    assert proposal.end == date(2028, 12, 31)


def test_a_deal_of_lump_sums_alone_proposes_no_end(db_session: Session, customer_id: UUID) -> None:
    deal = _deal(db_session, customer_id, line(price="30000", start=date(2026, 4, 1)))

    proposal = ContractService(db_session).proposal_from_deal(deal.id, ADMIN)

    assert (proposal.start, proposal.end) == (date(2026, 4, 1), None)


# ---- what the conversion refuses ------------------------------------------------------


def test_a_deal_that_is_not_won_is_refused_naming_its_stage(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _deal(db_session, customer_id, line(), won=False)

    with pytest.raises(Conflict, match="solo un deal vinto"):
        ContractService(db_session).create_from_deal(deal.id, _confirm(), ADMIN)
    with pytest.raises(Conflict, match="solo un deal vinto"):
        ContractService(db_session).proposal_from_deal(deal.id, ADMIN)


def test_a_won_deal_with_no_lines_is_refused(db_session: Session, customer_id: UUID) -> None:
    deal = _deal(db_session, customer_id)

    with pytest.raises(Conflict, match="non ha righe di valore"):
        ContractService(db_session).create_from_deal(deal.id, _confirm(), ADMIN)


def test_a_deal_becomes_one_contract_only(db_session: Session, customer_id: UUID) -> None:
    deal = _deal(db_session, customer_id, line())
    contract = ContractService(db_session).create_from_deal(deal.id, _confirm(), ADMIN)

    with pytest.raises(Conflict, match="già diventato il contratto") as refused:
        ContractService(db_session).create_from_deal(deal.id, _confirm(), ADMIN)
    assert refused.value.details["contract_id"] == str(contract.id)


def test_a_missing_deal_is_not_found(db_session: Session, customer_id: UUID) -> None:
    with pytest.raises(NotFound):
        ContractService(db_session).create_from_deal(UUID(int=1), _confirm(), ADMIN)


def test_a_renewal_type_other_than_none_needs_its_notice(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _deal(db_session, customer_id, line())

    with pytest.raises(ValidationFailed, match="renewal_notice_days"):
        ContractService(db_session).create_from_deal(deal.id, _confirm(renewal_type="tacit"), ADMIN)


def test_a_reader_cannot_convert_a_deal(db_session: Session, customer_id: UUID) -> None:
    deal = _deal(db_session, customer_id, line())
    reader = Actor(id=None, type="user", role="readonly")

    with pytest.raises(PermissionDenied):
        ContractService(db_session).create_from_deal(deal.id, _confirm(), reader)


# ---- the contract and its cards -----------------------------------------------------------


def test_the_contract_is_a_draft_of_the_deals_customer_with_no_payment_terms_of_its_own(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _mixed(db_session, customer_id)

    contract = ContractService(db_session).create_from_deal(
        deal.id,
        _confirm(
            title="Contratto piattaforma",
            start=date(2026, 2, 1),
            renewal_type="tacit",
            renewal_notice_days=60,
            termination_notice_days=45,
            billing_cadence="quarterly",
            expense_policy={"kind": "reimbursable"},
        ),
        ADMIN,
    )

    assert contract.status == "draft"
    assert contract.deal_id == deal.id
    assert contract.customer_id == customer_id
    assert contract.legal_entity_id == deal.legal_entity_id
    assert (contract.payment_days, contract.payment_end_of_month) == (None, None)
    assert (contract.title, contract.billing_cadence, contract.renewal_notice_days) == (
        "Contratto piattaforma",
        "quarterly",
        60,
    )
    read = DealService(db_session).get(deal.id, ADMIN)
    assert read.contract_id == contract.id
    assert ContractService(db_session).get(contract.id, ADMIN).deal_id == deal.id


def test_each_line_becomes_the_card_of_the_spec_table(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _deal(
        db_session,
        customer_id,
        line(price="3000", start=date(2026, 1, 1)),
        line(period="monthly", price="500", start=date(2026, 1, 1), months=12),
        line(period="annual", price="6000", start=date(2026, 1, 1), months=36),
        line(
            unit="day",
            period="monthly",
            price="600",
            quantity="4",
            start=date(2026, 1, 1),
            months=12,
        ),
        line(
            unit="hour",
            period="one_off",
            price="80",
            quantity="25.5",
            start=date(2027, 1, 1),
            months=2,
        ),
    )

    contract = ContractService(db_session).create_from_deal(
        deal.id, _confirm(end=date(2028, 12, 31)), ADMIN
    )

    shapes = {
        (c.type, c.unit, c.delivery_period, c.amount, c.expected_quantity, c.valid_from, c.valid_to)
        for c in _cards(db_session, contract.id)
    }
    assert shapes == {
        ("one_off", "flat", None, Decimal("3000.00"), None, date(2026, 1, 1), date(2026, 1, 1)),
        (
            "fixed_recurring",
            "month",
            "monthly",
            Decimal("500.00"),
            None,
            date(2026, 1, 1),
            date(2026, 12, 31),
        ),
        (
            "fixed_recurring",
            "year",
            "annual",
            Decimal("6000.00"),
            None,
            date(2026, 1, 1),
            date(2028, 12, 31),
        ),
        (
            "daily",
            "day",
            "monthly",
            Decimal("600.00"),
            Decimal("4.00"),
            date(2026, 1, 1),
            date(2026, 12, 31),
        ),
        (
            "hourly",
            "hour",
            "one_off",
            Decimal("80.00"),
            Decimal("25.50"),
            date(2027, 1, 1),
            date(2027, 2, 28),
        ),
    }


def test_a_line_with_no_start_takes_the_start_the_contract_is_confirmed_with(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _mixed(db_session, customer_id)

    contract = ContractService(db_session).create_from_deal(
        deal.id, _confirm(start=date(2026, 5, 15)), ADMIN
    )

    by_type = {c.type: c for c in _cards(db_session, contract.id)}
    assert by_type["one_off"].valid_from == date(2026, 2, 1)
    assert by_type["fixed_recurring"].valid_from == date(2026, 5, 15)
    assert (by_type["daily"].valid_from, by_type["daily"].valid_to) == (
        date(2026, 5, 15),
        date(2027, 5, 14),
    )


def test_a_start_that_makes_two_day_rates_meet_is_refused_naming_the_start(
    db_session: Session, customer_id: UUID
) -> None:
    # On the deal's dates the second rate (no start, so the close date) begins after the first
    # ended; on a contract starting in 2026-03 it begins inside it.
    deal = _deal(
        db_session,
        customer_id,
        line(
            unit="day",
            period="monthly",
            price="500",
            quantity="10",
            start=date(2026, 1, 1),
            months=12,
        ),
        line(unit="day", period="monthly", price="700", quantity="10", months=6),
        expected_close_date=date(2027, 1, 1),
    )

    with pytest.raises(ValidationFailed, match="si sovrappongono") as refused:
        ContractService(db_session).create_from_deal(
            deal.id, _confirm(start=date(2026, 3, 1)), ADMIN
        )
    assert refused.value.details["field"] == "start"
    assert ContractService(db_session).proposal_from_deal(deal.id, ADMIN).deal_id == deal.id


# ---- the lock ------------------------------------------------------------------------------


def test_the_lines_of_a_converted_deal_are_locked_naming_the_contract(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _deal(db_session, customer_id, line(price="1000"))
    contract = ContractService(db_session).create_from_deal(
        deal.id, _confirm(title="Piattaforma 2026"), ADMIN
    )

    with pytest.raises(Conflict, match="Piattaforma 2026") as refused:
        DealService(db_session).update(deal.id, DealUpdate(value_lines=[line(price="2000")]), ADMIN)
    assert refused.value.details["contract_id"] == str(contract.id)
    with pytest.raises(Conflict):
        DealService(db_session).update(deal.id, DealUpdate(value_lines=[]), ADMIN)
    assert DealService(db_session).get(deal.id, ADMIN).expected_value == Decimal("1000.00")


def test_a_converted_deal_still_takes_every_other_edit_and_the_same_lines_again(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _deal(db_session, customer_id, line(price="1000"))
    ContractService(db_session).create_from_deal(deal.id, _confirm(), ADMIN)

    updated = DealService(db_session).update(
        deal.id,
        DealUpdate(
            name="Piattaforma v2",
            value_lines=[line(price="1000")],
            expected_close_date=date(2027, 1, 1),
        ),
        ADMIN,
    )

    assert updated.name == "Piattaforma v2"
    assert [(row.unit_price) for row in updated.value_lines] == [Decimal("1000.00")]


# ---- renewal ------------------------------------------------------------------------------


def test_a_converted_contract_renews_keeping_its_day_forecast_and_not_its_deal(
    db_session: Session, customer_id: UUID
) -> None:
    deal = _deal(
        db_session,
        customer_id,
        line(price="3000", start=date(2026, 1, 1)),
        line(period="monthly", price="500", start=date(2026, 1, 1)),
        line(unit="day", period="monthly", price="600", quantity="4", start=date(2026, 1, 1)),
    )
    contract = ContractService(db_session).create_from_deal(
        deal.id,
        _confirm(end=date(2026, 12, 31), renewal_type="explicit", renewal_notice_days=30),
        ADMIN,
    )

    successor = ContractRenewalService(db_session).renew(contract.id, ContractRenew(), ADMIN)

    assert successor.deal_id is None
    assert successor.previous_contract_id == contract.id
    carried = {
        (c.type, c.expected_quantity, c.delivery_period) for c in _cards(db_session, successor.id)
    }
    assert ("daily", Decimal("4.00"), "monthly") in carried
    assert ("fixed_recurring", None, "monthly") in carried
    # The upfront fee was closed on its own day, so the next term does not carry it.
    assert not any(kind == "one_off" for kind, _quantity, _period in carried)
