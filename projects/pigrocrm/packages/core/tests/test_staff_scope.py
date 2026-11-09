"""A scoped administrator reaches the staff of their own legal entities and no other's
(REB-977, spec 2026-11-09, staff, payroll and expenses, § 4: «admins whose scope covers the
legal entity»).

Proven as the application role, as `test_due_items_scope.py` does: the suite's user is a
superuser and a superuser is never subject to a policy. Committed rows, since that role
connects on an engine of its own.
"""

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, create_engine, delete, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.staff.models import PayrollSettings, StaffMember
from pigrocrm.core.staff.schemas import EmployeeCreate, PayDayWrite, StaffListQuery
from pigrocrm.core.staff.service import StaffService

TAG = "STAFFSCOPE"
APP_ROLE = "pigrocrm_app_test"
APP_PASSWORD = "pigrocrm_app_test"
D = dt.date


class World(NamedTuple):
    engine: Engine
    app: Engine
    studio: UUID
    other: UUID
    studio_member: UUID
    other_member: UUID


@pytest.fixture
def world(db_engine: Engine) -> Iterator[World]:
    factory = session_factory(db_engine)
    with factory() as session:
        inserted = committed_default_legal_entity(session)
        entities = []
        for tag in ("studio", "other"):
            entity = LegalEntity(name=f"{TAG} {tag}", legal_name=f"{TAG} {tag}", country="IT")
            session.add(entity)
            session.flush()
            session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-standard"))
            entities.append(entity.id)
        session.flush()
        service = StaffService(session)
        members = [
            service.create_staff_member(
                EmployeeCreate(
                    legal_entity_id=entity,
                    name=f"{TAG} {index}",
                    start_date=D(2026, 1, 1),
                    gross_annual_pay=Decimal("20000.00"),
                    monthly_payments=13,
                ),
                Actor.system(),
            ).id
            for index, entity in enumerate(entities)
        ]
        service.set_payroll_pay_day(
            entities[0], PayDayWrite(pay_day=27, pay_month_offset=0), Actor.system()
        )
        app_url = db_engine.url.set(username=APP_ROLE, password=APP_PASSWORD)
        assert ensure_application_role(db_engine.url, app_url) is True
        ids = World(db_engine, create_engine(app_url, future=True), *entities, *members)
    try:
        yield ids
    finally:
        ids.app.dispose()
        with factory() as session:
            session.execute(delete(StaffMember).where(StaffMember.name.like(f"{TAG} %")))
            session.execute(
                delete(PayrollSettings).where(
                    PayrollSettings.legal_entity_id.in_([ids.studio, ids.other])
                )
            )
            session.execute(
                delete(FiscalProfile).where(
                    FiscalProfile.legal_entity_id.in_([ids.studio, ids.other])
                )
            )
            session.execute(delete(LegalEntity).where(LegalEntity.id.in_([ids.studio, ids.other])))
            remove_legal_entity(session, inserted)
            session.commit()


def _scoped(world: World, role: str, *legal_entities: UUID) -> tuple[Session, Actor]:
    session = session_factory(world.app)()
    actor = Actor(
        id=uuid4(),
        type="user",
        role=role,  # type: ignore[arg-type]
        legal_entities=tuple(legal_entities),
    )
    bind_scope(session, actor)
    return session, actor


def _count(session: Session, table: str) -> int:
    return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def test_a_scoped_admin_lists_and_reads_the_staff_of_their_own_legal_entity_alone(
    world: World,
) -> None:
    session, admin = _scoped(world, "admin", world.other)
    with session:
        assert _count(session, "staff_members") == 1
        listed = StaffService(session).list_staff(StaffListQuery(), admin).items
        assert [m.id for m in listed] == [world.other_member]
        service = StaffService(session)
        with pytest.raises(NotFound):
            service.get_staff_member(world.studio_member, admin)
        assert service.get_staff_member(world.other_member, admin).name == f"{TAG} 1"
        # The legal entity's pay day is out of sight too.
        with pytest.raises(NotFound):
            service.get_payroll_pay_day(world.studio, admin)
        assert service.get_payroll_pay_day(world.other, admin).pay_day is None


def test_a_scoped_admin_cannot_write_into_another_legal_entity(world: World) -> None:
    session, admin = _scoped(world, "admin", world.other)
    with session:
        service = StaffService(session)
        with pytest.raises(NotFound):
            service.create_staff_member(
                EmployeeCreate(
                    legal_entity_id=world.studio,
                    name=f"{TAG} intruder",
                    start_date=D(2026, 1, 1),
                    gross_annual_pay=Decimal("1000.00"),
                    monthly_payments=13,
                ),
                admin,
            )
        with pytest.raises(NotFound):
            service.set_payroll_pay_day(
                world.studio, PayDayWrite(pay_day=1, pay_month_offset=0), admin
            )
    with session_factory(world.engine)() as plain:
        assert _count(plain, "staff_members") == 2


def test_an_unscoped_admin_and_a_session_with_nothing_bound(world: World) -> None:
    session = session_factory(world.app)()
    bind_scope(session, Actor(id=uuid4(), type="user", role="admin"))
    with session:
        assert _count(session, "staff_members") == 2
        assert _count(session, "payroll_settings") == 1
    with session_factory(world.app)() as unbound:
        assert _count(unbound, "staff_members") == 0
        assert _count(unbound, "payroll_settings") == 0


def test_the_policies_put_the_all_fast_path_first(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        policies = {
            row.tablename: (row.qual, row.with_check)
            for row in connection.execute(
                text(
                    "SELECT tablename, qual, with_check FROM pg_policies "
                    "WHERE policyname = 'legal_entity_scope' "
                    "AND tablename IN ('staff_members', 'payroll_settings')"
                )
            )
        }
    assert set(policies) == {"staff_members", "payroll_settings"}
    for using, check in policies.values():
        assert "".join(using.split()).startswith("(scope_all()OR")
        assert check == using
