"""A space gains people by invitation (spec 2026-09-17).

The token half mirrors `test_magic_link.py`'s discipline with one difference the spec
insists on: the magic link folds its failures into one sentence, an invitation names
«scaduto», «revocato» and «già usato» apart, so each gets its own refusal rather than
one shared one. The race half borrows `test_invoice_issue_race.py`'s machinery --
committed rows, two real connections, a `Barrier` before the call -- because the
conditional `UPDATE` that spends the token is exactly the property that test exists to
prove: two racing clicks create exactly one user.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.auth.invitation_models import Invitation
from pigrocrm.core.auth.invitations import (
    INVITATION_TTL_DAYS,
    InvitationExpired,
    InvitationRevoked,
    InvitationService,
    InvitationUnknown,
    InvitationUsed,
    _hash,
)
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import InvitationCreate, UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.db import session_factory
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed

# The system actor cannot send an invitation (`invited_by` needs a person's id), and
# an unverified admin cannot either; so every test invites as a real, password-carrying
# admin created the way `test_magic_link.py` creates its user.
ADMIN = "admin@x.it"
INVITEE = "nuova@x.it"


def _admin(session: Session) -> Actor:
    existing = UserRepository(session).get_by_email(ADMIN)
    if existing is None:
        created = UserService(session).create(
            UserCreate(email=ADMIN, password="lunghissima1", nome="Ada", ruolo="admin"),
            Actor.system(),
        )
        return Actor(id=created.id, type="user", role="admin")
    return Actor(id=existing.id, type="user", role="admin")


def _invite(
    session: Session, email: str = INVITEE, nome: str | None = "Luca", ruolo: str = "collaboratore"
) -> tuple[Invitation, str]:
    invite, raw = InvitationService(session).create(
        InvitationCreate(email=email, nome=nome, ruolo=ruolo), _admin(session)
    )
    row = session.get(Invitation, invite.id)
    assert row is not None
    return row, raw


def test_create_writes_the_row_and_answers_a_token(db_session: Session) -> None:
    row, raw = _invite(db_session)
    assert raw and len(raw) >= 32
    assert row.token_hash == _hash(raw)
    assert row.accepted_at is None and row.revoked_at is None
    assert row.ruolo == "collaboratore" and row.nome == "Luca"
    assert row.expires_at > datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS - 1)
    assert row.invited_by == _admin(db_session).id
    # The list reads it back, and the read model never carries the credential.
    listed = InvitationService(db_session).list(_admin(db_session))
    assert [r.id for r in listed] == [row.id]
    assert "token" not in str(listed[0].model_dump())


def test_a_collaboratore_cannot_invite(db_session: Session) -> None:
    UserService(db_session).create(
        UserCreate(
            email="help@x.it", password="lunghissima1", nome="Bruno", ruolo="collaboratore"
        ),
        Actor.system(),
    )
    helper = UserRepository(db_session).get_by_email("help@x.it")
    assert helper is not None
    actor = Actor(id=helper.id, type="user", role="collaboratore")
    with pytest.raises(PermissionDenied):
        InvitationService(db_session).create(
            InvitationCreate(email=INVITEE, nome=None, ruolo="readonly"), actor
        )


def test_an_unverified_admin_cannot_mint_an_invitation(db_session: Session) -> None:
    """`require_verified_identity` (action `"invite_user"`, spec §3): the first admin
    of a space who has never opened their welcome mail has an address nobody proved.
    The same guard `PatService.create` already applies to a token."""
    nobody = UserService(db_session).create(
        UserCreate(email="appena@x.it", password=None, nome="Zero", ruolo="admin"),
        Actor.system(),
    )
    actor = Actor(id=nobody.id, type="user", role="admin")
    with pytest.raises(ValidationFailed) as refused:
        InvitationService(db_session).create(
            InvitationCreate(email=INVITEE, nome=None, ruolo="admin"), actor
        )
    assert "conferma il tuo indirizzo" in refused.value.message


def test_an_active_user_and_a_pending_invitation_are_both_409(db_session: Session) -> None:
    UserService(db_session).create(
        UserCreate(email="attiva@x.it", password="lunghissima1", nome="Già", ruolo="admin"),
        Actor.system(),
    )
    admin = _admin(db_session)
    with pytest.raises(Conflict) as existing:
        InvitationService(db_session).create(
            InvitationCreate(email="attiva@x.it", nome=None, ruolo="admin"), admin
        )
    assert "persona attiva" in existing.value.message
    _invite(db_session)
    with pytest.raises(Conflict) as pending:
        InvitationService(db_session).create(
            InvitationCreate(email=INVITEE.upper(), nome="Luca", ruolo="collaboratore"), admin
        )
    assert "invito in attesa" in pending.value.message
    # The uppercase retry addressed the same row, not a second one.
    assert len(InvitationService(db_session).list(admin)) == 1


def test_an_expired_invitation_is_rewritten_in_place_not_duplicated(db_session: Session) -> None:
    """Spec §2: the partial index still claims an expired row, so the service must
    overwrite it (new token, new week) rather than answer 409 or insert a second."""
    row, stale = _invite(db_session)
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    with pytest.raises(InvitationExpired):
        InvitationService(db_session).peek(stale, spazio="x")
    invitation_id = row.id
    again, fresh = InvitationService(db_session).create(
        InvitationCreate(email=INVITEE, nome=None, ruolo="readonly"), _admin(db_session)
    )
    assert again.id == invitation_id
    assert fresh != stale
    assert row.ruolo == "readonly" and row.nome is None
    assert row.expires_at > datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS - 1)
    assert db_session.scalar(select(Invitation)) is row  # still one row, not two


def test_resend_kills_the_old_token(db_session: Session) -> None:
    row, stale = _invite(db_session)
    after, fresh = InvitationService(db_session).resend(row.id, _admin(db_session))
    assert fresh != stale
    assert after.id == row.id
    with pytest.raises(InvitationUnknown):
        InvitationService(db_session).peek(stale, spazio="x")
    peek = InvitationService(db_session).peek(fresh, spazio="studio")
    assert peek.spazio == "studio" and peek.invitato_da == "Ada" and peek.nome == "Luca"
    # A peek is not a spend.
    assert row.accepted_at is None


def test_revoke_stops_acceptance_and_ends_the_row(db_session: Session) -> None:
    row, raw = _invite(db_session)
    admin = _admin(db_session)
    InvitationService(db_session).revoke(row.id, admin)
    with pytest.raises(InvitationRevoked):
        InvitationService(db_session).accept(raw, None)
    # Nothing left to revoke or resend: the terminal row answers 404 to both.
    with pytest.raises(NotFound):
        InvitationService(db_session).revoke(row.id, admin)
    with pytest.raises(NotFound):
        InvitationService(db_session).resend(row.id, admin)
    # The address is free again, and the list stopped showing it.
    assert InvitationService(db_session).list(admin) == []
    _invite(db_session)


def test_the_three_dead_states_answer_three_ways(db_session: Session) -> None:
    """The oracle argument lives in the spec; what belongs in a test is that the
    three are *distinct* types with distinct sentences, not one folded `None`."""
    _, raw = _invite(db_session)
    service = InvitationService(db_session)
    assert isinstance(raw, str)
    with pytest.raises(InvitationUnknown):
        service.accept("", None)
    with pytest.raises(InvitationUnknown):
        service.accept("non-esiste", None)
    expired, expired_raw = _invite(db_session, email="scaduta@x.it")
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    with pytest.raises(InvitationExpired):
        service.accept(expired_raw, None)
    revoked, revoked_raw = _invite(db_session, email="revocata@x.it")
    service.revoke(revoked.id, _admin(db_session))
    with pytest.raises(InvitationRevoked):
        service.accept(revoked_raw, None)
    _, used_raw = _invite(db_session, email="usata@x.it")
    service.accept(used_raw, "Usata")
    with pytest.raises(InvitationUsed):
        service.accept(used_raw, "Usata")


def test_accept_creates_the_verified_user_and_audits_both_sides(db_session: Session) -> None:
    row, raw = _invite(db_session, email="Entrante@X.it", ruolo="readonly")
    user = InvitationService(db_session).accept(raw, None)
    assert user.email == "entrante@x.it"
    assert user.ruolo == "readonly" and user.attivo and user.nome == "Luca"
    created = db_session.get(User, user.id)
    assert created is not None
    assert created.password_hash is None
    assert created.email_verificata_il is not None  # the click proved the address
    # The invitation's own lifecycle and the account's, both audited, neither leaking
    # the token: `created`/`accepted` carry the email and the role only.
    invitation_events = db_session.scalars(
        select(Activity)
        .where(Activity.entity_type == "invitation")
        .order_by(Activity.id)  # ids are UUIDv7: time-ordered
    ).all()
    assert [a.kind for a in invitation_events] == ["created", "accepted"]
    user_events = db_session.scalars(
        select(Activity)
        .where(Activity.entity_type == "user", Activity.entity_id == user.id)
        .order_by(Activity.id)
    ).all()
    kinds = [a.kind for a in user_events]
    assert "created" in kinds and "invited" in kinds
    invited = next(a for a in user_events if a.kind == "invited")
    assert invited.payload == {"invited_by": str(_admin(db_session).id)}
    assert row.accepted_at is not None


def test_accept_needs_a_name_when_the_invitation_carried_none(db_session: Session) -> None:
    _, raw = _invite(db_session, nome=None)
    service = InvitationService(db_session)
    with pytest.raises(ValidationFailed):
        service.accept(raw, "   ")
    user = service.accept(raw, "  Nome Ricavato  ")
    assert user.nome == "Nome Ricavato"


def test_accepting_twice_creates_one_user(db_session: Session) -> None:
    _, raw = _invite(db_session)
    InvitationService(db_session).accept(raw, None)
    with pytest.raises(InvitationUsed):
        InvitationService(db_session).accept(raw, None)
    assert UserRepository(db_session).get_by_email(INVITEE) is not None


# --- the race, on two real connections -----------------------------------------------
#
# The transactional `db_session` cannot host it: both threads would share one
# snapshot and one savepoint. Same shape as `test_invoice_issue_race.py`, including
# the cleanup these tests' committed rows need (the fixture's rollback never sees
# them). Deleting `users` by email is safe in dependency order: `invitations`
# references the admin, and is deleted first.


def _wipe(session: Session, *emails: str) -> None:
    session.execute(Invitation.__table__.delete())
    for email in (*emails, ADMIN):
        session.execute(User.__table__.delete().where(User.__table__.c.email == email))
    session.commit()


def test_two_racing_accepts_create_exactly_one_user(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = session_factory(db_engine)
    with factory() as setup:
        row, raw = _invite(setup)
        invitation_id = row.id
        setup.commit()

    both_ready = Barrier(2)
    outcomes: list[bool] = []

    def accept_once() -> None:
        with factory() as session:
            both_ready.wait(timeout=30)
            try:
                InvitationService(session).accept(raw, None)
            except Exception:
                outcomes.append(False)
                return
            outcomes.append(True)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: accept_once(), range(2)))
        assert sorted(outcomes) == [False, True], "the same invitation was spent twice"
        with factory() as reader:
            users = reader.scalars(select(User).where(User.email == INVITEE)).all()
            assert len(users) == 1
            spent = reader.get(Invitation, invitation_id)
            assert spent is not None and spent.accepted_at is not None
    finally:
        with factory() as cleaner:
            _wipe(cleaner, INVITEE)


def test_the_partial_unique_index_refuses_two_open_invitations(db_engine) -> None:  # type: ignore[no-untyped-def]
    """The service's read-then-write cannot cover a race (two requests can both pass
    the SELECT); the index is the last authority, and this proves it exists as
    declared: unique over `lower(email)`, *excluding* terminal rows. Two open rows
    must conflict; an open row beside an accepted one for the same address must not.
    Raw inserts rather than the service: this tests the index, not the checks above
    it, and the service's own read would refuse the duplicate before the database
    ever saw it."""
    factory = session_factory(db_engine)
    try:
        with factory() as session:
            admin = _admin(session)
            assert admin.id is not None
            soon = datetime.now(UTC) + timedelta(days=1)
            session.add(
                Invitation(
                    email="dup@x.it",
                    nome=None,
                    ruolo="admin",
                    token_hash="a" * 64,
                    invited_by=admin.id,
                    expires_at=soon,
                )
            )
            session.flush()
            session.add(
                Invitation(
                    email="Dup@X.it",
                    nome=None,
                    ruolo="admin",
                    token_hash="b" * 64,
                    invited_by=admin.id,
                    expires_at=soon,
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
        with factory() as session:
            admin = _admin(session)
            assert admin.id is not None
            soon = datetime.now(UTC) + timedelta(days=1)
            session.add(
                Invitation(
                    email="finita@x.it",
                    nome=None,
                    ruolo="admin",
                    token_hash="c" * 64,
                    invited_by=admin.id,
                    expires_at=soon,
                    accepted_at=datetime.now(UTC),
                )
            )
            session.flush()
            session.add(
                Invitation(
                    email="finita@x.it",
                    nome=None,
                    ruolo="admin",
                    token_hash="d" * 64,
                    invited_by=admin.id,
                    expires_at=soon,
                )
            )
            session.flush()  # no IntegrityError: the accepted row is outside the predicate
    finally:
        with factory() as cleaner:
            _wipe(cleaner, "dup@x.it", "finita@x.it")
