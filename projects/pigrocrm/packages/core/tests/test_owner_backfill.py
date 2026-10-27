"""REB-797: customers and deals take the owner their creation entry names, once.

The rule is the card's: a live record without an owner is given the user in its earliest
`created` entry; a record created by the system, by an account named generic, by a
deactivated user, or with no creation entry is listed and left alone; nothing already
owned is touched; a second run finds nothing to do.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import NamedTuple
from uuid import UUID

import pytest
from fakes.azienda_fixtures import committed_default_azienda
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import pigrocrm.core.cli as cli
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.owner_backfill import apply_owner_backfill, plan_owner_backfill
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.tenants.service import migrate_to_head

_serial = count()


def _user(session: Session, nome: str, *, attivo: bool = True) -> User:
    user = User(
        email=f"{nome.lower().replace(' ', '.')}-{next(_serial)}@example.test",
        password_hash="x",
        nome=nome,
        ruolo="admin",
        attivo=attivo,
    )
    session.add(user)
    session.flush()
    return user


def _created(
    session: Session,
    entity: str,
    entity_id: UUID,
    actor: User | None,
    *,
    actor_type: str = "mcp",
    at: datetime | None = None,
) -> None:
    session.add(
        Activity(
            entity_type=entity,
            entity_id=entity_id,
            kind="created",
            actor_id=actor.id if actor else None,
            actor_type=actor_type,
            payload={},
            occurred_at=at or datetime.now(UTC),
        )
    )
    session.flush()


def _customer(
    session: Session, nome: str, creator: User | None, *, actor_type: str = "mcp"
) -> Customer:
    customer = Customer(ragione_sociale=nome, nazione="IT", custom_fields={})
    session.add(customer)
    session.flush()
    _created(session, "customer", customer.id, creator, actor_type=actor_type)
    return customer


def _stage(session: Session) -> PipelineStage:
    stage = PipelineStage(
        nome=f"Fase {next(_serial)}", posizione=0, probabilita_default=10, tipo="open"
    )
    session.add(stage)
    session.flush()
    return stage


def _deal(session: Session, nome: str, customer: Customer, creator: User | None) -> Deal:
    deal = Deal(
        nome=nome,
        customer_id=customer.id,
        pipeline_stage_id=_stage(session).id,
        probabilita=10,
        custom_fields={},
    )
    session.add(deal)
    session.flush()
    _created(session, "deal", deal.id, creator)
    return deal


def test_a_record_takes_the_user_who_created_it(db_session: Session) -> None:
    ada = _user(db_session, "Ada Lovelace")
    customer = _customer(db_session, "ACME Srl", ada)
    deal = _deal(db_session, "Sito ACME", customer, ada)

    plan = plan_owner_backfill(db_session)
    changed = apply_owner_backfill(db_session, plan)

    assert {(item.entity, item.label, item.owner_name) for item in plan.assignments} == {
        ("customer", "ACME Srl", "Ada Lovelace"),
        ("deal", "Sito ACME", "Ada Lovelace"),
    }
    assert changed == 2
    db_session.refresh(customer)
    db_session.refresh(deal)
    assert customer.owner_id == ada.id
    assert deal.owner_id == ada.id


def test_the_earliest_creation_entry_decides(db_session: Session) -> None:
    ada = _user(db_session, "Ada Lovelace")
    grace = _user(db_session, "Grace Hopper")
    customer = _customer(db_session, "ACME Srl", ada)
    _created(db_session, "customer", customer.id, grace, at=datetime.now(UTC) + timedelta(hours=1))

    plan = plan_owner_backfill(db_session)

    assert [(item.label, item.owner_name) for item in plan.assignments] == [
        ("ACME Srl", "Ada Lovelace")
    ]


def test_what_cannot_be_decided_is_listed_and_not_written(db_session: Session) -> None:
    generic = _user(db_session, "rebase")
    gone = _user(db_session, "Ex Collega", attivo=False)
    by_generic = _customer(db_session, "Generico Srl", generic)
    by_system = _customer(db_session, "Sistema Srl", None, actor_type="system")
    by_gone = _customer(db_session, "Ex Srl", gone)
    no_entry = Customer(ragione_sociale="Senza storia Srl", nazione="IT", custom_fields={})
    db_session.add(no_entry)
    db_session.flush()

    plan = plan_owner_backfill(db_session, generic_emails=[generic.email.upper()])
    changed = apply_owner_backfill(db_session, plan)

    assert changed == 0
    assert plan.assignments == []
    assert {(item.label, item.reason, item.creator_name) for item in plan.listed} == {
        ("Generico Srl", "generic_account", "rebase"),
        ("Sistema Srl", "no_user", None),
        ("Ex Srl", "inactive_creator", "Ex Collega"),
        ("Senza storia Srl", "no_creation_entry", None),
    }
    for customer in (by_generic, by_system, by_gone, no_entry):
        db_session.refresh(customer)
        assert customer.owner_id is None


def test_without_naming_the_generic_account_its_records_are_set(db_session: Session) -> None:
    generic = _user(db_session, "rebase")
    _customer(db_session, "Generico Srl", generic)

    plan = plan_owner_backfill(db_session)

    assert [(item.label, item.owner_name) for item in plan.assignments] == [
        ("Generico Srl", "rebase")
    ]


def test_an_owner_already_set_and_a_deleted_record_are_left_alone(db_session: Session) -> None:
    ada = _user(db_session, "Ada Lovelace")
    grace = _user(db_session, "Grace Hopper")
    owned = _customer(db_session, "Assegnato Srl", ada)
    owned.owner_id = grace.id
    deleted = _customer(db_session, "Archiviato Srl", ada)
    deleted.deleted_at = datetime.now(UTC)
    db_session.flush()

    plan = plan_owner_backfill(db_session)

    assert plan.assignments == []
    assert plan.listed == []
    assert plan.already_owned == {"customer": 1, "deal": 0}
    apply_owner_backfill(db_session, plan)
    db_session.refresh(owned)
    assert owned.owner_id == grace.id


def test_a_second_run_finds_nothing_and_the_timeline_says_where_the_owner_came_from(
    db_session: Session,
) -> None:
    ada = _user(db_session, "Ada Lovelace")
    customer = _customer(db_session, "ACME Srl", ada)

    assert apply_owner_backfill(db_session, plan_owner_backfill(db_session)) == 1
    second = plan_owner_backfill(db_session)

    assert second.assignments == []
    assert apply_owner_backfill(db_session, second) == 0
    entries = (
        db_session.query(Activity)
        .filter(Activity.entity_id == customer.id, Activity.kind == "updated")
        .all()
    )
    assert [(e.actor_type, e.actor_id, e.payload) for e in entries] == [
        ("system", None, {"changed": ["owner_id"], "source": "REB-797"})
    ]


def test_a_plan_does_not_overwrite_an_owner_set_after_it_was_made(db_session: Session) -> None:
    ada = _user(db_session, "Ada Lovelace")
    grace = _user(db_session, "Grace Hopper")
    customer = _customer(db_session, "ACME Srl", ada)
    plan = plan_owner_backfill(db_session)

    customer.owner_id = grace.id
    db_session.flush()

    assert apply_owner_backfill(db_session, plan) == 0
    db_session.refresh(customer)
    assert customer.owner_id == grace.id


# -- the command -------------------------------------------------------------------------

ROOT_DB = "prova_owner_backfill_radice"


class Root(NamedTuple):
    settings: Settings
    generic_email: str


@pytest.fixture(scope="module")
def root_settings(db_engine: Engine) -> Iterator[Settings]:
    shared_url = db_engine.url.render_as_string(hide_password=False)
    root_url = make_url(shared_url).set(database=ROOT_DB)
    helper = Settings(database_url=shared_url, _env_file=None)  # type: ignore[call-arg]
    configured = Settings(
        database_url=root_url.render_as_string(hide_password=False),
        _env_file=None,  # type: ignore[call-arg]
    )
    create_database_if_missing(helper, root_url)
    try:
        migrate_to_head(configured, configured.database_url)
        engine = create_engine(configured.database_url, future=True)
        try:
            with session_factory(engine)() as session:
                committed_default_azienda(session, nome="Radice")
                session.commit()
        finally:
            engine.dispose()
        yield configured
    finally:
        drop_database(helper, root_url)


@pytest.fixture
def root(root_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Root]:
    """The root installation with two customers: Ada's, and one made by the generic account."""
    monkeypatch.setattr(cli, "get_settings", lambda: root_settings)
    engine = create_engine(root_settings.database_url, future=True)
    try:
        with session_factory(engine)() as session:
            ada = _user(session, "Ada Lovelace")
            generic = _user(session, "rebase")
            _customer(session, "ACME Srl", ada)
            _customer(session, "Generico Srl", generic)
            session.commit()
            generic_email = generic.email
        yield Root(root_settings, generic_email)
        with engine.begin() as connection:
            connection.execute(text("delete from activities"))
            connection.execute(text("delete from customers"))
            connection.execute(text("delete from users"))
    finally:
        engine.dispose()


def _owners(settings: Settings) -> dict[str, str | None]:
    engine = create_engine(settings.database_url, future=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "select c.ragione_sociale, u.nome from customers c "
                    "left join users u on u.id = c.owner_id"
                )
            ).all()
    finally:
        engine.dispose()
    return {row[0]: row[1] for row in rows}


def test_the_command_writes_nothing_without_apply(
    root: Root, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["owner-backfill", "--slug", "root", "--generic-account", root.generic_email]

    assert cli.main(argv) == 0

    out = capsys.readouterr().out
    assert "root: cliente «ACME Srl» -> Ada Lovelace" in out
    assert "«Generico Srl» lasciato senza responsabile: generic_account (creato da rebase)" in out
    assert "root: cliente: 1 da assegnare, 1 lasciati, 0 già con responsabile" in out
    assert "prova, non scritto niente" in out
    assert _owners(root.settings) == {"ACME Srl": None, "Generico Srl": None}


def test_the_command_applies_with_apply_and_a_second_run_has_nothing_left(
    root: Root, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["owner-backfill", "--slug", "root", "--generic-account", root.generic_email, "--apply"]

    assert cli.main(argv) == 0
    assert "scritti 1" in capsys.readouterr().out
    assert _owners(root.settings) == {"ACME Srl": "Ada Lovelace", "Generico Srl": None}

    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "root: cliente: 0 da assegnare, 1 lasciati, 1 già con responsabile" in out
    assert "scritti 0" in out


def test_a_change_committed_between_the_plan_and_the_apply_is_respected(root: Root) -> None:
    """The plan loaded the rows; another session then commits an owner on one customer and
    archives the other. Only a locked read that refreshes what the plan loaded sees it."""
    engine = create_engine(root.settings.database_url, future=True)
    try:
        with session_factory(engine)() as session:
            plan = plan_owner_backfill(session, generic_emails=[root.generic_email])
            assert [item.label for item in plan.assignments] == ["ACME Srl"]
            with engine.begin() as other:
                other.execute(
                    text(
                        "update customers set owner_id = "
                        "(select id from users where nome = 'rebase') "
                        "where ragione_sociale = 'ACME Srl'"
                    )
                )
            assert apply_owner_backfill(session, plan) == 0
        with engine.begin() as other:
            other.execute(text("update customers set owner_id = null"))
        with session_factory(engine)() as session:
            plan = plan_owner_backfill(session)
            with engine.begin() as other:
                other.execute(text("update customers set deleted_at = now()"))
            assert apply_owner_backfill(session, plan) == 0
        assert _owners(root.settings) == {"ACME Srl": None, "Generico Srl": None}
        with engine.connect() as connection:
            updated = connection.execute(
                text("select count(*) from activities where kind = 'updated'")
            ).scalar()
        assert updated == 0
    finally:
        engine.dispose()


def test_an_unknown_space_is_one_line_on_stderr(
    root_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: root_settings)

    assert cli.main(["owner-backfill", "--slug", "non-esiste"]) == 1

    assert "non-esiste: non nel registro" in capsys.readouterr().err
