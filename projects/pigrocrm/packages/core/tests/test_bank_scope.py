"""A member limited to one legal entity sees none of another's bank accounts, statements,
movements or counterparty IBANs (REB-998, spec 2026-11-09, due items, § Scope «Row security»).

Proven as the application role, as `test_due_items_scope.py` does: the suite's user is a
superuser and a superuser is never subject to a policy.
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
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.bank.models import (
    BankAccount,
    BankMovement,
    BankStatement,
    OrganizationIban,
    StatementMovement,
)
from pigrocrm.core.bank.readers import ParsedMovement, ParsedStatement
from pigrocrm.core.bank.service import BankStatementService, OrganizationIbanService
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization

TAG = "BANKSCOPE"
APP_ROLE = "pigrocrm_app_test"
APP_PASSWORD = "pigrocrm_app_test"
IBANS = {"studio": "IT60X0542811101000000123456", "foreign": "DE89370400440532013000"}
TABLES = (
    "bank_accounts",
    "bank_statements",
    "bank_movements",
    "statement_movements",
    "organization_ibans",
)


class World(NamedTuple):
    app: Engine
    studio: UUID
    foreign: UUID


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
        for tag, entity in (("studio", studio.id), ("foreign", foreign.id)):
            account = BankAccount(
                legal_entity_id=entity, name=f"{TAG} {tag}", bank="B", iban=IBANS[tag]
            )
            session.add(account)
            session.flush()
            BankStatementService(session).import_statement(
                account.id,
                ParsedStatement(
                    format="test",
                    period_from=dt.date(2026, 10, 1),
                    period_to=dt.date(2026, 10, 31),
                    movements=[
                        ParsedMovement(
                            booking_date=dt.date(2026, 10, 1),
                            amount=Decimal("-5.00"),
                            currency="EUR",
                            description=tag,
                        )
                    ],
                ),
            )
            customer = make_customer(session, legal_name=f"{TAG} {tag}", legal_entity_id=entity)
            OrganizationIbanService(session).add(customer.id, IBANS[tag], source="manual")
        session.commit()
    app_url = db_engine.url.set(username=APP_ROLE, password=APP_PASSWORD)
    assert ensure_application_role(db_engine.url, app_url) is True
    app = create_engine(app_url, future=True)
    try:
        yield World(app, studio.id, foreign.id)
    finally:
        app.dispose()
        with factory() as session:
            for model in (StatementMovement, BankMovement, BankStatement):
                session.execute(delete(model))
            session.execute(delete(OrganizationIban))
            session.execute(delete(BankAccount))
            session.execute(delete(Organization).where(Organization.legal_name.like(f"{TAG} %")))
            session.execute(delete(LegalEntity).where(LegalEntity.id == foreign.id))
            remove_legal_entity(session, inserted)
            session.commit()


def _scoped(world: World, role: Role, *legal_entities: UUID) -> Session:
    session = session_factory(world.app)()
    bind_scope(
        session, Actor(id=uuid4(), type="user", role=role, legal_entities=tuple(legal_entities))
    )
    return session


def _counts(session: Session) -> list[int]:
    return [
        int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608
        for table in TABLES
    ]


def test_a_scoped_member_sees_the_bank_data_of_their_legal_entity_alone(world: World) -> None:
    with _scoped(world, "collaborator", world.foreign) as session:
        assert _counts(session) == [1, 1, 1, 1, 1]
        assert (
            session.execute(text("SELECT iban FROM bank_accounts")).scalar_one()
            == (IBANS["foreign"])
        )
    with _scoped(world, "collaborator", world.studio, world.foreign) as session:
        assert _counts(session) == [2, 2, 2, 2, 2]
    with _scoped(world, "collaborator") as session:
        assert _counts(session) == [0, 0, 0, 0, 0]


def test_an_unscoped_member_sees_both(world: World) -> None:
    session = session_factory(world.app)()
    bind_scope(session, Actor(id=uuid4(), type="user", role="collaborator"))
    with session:
        assert _counts(session) == [2, 2, 2, 2, 2]


def test_a_session_with_nothing_bound_sees_no_bank_data(world: World) -> None:
    with session_factory(world.app)() as session:
        assert _counts(session) == [0, 0, 0, 0, 0]
