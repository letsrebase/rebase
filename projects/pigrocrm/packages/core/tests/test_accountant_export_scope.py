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

from pigrocrm.core.accountant_export.models import (
    AccountantExportRow,
    ExportDriveWrite,
    ExportShareLink,
    ExportShareOpening,
)
from pigrocrm.core.accountant_export.parts import Period
from pigrocrm.core.accountant_export.service import AccountantExportService
from pigrocrm.core.accountant_export.sharing import ExportShareDoor, ExportShareService
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, get_settings
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
            session.execute(delete(ExportShareOpening))
            session.execute(delete(ExportShareLink))
            session.execute(delete(ExportDriveWrite))
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


def test_the_links_and_openings_of_an_export_are_seen_by_whoever_sees_the_export(
    world: World, db_engine: Engine, tmp_path: object
) -> None:
    settings = Settings(_env_file=None, public_url="https://pigro.example")  # type: ignore[call-arg]
    storage = LocalFileStorage(tmp_path)  # type: ignore[arg-type]
    with session_factory(db_engine)() as session:
        service = ExportShareService(session, settings)
        links = {
            export: service.create_export_share_link(export, Actor.system(), now=NOW)
            for export in (world.studio_export, world.other_export, world.all_export)
        }
        door = ExportShareDoor(session, storage, settings)
        for link in links.values():
            door.open(link.url.split("?t=", 1)[1], 1, "203.0.113.7", now=NOW)

    def seen(session: Session, model: type[ExportShareLink] | type[ExportShareOpening]) -> int:
        return len(session.scalars(model.__table__.select()).all())  # type: ignore[attr-defined]

    with _scoped(world, world.studio) as session:
        assert (seen(session, ExportShareLink), seen(session, ExportShareOpening)) == (1, 1)
    with _scoped(world, world.other) as session:
        assert (seen(session, ExportShareLink), seen(session, ExportShareOpening)) == (1, 1)
    with _unscoped(world) as session:
        assert (seen(session, ExportShareLink), seen(session, ExportShareOpening)) == (3, 3)
    # A session with no scope at all reads nothing: the default is closed.
    with session_factory(world.app)() as session:
        assert (seen(session, ExportShareLink), seen(session, ExportShareOpening)) == (0, 0)


def test_the_public_door_opens_one_link_as_the_application_role_with_no_session_of_a_person(
    world: World, db_engine: Engine, tmp_path: object
) -> None:
    settings = Settings(_env_file=None, public_url="https://pigro.example")  # type: ignore[call-arg]
    storage = LocalFileStorage(tmp_path)  # type: ignore[arg-type]
    with session_factory(db_engine)() as session:
        created = ExportShareService(session, settings).create_export_share_link(
            world.all_export, Actor.system(), now=NOW
        )
    token = created.url.split("?t=", 1)[1]

    with session_factory(world.app)() as session:
        door = ExportShareDoor(session, storage, settings)
        assert door.describe(token, now=NOW).company == "Tutte le aziende"
        assert door.open(token, 1, "203.0.113.7", now=NOW).data.startswith(b"PK")
    with _unscoped(world) as session:
        link = session.get(ExportShareLink, created.id)
        assert link is not None and link.download_count == 1
