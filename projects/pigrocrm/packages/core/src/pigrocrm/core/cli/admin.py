"""`createadmin` and `resetpassword`: the operator's way into an installation."""

import getpass
import sys

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.cli.spaces_walk import disposed
from pigrocrm.core.config import Settings
from pigrocrm.core.db import create_engine_from_settings, session_factory
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.errors import DomainError
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository


def _read_password(prompt: str) -> str:
    """From the terminal when there is one, hidden; from stdin when there is not.

    `getpass` needs a tty and raises `EOFError` without one -- which is what happens
    inside `docker compose exec` without `-t`, and in any shell that pipes into this.
    The non-tty branch reads one line, so `echo "$PW" | pigrocrm resetpassword --email x`
    works from a script the operator controls; the password still never appears on a
    command line or in `ps`."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    return sys.stdin.readline().rstrip("\n")


def _read_new_password(prompt: str) -> str | None:
    """The password to set, asked twice on a terminal. `None` when the two differ."""
    password = _read_password(prompt)
    if sys.stdin.isatty() and password != getpass.getpass("Conferma password: "):
        print("Le password non coincidono.", file=sys.stderr)
        return None
    return password


def createadmin(settings: Settings, email: str | None, nome: str | None) -> int:
    """Bootstrap the first administrator. There is no default account and no known
    default password — the direct lesson from the previous system's hardcoded credentials.

    On the application role (`PIGROCRM_DATABASE_URL`, `NOBYPASSRLS`), so the session binds
    the system actor's scope first: the first azienda's row policy reads the scope and an
    unbound session sees no azienda and may write none."""
    email = email or input("Email: ").strip()
    nome = nome or input("Nome: ").strip()
    password = _read_new_password("Password: ")
    if password is None:
        return 1

    with (
        disposed(create_engine_from_settings(settings)) as engine,
        session_factory(engine)() as session,
    ):
        bind_scope(session, Actor.system())
        try:
            user = UserService(session).create(
                UserCreate(email=email, password=password, nome=nome, role="admin"),
                Actor.system(),
            )
        except DomainError as exc:
            # A short password is the single most likely first mistake a new operator
            # will make with this tool; a raw traceback here is a bad first impression.
            print(exc.message, file=sys.stderr)
            return 1
        legal_entity_created = _bootstrap_root_legal_entity(session, nome)
    print(f"Creato amministratore {user.email}")
    if legal_entity_created:
        print(f"Creata l'azienda predefinita «{nome}»: si rinomina da Impostazioni → Aziende")
    return 0


def _bootstrap_root_legal_entity(session: Session, nome: str) -> bool:
    """The root installation's first azienda, written with its first admin.

    The root is not in the registry, so `ensure_defaults` never reaches it, and the API
    updates an azienda by id and creates none (REB-615): a root migrated from an empty
    database would have no issuer to configure from Impostazioni. The first admin's own
    name is the row's, as a space's name is at provisioning; a root that already has its
    azienda keeps it, whoever is created after.

    Insert-only, never through `upsert_default`: two `createadmin` runs racing on an
    empty root would both pass the count, and the second's upsert would rename the
    first's row. An insert can only be refused: the partial unique index on
    `predefinita` turns the second into an `IntegrityError`, rolled back here with its
    savepoint, and the first admin's row stands. No timeline entry: the row is the
    installation's, not a person's edit."""
    repo = LegalEntityRepository(session)
    if repo.count():
        return False
    try:
        with session.begin_nested():
            repo.add(LegalEntity(legal_name=nome, predefinita=True, active=True))
    except IntegrityError:
        session.rollback()
        return False
    session.commit()
    return True


def resetpassword(settings: Settings, email: str | None) -> int:
    """`pigrocrm resetpassword --email chi@dove.it`: a new password for an account that
    exists. The product has no e-mail flow for this on purpose; the operator at the
    server is the reset."""
    email = email or input("Email: ").strip()
    password = _read_new_password("Nuova password: ")
    if password is None:
        return 1

    with (
        disposed(create_engine_from_settings(settings)) as engine,
        session_factory(engine)() as session,
    ):
        try:
            user = UserService(session).reset_password(email, password, Actor.system())
        except DomainError as exc:
            print(exc.message, file=sys.stderr)
            return 1
    print(f"Password aggiornata per {user.email}")
    return 0
