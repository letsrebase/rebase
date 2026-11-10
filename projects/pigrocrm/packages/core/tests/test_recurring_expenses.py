"""Recurring expenses (REB-1022, spec 2026-11-09, due items, § 1.2): a rule owned by due items
writes one forecast out per due date, the purchase of that period replaces it and never adds
to it, and the daily pass keeps it all in step."""

import datetime as dt
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.clock import add_months, today_local
from pigrocrm.core.due_items.forecast import (
    HORIZON_DAYS,
    RECURRING_EXPENSE,
    ForecastService,
    forecast_key,
    refresh_forecasts,
)
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.recurring import RecurringExpenseService
from pigrocrm.core.due_items.recurring_schemas import RecurringExpenseCreate
from pigrocrm.core.errors import Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.schemas import PurchaseCreate
from pigrocrm.core.purchases.service import PurchaseService

ADMIN = Actor(id=None, type="system", role="admin")
WRITER = Actor(id=None, type="user", role="collaborator")
READER = Actor(id=None, type="user", role="readonly")
TODAY = today_local()
# The first of next month: always ahead of today, so no occurrence is in the past.
FIRST = add_months(TODAY.replace(day=1), 1)


@pytest.fixture
def legal_entity_id(db_session: Session) -> UUID:
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(legal_name="Studio Rossi", country="IT"), ADMIN
    )
    legal_entity = LegalEntityRepository(db_session).default()
    assert legal_entity is not None
    return legal_entity.id


@pytest.fixture
def supplier_id(db_session: Session) -> UUID:
    supplier = Organization(legal_name="Affitti Srl", country="IT", roles=["supplier"])
    db_session.add(supplier)
    db_session.flush()
    return supplier.id


def _rule(db_session: Session, supplier_id: UUID, category_id: UUID, **overrides: object) -> UUID:
    payload: dict[str, object] = {
        "supplier_id": supplier_id,
        "category_id": category_id,
        "description": "Affitto ufficio",
        "amount": Decimal("800.00"),
        "cadence": "monthly",
        "first_due_date": FIRST,
    }
    payload.update(overrides)
    return (
        RecurringExpenseService(db_session)
        .create_recurring_expense(RecurringExpenseCreate(**payload), WRITER)  # type: ignore[arg-type]
        .id
    )


def _forecasts(db_session: Session, rule_id: UUID | None = None) -> list[DueItem]:
    stmt = select(DueItem).where(
        DueItem.source_kind == RECURRING_EXPENSE, DueItem.state == "forecast"
    )
    if rule_id is not None:
        stmt = stmt.where(DueItem.source_id == rule_id)
    return sorted(db_session.scalars(stmt), key=lambda item: item.own_date or dt.date.max)


def test_a_rule_forecasts_one_item_out_for_each_due_date_in_the_horizon(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    rule_id = _rule(db_session, supplier_id, seeded_category_id)
    items = _forecasts(db_session, rule_id)

    horizon = TODAY + dt.timedelta(days=HORIZON_DAYS)
    expected = []
    day, index = FIRST, 0
    while day <= horizon:
        expected.append(day)
        index += 1
        day = add_months(FIRST, index)
    assert [item.own_date for item in items] == expected
    first = items[0]
    assert first.direction == "out"
    assert first.organization_id == supplier_id
    assert first.amount == Decimal("800.00")
    assert first.due_date == FIRST
    assert first.legal_entity_id == legal_entity_id
    assert first.forecast_key == forecast_key(RECURRING_EXPENSE, rule_id, FIRST)


def test_the_cadence_spaces_the_due_dates_and_the_end_stops_them(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    rule_id = _rule(
        db_session,
        supplier_id,
        seeded_category_id,
        cadence="quarterly",
        ends_on=add_months(FIRST, 3),
    )
    assert [item.own_date for item in _forecasts(db_session, rule_id)] == [
        FIRST,
        add_months(FIRST, 3),
    ]


def test_a_purchase_of_the_period_replaces_the_forecast_and_is_never_added_to_it(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    rule_id = _rule(db_session, supplier_id, seeded_category_id)
    before = _forecasts(db_session, rule_id)

    purchase = PurchaseService(db_session).create(
        PurchaseCreate(
            category_id=seeded_category_id,
            supplier_id=supplier_id,
            date=FIRST + dt.timedelta(days=2),
            amount=Decimal("800.00"),
            description="Affitto",
        ),
        WRITER,
    )
    after = _forecasts(db_session, rule_id)
    assert [item.own_date for item in after] == [item.own_date for item in before[1:]]
    replaced = db_session.scalars(
        select(DueItem).where(
            DueItem.forecast_key == forecast_key(RECURRING_EXPENSE, rule_id, FIRST)
        )
    ).one()
    assert replaced.state == "dropped"
    assert replaced.dropped_reason == "sostituita da un documento"

    # Cancelling the purchase gives the period its forecast back, as a new live item.
    PurchaseService(db_session).cancel(purchase.id, WRITER)
    assert [item.own_date for item in _forecasts(db_session, rule_id)] == [
        item.own_date for item in before
    ]


def test_another_supplier_or_a_credit_does_not_replace_the_forecast(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    other = Organization(legal_name="Altro Srl", country="IT", roles=["supplier"])
    db_session.add(other)
    db_session.flush()
    rule_id = _rule(db_session, supplier_id, seeded_category_id)
    count = len(_forecasts(db_session, rule_id))
    PurchaseService(db_session).create(
        PurchaseCreate(
            category_id=seeded_category_id,
            supplier_id=other.id,
            date=FIRST,
            amount=Decimal("800.00"),
            description="Altro",
        ),
        WRITER,
    )
    PurchaseService(db_session).create(
        PurchaseCreate(
            category_id=seeded_category_id,
            supplier_id=supplier_id,
            date=FIRST,
            amount=Decimal("-50.00"),
            description="Rimborso",
        ),
        WRITER,
    )
    assert len(_forecasts(db_session, rule_id)) == count


def test_the_daily_pass_is_idempotent_and_replaces_a_purchase_written_outside_the_service(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    from pigrocrm.core.purchases.models import Purchase

    rule_id = _rule(db_session, supplier_id, seeded_category_id)
    first = refresh_forecasts(db_session, today=TODAY)
    assert (first.created, first.dropped, first.failed) == (0, 0, 0)

    db_session.add(
        Purchase(
            category_id=seeded_category_id,
            supplier_id=supplier_id,
            date=FIRST,
            amount=Decimal("800.00"),
            description="Affitto",
        )
    )
    db_session.flush()
    report = refresh_forecasts(db_session, today=TODAY)
    assert report.dropped == 1
    assert FIRST not in [item.own_date for item in _forecasts(db_session, rule_id)]
    assert refresh_forecasts(db_session, today=TODAY) == type(report)()


def test_the_pass_extends_the_horizon_and_drops_a_due_date_that_passed(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    rule_id = _rule(db_session, supplier_id, seeded_category_id)
    later = TODAY + dt.timedelta(days=62)
    ForecastService(db_session).refresh_recurring_expense(rule_id, today=later)
    dates = [item.own_date for item in _forecasts(db_session, rule_id)]
    assert FIRST not in dates
    assert min(dates) >= later
    assert max(dates) <= later + dt.timedelta(days=HORIZON_DAYS)


def test_stopping_a_rule_drops_its_forecasts_and_is_not_repeated(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    rule_id = _rule(db_session, supplier_id, seeded_category_id)
    service = RecurringExpenseService(db_session)
    stopped = service.stop_recurring_expense(rule_id, WRITER)
    assert stopped.stopped_on == TODAY
    assert stopped.active is False
    assert _forecasts(db_session, rule_id) == []
    with pytest.raises(Conflict):
        service.stop_recurring_expense(rule_id, WRITER)
    assert refresh_forecasts(db_session, today=TODAY).created == 0
    assert _forecasts(db_session, rule_id) == []
    assert service.list_recurring_expenses(READER) == []
    assert [r.id for r in service.list_recurring_expenses(READER, include_stopped=True)] == [
        rule_id
    ]


def test_the_rule_is_validated_and_authorised(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    customer = Organization(legal_name="Cliente Srl", country="IT")
    db_session.add(customer)
    db_session.flush()
    with pytest.raises(ValidationFailed) as excinfo:
        _rule(db_session, customer.id, seeded_category_id)
    assert excinfo.value.details["field"] == "supplier_id"
    with pytest.raises(ValueError, match="precedere"):
        RecurringExpenseCreate(
            supplier_id=supplier_id,
            category_id=seeded_category_id,
            description="x",
            amount=Decimal("1.00"),
            cadence="monthly",
            first_due_date=FIRST,
            ends_on=FIRST - dt.timedelta(days=1),
        )
    data = RecurringExpenseCreate(
        supplier_id=supplier_id,
        category_id=seeded_category_id,
        description="x",
        amount=Decimal("1.00"),
        cadence="annual",
        first_due_date=FIRST,
    )
    service = RecurringExpenseService(db_session)
    with pytest.raises(PermissionDenied):
        service.create_recurring_expense(data, READER)
    # An agent's token writes it, as it writes a purchase.
    agent = Actor(id=None, type="mcp", role="collaborator")
    assert service.create_recurring_expense(data, agent).cadence == "annual"


def test_a_purchase_of_another_company_does_not_replace_and_a_shared_one_does(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    from pigrocrm.core.legal_entities.models import LegalEntity
    from pigrocrm.core.purchases.models import Purchase

    other = LegalEntity(name="altra", legal_name="Altra Srl", country="IT")
    db_session.add(other)
    db_session.flush()
    rule_id = _rule(db_session, supplier_id, seeded_category_id, legal_entity_id=legal_entity_id)
    count = len(_forecasts(db_session, rule_id))

    def buy(legal_entity: UUID | None) -> Purchase:
        purchase = Purchase(
            category_id=seeded_category_id,
            supplier_id=supplier_id,
            legal_entity_id=legal_entity,
            date=FIRST,
            amount=Decimal("800.00"),
            description="Affitto",
        )
        db_session.add(purchase)
        db_session.flush()
        return purchase

    buy(other.id)
    ForecastService(db_session).refresh_supplier_rules(supplier_id)
    assert len(_forecasts(db_session, rule_id)) == count
    buy(None)
    ForecastService(db_session).refresh_supplier_rules(supplier_id)
    assert len(_forecasts(db_session, rule_id)) == count - 1


def test_the_period_is_the_cadences_calendar_span_and_a_moved_purchase_gives_it_back(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    from pigrocrm.core.purchases.schemas import PurchaseUpdate

    first = dt.date(TODAY.year + 1, 2, 10)
    rule_id = _rule(
        db_session, supplier_id, seeded_category_id, cadence="quarterly", first_due_date=first
    )
    service = PurchaseService(db_session)
    count = len(_forecasts(db_session, rule_id))
    # Quarter one of the year runs from 1 January to 31 March: a purchase on 1 January covers
    # the due date of 10 February, one on 1 April does not.
    purchase = service.create(
        PurchaseCreate(
            category_id=seeded_category_id,
            supplier_id=supplier_id,
            date=dt.date(first.year, 4, 1),
            amount=Decimal("800.00"),
            description="Affitto",
        ),
        WRITER,
    )
    assert len(_forecasts(db_session, rule_id)) == count
    service.update(purchase.id, PurchaseUpdate(date=dt.date(first.year, 1, 1)), WRITER)
    assert first not in [item.own_date for item in _forecasts(db_session, rule_id)]
    service.update(purchase.id, PurchaseUpdate(date=dt.date(first.year, 4, 1)), WRITER)
    assert first in [item.own_date for item in _forecasts(db_session, rule_id)]
    # Archiving the purchase that covers the period frees it too; restoring covers it again.
    service.update(purchase.id, PurchaseUpdate(date=dt.date(first.year, 1, 1)), WRITER)
    service.soft_delete(purchase.id, WRITER)
    assert first in [item.own_date for item in _forecasts(db_session, rule_id)]
    service.restore(purchase.id, WRITER)
    assert first not in [item.own_date for item in _forecasts(db_session, rule_id)]


def test_merging_two_suppliers_moves_the_rule_and_replaces_with_the_losers_purchases(
    db_session: Session, legal_entity_id: UUID, supplier_id: UUID, seeded_category_id: UUID
) -> None:
    from pigrocrm.core.organizations.service import OrganizationService
    from pigrocrm.core.purchases.models import Purchase

    loser = Organization(legal_name="Affitti Sas", country="IT", roles=["supplier"])
    db_session.add(loser)
    db_session.flush()
    rule_id = _rule(db_session, loser.id, seeded_category_id)
    db_session.add(
        Purchase(
            category_id=seeded_category_id,
            supplier_id=supplier_id,
            date=FIRST,
            amount=Decimal("800.00"),
            description="Affitto",
        )
    )
    db_session.flush()
    count = len(_forecasts(db_session, rule_id))

    OrganizationService(db_session).merge(supplier_id, loser.id, ADMIN, dry_run=False)

    rule = RecurringExpenseService(db_session).list_recurring_expenses(READER)[0]
    assert rule.supplier_id == supplier_id
    after = _forecasts(db_session, rule_id)
    assert len(after) == count - 1
    assert {item.organization_id for item in after} == {supplier_id}
