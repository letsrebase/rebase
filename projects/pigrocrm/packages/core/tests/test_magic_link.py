"""A link by mail as a way in (spec 2026-09-12 §6.2)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.magic_link import MagicLinkService, _hash
from pigrocrm.core.auth.magic_models import MagicLinkToken
from pigrocrm.core.auth.refresh_models import RefreshToken
from pigrocrm.core.auth.refresh_service import RefreshTokenService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import UserCreate, UserUpdate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


def _user(session: Session, email: str = "ada@x.it") -> UserService:
    service = UserService(session)
    service.create(
        UserCreate(email=email, password="lunghissima1", nome="Ada", ruolo="admin"),
        Actor.system(),
    )
    return service


def _alive(session: Session) -> list[RefreshToken]:
    return list(
        session.scalars(select(RefreshToken).where(RefreshToken.consumed_at.is_(None))).all()
    )


def test_request_answers_a_token_for_a_known_address_and_none_otherwise(
    db_session: Session,
) -> None:
    _user(db_session)
    links = MagicLinkService(db_session, SETTINGS)
    raw = links.request("Ada@X.it")
    assert raw is not None and len(raw) >= 32
    assert links.request("nessuno@x.it") is None
    row = db_session.scalar(select(MagicLinkToken))
    assert row is not None and row.token_hash == _hash(raw) and row.used_at is None
    assert row.expires_at > datetime.now(UTC) + timedelta(minutes=14)


def test_enter_spends_the_token_once_and_verifies_the_address(db_session: Session) -> None:
    _user(db_session)
    links = MagicLinkService(db_session, SETTINGS)
    raw = links.request("ada@x.it")
    assert raw is not None
    user = links.enter(raw)
    assert user is not None and user.email == "ada@x.it"
    assert links.enter(raw) is None  # spent
    row = UserRepository(db_session).get_by_email("ada@x.it")
    assert row is not None and row.email_verificata_il is not None


def test_the_first_entry_revokes_the_sessions_issued_before_it(db_session: Session) -> None:
    _user(db_session)
    user = UserRepository(db_session).get_by_email("ada@x.it")
    assert user is not None
    RefreshTokenService(db_session).issue(user.id, SETTINGS)  # the session a signup opens
    links = MagicLinkService(db_session, SETTINGS)
    raw = links.request("ada@x.it")
    assert raw is not None and links.enter(raw) is not None
    assert _alive(db_session) == []
    # A later entry is an ordinary login: it revokes nothing.
    RefreshTokenService(db_session).issue(user.id, SETTINGS)
    raw2 = links.request("ada@x.it")
    assert raw2 is not None and links.enter(raw2) is not None
    assert len(_alive(db_session)) == 1


def test_an_expired_or_unknown_or_inactive_link_answers_none(db_session: Session) -> None:
    service = _user(db_session)
    links = MagicLinkService(db_session, SETTINGS)
    assert links.enter("") is None
    assert links.enter("non-esiste") is None
    raw = links.request("ada@x.it")
    assert raw is not None
    row = db_session.scalar(select(MagicLinkToken))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()
    assert links.enter(raw) is None
    raw2 = links.request("ada@x.it")
    assert raw2 is not None
    # REB-292: Ada is the space's only admin, and even the system actor may not
    # take the last one. A survivor first; this test is about the link, not the guard.
    service.create(
        UserCreate(email="superstite@x.it", password=None, nome="S", ruolo="admin"),
        Actor.system(),
    )
    user = UserRepository(db_session).get_by_email("ada@x.it")
    assert user is not None
    service.update(user.id, UserUpdate(attivo=False), Actor.system())
    assert links.enter(raw2) is None
    # Spent all the same: reactivating the account does not revive the link.
    service.update(user.id, UserUpdate(attivo=True), Actor.system())
    assert links.enter(raw2) is None


def test_request_sweeps_the_spent_and_expired_rows_of_that_user(db_session: Session) -> None:
    _user(db_session)
    links = MagicLinkService(db_session, SETTINGS)
    first = links.request("ada@x.it")
    assert first is not None and links.enter(first) is not None
    links.request("ada@x.it")
    rows = db_session.scalars(select(MagicLinkToken)).all()
    assert len(rows) == 1 and rows[0].used_at is None
