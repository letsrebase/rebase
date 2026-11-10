"""REB-773: a customer has an owner, and the migration gave the existing ones one.

The owner is who the reminders about a customer's invoices and contracts go to. It
defaults to whoever creates the customer; naming somebody else, changing it and clearing
it are an admin's; and a customer that existed before the column takes the owner of its
open deals (the rule the reminders spec of 2026-10-26 §4 gave for the derivation).
"""

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from fakes.contacts import points_in
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.errors import NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.organizations.schemas import OrganizationCreate, OrganizationUpdate
from pigrocrm.core.organizations.service import OrganizationService

CORE_ROOT = Path(__file__).resolve().parents[1]


def _user(session: Session, *, role: str = "collaborator", active: bool = True) -> User:
    user = User(
        email=f"u-{uuid4()}@example.test",
        password_hash="x",
        name="Persona",
        role=role,
        active=active,
    )
    session.add(user)
    session.flush()
    return user


def _actor(user: User) -> Actor:
    return Actor(id=user.id, type="user", role=user.role)  # type: ignore[arg-type]


def test_a_customer_created_by_a_member_is_owned_by_that_member(db_session: Session) -> None:
    member = _user(db_session)

    created = OrganizationService(db_session).create(
        OrganizationCreate(legal_name="ACME Srl"), _actor(member)
    )

    assert created.owner_id == member.id


def test_a_customer_created_by_a_job_has_no_owner(db_session: Session) -> None:
    created = OrganizationService(db_session).create(
        OrganizationCreate(legal_name="ACME Srl"), Actor.system()
    )

    assert created.owner_id is None


def test_a_member_cannot_hand_a_new_customer_to_somebody_else(db_session: Session) -> None:
    member, other = _user(db_session), _user(db_session)

    with pytest.raises(PermissionDenied):
        OrganizationService(db_session).create(
            OrganizationCreate(legal_name="ACME Srl", owner_id=other.id), _actor(member)
        )

    # Naming yourself is not handing it to anybody.
    own = OrganizationService(db_session).create(
        OrganizationCreate(legal_name="ACME Srl", owner_id=member.id), _actor(member)
    )
    assert own.owner_id == member.id


def test_an_admin_can_create_a_customer_for_somebody_else(db_session: Session) -> None:
    admin, other = _user(db_session, role="admin"), _user(db_session)

    created = OrganizationService(db_session).create(
        OrganizationCreate(legal_name="ACME Srl", owner_id=other.id), _actor(admin)
    )

    assert created.owner_id == other.id


def test_an_admin_changes_the_owner_and_a_member_cannot(db_session: Session) -> None:
    admin = _user(db_session, role="admin")
    member, other = _user(db_session), _user(db_session)
    service = OrganizationService(db_session)
    created = service.create(OrganizationCreate(legal_name="ACME Srl"), _actor(member))

    with pytest.raises(PermissionDenied):
        service.update(created.id, OrganizationUpdate(owner_id=other.id), _actor(member))
    assert service.get(created.id, _actor(member)).owner_id == member.id

    moved = service.update(created.id, OrganizationUpdate(owner_id=other.id), _actor(admin))
    assert moved.owner_id == other.id

    cleared = service.update(created.id, OrganizationUpdate(owner_id=None), _actor(admin))
    assert cleared.owner_id is None


def test_clearing_the_owner_is_an_admins_too(db_session: Session) -> None:
    member = _user(db_session)
    service = OrganizationService(db_session)
    created = service.create(OrganizationCreate(legal_name="ACME Srl"), _actor(member))

    with pytest.raises(PermissionDenied):
        service.update(created.id, OrganizationUpdate(owner_id=None), _actor(member))


def test_an_update_that_does_not_mention_the_owner_leaves_it_and_asks_nobody(
    db_session: Session,
) -> None:
    member = _user(db_session)
    service = OrganizationService(db_session)
    created = service.create(OrganizationCreate(legal_name="ACME Srl"), _actor(member))

    renamed = service.update(
        created.id, OrganizationUpdate(contact_points=points_in(phone="02 1234567")), _actor(member)
    )
    again = service.update(created.id, OrganizationUpdate(owner_id=member.id), _actor(member))

    assert renamed.owner_id == member.id
    assert again.owner_id == member.id


def test_the_owner_must_be_an_active_user_that_exists(db_session: Session) -> None:
    admin = _user(db_session, role="admin")
    gone = _user(db_session, active=False)
    service = OrganizationService(db_session)

    with pytest.raises(NotFound):
        service.create(OrganizationCreate(legal_name="A", owner_id=uuid4()), _actor(admin))
    with pytest.raises(ValidationFailed) as excinfo:
        service.create(OrganizationCreate(legal_name="B", owner_id=gone.id), _actor(admin))
    assert excinfo.value.details["field"] == "owner_id"


# -- the migration ---------------------------------------------------------------------


def _config(url: str) -> Config:
    config = Config(str(CORE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(CORE_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_the_migration_gives_a_customer_the_owner_of_its_open_deals(
    pigrocrm_postgres: Any,
) -> None:
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        engine = create_engine(url)
        try:
            _check_backfill(engine, _config(url))
        finally:
            engine.dispose()


def _check_backfill(migration_engine: Engine, config: Config) -> None:
    upgrade(config, "0052")

    ids = {name: uuid4() for name in ("a", "b", "c", "stage_open", "stage_won")}
    users = {name: uuid4() for name in ("one", "two", "three")}
    organizations = {name: uuid4() for name in ("only_open", "majority", "tie", "none", "won_only")}
    with migration_engine.begin() as connection:
        for name, user_id in users.items():
            connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, nome, ruolo, attivo, "
                    "digest_settimanale, ambito_limitato) VALUES (:id, :email, 'x', :nome, "
                    "'collaboratore', true, true, false)"
                ),
                {"id": user_id, "email": f"{name}@example.test", "nome": name},
            )
        for key, type in (("stage_open", "open"), ("stage_won", "won")):
            connection.execute(
                text(
                    "INSERT INTO pipeline_stages (id, nome, posizione, probabilita_default, tipo) "
                    "VALUES (:id, :nome, 0, 10, :tipo)"
                ),
                {"id": ids[key], "nome": key, "tipo": type},
            )
        legal_entity = connection.execute(text("SELECT id FROM emitter_profile LIMIT 1")).scalar()
        if legal_entity is None:
            legal_entity = uuid4()
            connection.execute(
                text(
                    "INSERT INTO emitter_profile (id, nome, ragione_sociale, nazione, predefinita, "
                    "attiva) VALUES (:id, 'Studio', 'Studio', 'IT', true, true)"
                ),
                {"id": legal_entity},
            )
        for name, customer_id in organizations.items():
            connection.execute(
                text(
                    "INSERT INTO customers (id, azienda_id, ragione_sociale, nazione, "
                    "pagamento_fine_mese, custom_fields) "
                    "VALUES (:id, :azienda, :nome, 'IT', false, '{}')"
                ),
                {"id": customer_id, "azienda": legal_entity, "nome": name},
            )

        def deal(customer: str, owner: str | None, stage: str, created: str) -> None:
            connection.execute(
                text(
                    "INSERT INTO deals (id, azienda_id, nome, customer_id, pipeline_stage_id, "
                    "probabilita, owner_id, custom_fields, created_at) VALUES (:id, :azienda, "
                    "'d', :customer, :stage, 10, :owner, '{}', :created)"
                ),
                {
                    "id": uuid4(),
                    "azienda": legal_entity,
                    "customer": organizations[customer],
                    "stage": ids[stage],
                    "owner": users[owner] if owner else None,
                    "created": created,
                },
            )

        deal("only_open", "one", "stage_open", "2026-01-01")
        deal("majority", "one", "stage_open", "2026-01-01")
        deal("majority", "two", "stage_open", "2026-02-01")
        deal("majority", "two", "stage_open", "2026-03-01")
        deal("tie", "three", "stage_open", "2026-02-01")
        deal("tie", "two", "stage_open", "2026-01-01")
        deal("none", None, "stage_open", "2026-01-01")
        deal("won_only", "one", "stage_won", "2026-01-01")

    upgrade(config, "0053")
    with migration_engine.connect() as connection:
        owners: dict[str, UUID | None] = {
            name: connection.execute(
                text("SELECT owner_id FROM customers WHERE id = :id"), {"id": customer_id}
            ).scalar()
            for name, customer_id in organizations.items()
        }
    assert owners == {
        "only_open": users["one"],
        "majority": users["two"],
        "tie": users["two"],
        "none": None,
        "won_only": None,
    }
    downgrade(config, "0052")
    with migration_engine.connect() as connection:
        columns = connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'customers' AND column_name = 'owner_id'"
            )
        ).all()
    assert columns == []
