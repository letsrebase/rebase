"""A member limited to one legal entity sees none of another's due items or settlements
(REB-967, spec 2026-11-09, due items, § Scope «Row security»).

Proven as the application role, as `test_legal_entity_scope.py` does: the suite's user is a
superuser and a superuser is never subject to a policy. Committed rows, since that role
connects on an engine of its own.
"""

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, create_engine, delete, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.schemas import DueItemCreate, DueItemListQuery, SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization

TAG = "DUESCOPE"
APP_ROLE = "pigrocrm_app_test"
APP_PASSWORD = "pigrocrm_app_test"
D = dt.date


class World(NamedTuple):
    engine: Engine
    app: Engine
    studio: UUID
    foreign: UUID
    studio_item: UUID
    foreign_item: UUID
    studio_settlement: UUID
    foreign_settlement: UUID


def _application_role(engine: Engine) -> Engine:
    app_url = engine.url.set(username=APP_ROLE, password=APP_PASSWORD)
    assert ensure_application_role(engine.url, app_url) is True
    return create_engine(app_url, future=True)


@pytest.fixture
def world(db_engine: Engine) -> Iterator[World]:
    factory = session_factory(db_engine)
    with factory() as session:
        inserted = committed_default_legal_entity(session)
        studio = LegalEntityRepository(session).default()
        assert studio is not None
        foreign = LegalEntity(name=f"{TAG} ltd", legal_name=f"{TAG} Ltd", country="GB")
        session.add(foreign)
        session.flush()
        service = DueItemService(session)
        made: dict[str, tuple[UUID, UUID]] = {}
        for tag, entity in (("studio", studio.id), ("foreign", foreign.id)):
            customer = make_customer(session, legal_name=f"{TAG} {tag}", legal_entity_id=entity)
            item = service.create(
                DueItemCreate(
                    legal_entity_id=entity,
                    direction="in",
                    organization_id=customer.id,
                    amount=Decimal("100.00"),
                    source_kind="sales_invoice",
                    source_id=uuid4(),
                    own_date=D(2026, 2, 28),
                )
            )
            session.commit()
            paid = service.settle(
                item.id,
                SettlementCreate(amount=Decimal("40.00"), settled_on=D(2026, 3, 1)),
                Actor.system(),
            )
            made[tag] = (item.id, paid.settlements[0].id)
        ids = World(
            db_engine,
            _application_role(db_engine),
            studio.id,
            foreign.id,
            made["studio"][0],
            made["foreign"][0],
            made["studio"][1],
            made["foreign"][1],
        )
    try:
        yield ids
    finally:
        ids.app.dispose()
        with factory() as session:
            session.execute(
                delete(DueItemSettlement).where(
                    DueItemSettlement.due_item_id.in_([ids.studio_item, ids.foreign_item])
                )
            )
            session.execute(
                delete(DueItem).where(DueItem.id.in_([ids.studio_item, ids.foreign_item]))
            )
            session.execute(delete(Organization).where(Organization.legal_name.like(f"{TAG} %")))
            session.execute(delete(LegalEntity).where(LegalEntity.id == ids.foreign))
            remove_legal_entity(session, inserted)
            session.commit()


def _scoped(world: World, role: Role, *legal_entities: UUID) -> Session:
    session = session_factory(world.app)()
    actor = Actor(id=uuid4(), type="user", role=role, legal_entities=tuple(legal_entities))
    bind_scope(session, actor)
    return session


def _count(session: Session, table: str) -> int:
    return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def test_a_scoped_member_sees_the_items_and_settlements_of_their_legal_entity_alone(
    world: World,
) -> None:
    with _scoped(world, "collaborator", world.foreign) as session:
        assert _count(session, "due_items") == 1
        assert _count(session, "due_item_settlements") == 1
        member = Actor(
            id=uuid4(), type="user", role="collaborator", legal_entities=(world.foreign,)
        )
        listed = DueItemService(session).list(DueItemListQuery(), member).items
        assert [item.id for item in listed] == [world.foreign_item]
    with _scoped(world, "collaborator", world.studio, world.foreign) as session:
        assert _count(session, "due_items") == 2
        assert _count(session, "due_item_settlements") == 2
    with _scoped(world, "collaborator") as session:
        assert _count(session, "due_items") == 0
        assert _count(session, "due_item_settlements") == 0


def test_a_session_with_nothing_bound_sees_no_due_item(world: World) -> None:
    with session_factory(world.app)() as session:
        assert _count(session, "due_items") == 0
        assert _count(session, "due_item_settlements") == 0


def test_an_unscoped_member_sees_both(world: World) -> None:
    session = session_factory(world.app)()
    bind_scope(session, Actor(id=uuid4(), type="user", role="collaborator"))
    with session:
        assert _count(session, "due_items") == 2
        assert _count(session, "due_item_settlements") == 2


def test_the_service_cannot_reach_an_item_out_of_sight(world: World) -> None:
    """The row is simply not there for a member of another legal entity: the answer is the one
    an item that does not exist gets, and nothing is written."""
    member = Actor(id=uuid4(), type="user", role="collaborator", legal_entities=(world.foreign,))
    with _scoped(world, "collaborator", world.foreign) as session:
        service = DueItemService(session)
        with pytest.raises(NotFound):
            service.get(world.studio_item, member)
        with pytest.raises(NotFound):
            service.settle(
                world.studio_item,
                SettlementCreate(amount=Decimal("10.00"), settled_on=D(2026, 3, 2)),
                member,
            )
        with pytest.raises(NotFound):
            service.reverse_settlement(world.studio_settlement, None, member)
        session.rollback()
        # Their own is theirs to settle, as the application role, under the policy.
        paid = service.settle(
            world.foreign_item,
            SettlementCreate(amount=Decimal("10.00"), settled_on=D(2026, 3, 2)),
            member,
        )
        assert paid.remaining == Decimal("50.00")
    with session_factory(world.engine)() as session:
        remaining = DueItemService(session).get(world.studio_item, Actor.system())
        assert remaining.remaining == Decimal("60.00")
        assert len(remaining.settlements) == 1


def test_an_insert_across_the_line_is_refused_by_the_policy(world: World) -> None:
    with _scoped(world, "collaborator", world.foreign) as session:
        with pytest.raises(DBAPIError) as refused:
            session.add(
                DueItem(
                    legal_entity_id=world.studio,
                    direction="in",
                    state="expected",
                    counterparty_type="tax_office",
                    tax="vat",
                    amount=Decimal("1.00"),
                    source_kind="iva_settlement",
                )
            )
            session.flush()
        assert "legal_entity_scope" in str(refused.value) or "row-level security" in str(
            refused.value
        )
        session.rollback()
        with pytest.raises(DBAPIError) as refused_settlement:
            session.add(
                DueItemSettlement(
                    due_item_id=world.studio_item,
                    legal_entity_id=world.studio,
                    amount=Decimal("1.00"),
                    settled_on=D(2026, 3, 1),
                    recorded_by_type="user",
                )
            )
            session.flush()
        assert "legal_entity_scope" in str(refused_settlement.value) or "row-level security" in str(
            refused_settlement.value
        )
        session.rollback()


def test_the_policies_put_the_all_fast_path_first(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        policies = {
            row.tablename: (row.qual, row.with_check)
            for row in connection.execute(
                text(
                    "SELECT tablename, qual, with_check FROM pg_policies "
                    "WHERE policyname = 'legal_entity_scope' "
                    "AND tablename IN ('due_items', 'due_item_settlements')"
                )
            )
        }
    assert set(policies) == {"due_items", "due_item_settlements"}
    for using, check in policies.values():
        assert "".join(using.split()).startswith("(scope_all()OR")
        assert check == using
