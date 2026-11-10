"""A scoped administrator reaches the stored exports of their own legal entities and no other's,
and the same goes for the activities the exports write (REB-1012, accountant export design § 3
and § 5: «an admin whose scope covers the entity»; the export of every entity is an unscoped
admin's).

Proven as the application role, as `test_staff_scope.py` does: the suite's user is a superuser
and a superuser is never subject to a policy. Committed rows, since that role connects on an
engine of its own.
"""

import datetime as dt
from collections.abc import Iterator
from typing import NamedTuple
from uuid import UUID, uuid4

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.models import AccountantExportRow
from pigrocrm.core.accountant_export.parts import Period
from pigrocrm.core.accountant_export.service import AccountantExportService
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import get_settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage.local import LocalFileStorage

TAG = "EXPORTSCOPE"
APP_ROLE = "pigrocrm_app_test"
APP_PASSWORD = "pigrocrm_app_test"
Q1 = Period(dt.date(2026, 1, 1), dt.date(2026, 3, 31))
NOW = dt.datetime(2026, 4, 2, 9, 30, tzinfo=dt.UTC)


class World(NamedTuple):
    app: Engine
    studio: UUID
    other: UUID
    studio_export: UUID
    other_export: UUID
    all_export: UUID


@pytest.fixture
def world(db_engine: Engine, tmp_path: object) -> Iterator[World]:
    factory = session_factory(db_engine)
    with factory() as session:
        inserted = committed_default_legal_entity(session)
        entities = []
        for tag in ("studio", "other"):
            entity = LegalEntity(name=f"{TAG} {tag}", legal_name=f"{TAG} {tag}", country="IT")
            session.add(entity)
            session.flush()
            entities.append(entity.id)
        session.commit()
        service = AccountantExportService(session, LocalFileStorage(tmp_path), get_settings())  # type: ignore[arg-type]
        exports = [
            service.build_accountant_export(Q1, Actor.system(), entity, now=NOW).export.id
            for entity in entities
        ]
        exports.append(
            service.build_accountant_export(
                Q1, Actor.system(), all_entities=True, now=NOW
            ).export.id
        )
        app_url = db_engine.url.set(username=APP_ROLE, password=APP_PASSWORD)
        assert ensure_application_role(db_engine.url, app_url) is True
        ids = World(create_engine(app_url, future=True), *entities, *exports)
    try:
        yield ids
    finally:
        ids.app.dispose()
        with factory() as session:
            session.execute(delete(Activity).where(Activity.entity_type == "accountant_export"))
            session.execute(delete(AccountantExportRow))
            session.execute(delete(LegalEntity).where(LegalEntity.id.in_([ids.studio, ids.other])))
            remove_legal_entity(session, inserted)
            session.commit()


def _scoped(world: World, *legal_entities: UUID) -> Session:
    session = session_factory(world.app)()
    actor = Actor(id=uuid4(), type="user", role="admin", legal_entities=tuple(legal_entities))
    bind_scope(session, actor)
    return session


def _unscoped(world: World) -> Session:
    session = session_factory(world.app)()
    bind_scope(session, Actor(id=uuid4(), type="user", role="admin"))
    return session


def _entries(session: Session) -> set[UUID]:
    return set(
        session.scalars(
            Activity.__table__.select()
            .with_only_columns(Activity.entity_id)
            .where(  # type: ignore[attr-defined]
                Activity.entity_type == "accountant_export"
            )
        )
    )


def _rows(session: Session) -> set[UUID]:
    return set(
        session.scalars(
            AccountantExportRow.__table__.select().with_only_columns(AccountantExportRow.id)
        )
    )  # type: ignore[attr-defined]


def test_a_scoped_admin_sees_the_exports_and_the_trace_of_their_own_entity_alone(
    world: World,
) -> None:
    with _scoped(world, world.studio) as session:
        assert _rows(session) == {world.studio_export}
        assert _entries(session) == {world.studio_export}
    with _scoped(world, world.other) as session:
        assert _rows(session) == {world.other_export}
        assert _entries(session) == {world.other_export}


def test_an_unscoped_admin_sees_every_export_including_the_one_of_every_entity(
    world: World,
) -> None:
    expected = {world.studio_export, world.other_export, world.all_export}
    with _unscoped(world) as session:
        assert _rows(session) == expected
        assert _entries(session) == expected
