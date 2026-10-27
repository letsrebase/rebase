from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth import tokens as tokens_module
from pigrocrm.core.auth.invitations import InvitationService
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.pat_models import PersonalAccessToken
from pigrocrm.core.auth.pat_service import INVALID_TOKEN, PAT_PREFIX, PatService
from pigrocrm.core.auth.schemas import InvitationCreate, UserCreate, UserUpdate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.auth.tokens import (
    ALGORITHM,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ScopedAdmin,
    ValidationFailed,
)

SETTINGS = Settings(jwt_secret="test-secret-not-for-production-and-32-chars-long")
ADMIN = Actor(id=None, type="system", role="admin")


def _make_user(db_session: Session, email: str = "tok@test.it"):
    return UserService(db_session).create(
        UserCreate(email=email, password="supersegreta1", nome="Tok", ruolo="admin"), ADMIN
    )


def _forge(claims: dict[str, object]) -> str:
    """A token signed with the real secret, whose claims are not necessarily what
    `issue_access_token`/`issue_refresh_token` would ever produce -- for exercising
    what `decode_token` does with a well-signed but malformed payload."""
    return jwt.encode(claims, SETTINGS.jwt_secret, algorithm=ALGORITHM)


def _future_exp() -> int:
    return int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())


def test_access_token_round_trips_user_and_role(db_session: Session) -> None:
    user = _make_user(db_session)
    payload = decode_token(
        issue_access_token(user.id, "admin", SETTINGS), SETTINGS, expected_type="access"
    )
    assert payload.sub == user.id
    assert payload.role == "admin"
    assert payload.type == "access"


def test_refresh_token_cannot_be_used_as_an_access_token(db_session: Session) -> None:
    user = _make_user(db_session)
    refresh = issue_refresh_token(user.id, SETTINGS)
    with pytest.raises(ValidationFailed) as exc:
        decode_token(refresh, SETTINGS, expected_type="access")
    assert exc.value.details["field"] == "token"


def test_two_refresh_tokens_issued_in_the_same_instant_are_still_different(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the jti claim existed, two tokens issued within the same wall-clock
    second were byte-for-byte identical: iat/exp truncate to whole seconds and HS256
    over identical claims with the same key is deterministic. Freezing "now" removes
    the timing luck needed to hit that window by chance, so this proves the actual
    guarantee -- a fresh random jti every time -- instead of relying on a coincidence
    of scheduling to reproduce it."""
    user = _make_user(db_session)
    # Real "now" (unpatched), not a hardcoded date: jwt.decode below checks `exp`
    # against the real clock, which this patch does not touch -- only
    # `issue_refresh_token`'s own notion of "now" is frozen.
    frozen_instant = datetime.now(UTC)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return frozen_instant

    monkeypatch.setattr(tokens_module, "datetime", _FrozenDateTime)

    token_a = issue_refresh_token(user.id, SETTINGS)
    token_b = issue_refresh_token(user.id, SETTINGS)

    assert token_a != token_b
    payload_a = decode_token(token_a, SETTINGS, expected_type="refresh")
    payload_b = decode_token(token_b, SETTINGS, expected_type="refresh")
    assert payload_a.exp == payload_b.exp, "the freeze must actually be in effect"
    assert payload_a.jti is not None
    assert payload_b.jti is not None
    assert payload_a.jti != payload_b.jti


def test_expired_token_is_rejected(db_session: Session) -> None:
    user = _make_user(db_session)
    expired = Settings(jwt_secret=SETTINGS.jwt_secret, access_token_minutes=-1)
    token = issue_access_token(user.id, "admin", expired)
    with pytest.raises(ValidationFailed):
        decode_token(token, SETTINGS, expected_type="access")


def test_token_signed_with_another_secret_is_rejected(db_session: Session) -> None:
    user = _make_user(db_session)
    token = issue_access_token(
        user.id, "admin", Settings(jwt_secret="a-different-secret-that-is-also-32-chars")
    )
    with pytest.raises(ValidationFailed):
        decode_token(token, SETTINGS, expected_type="access")


def test_access_token_expiry_matches_settings(db_session: Session) -> None:
    user = _make_user(db_session)
    payload = decode_token(
        issue_access_token(user.id, "admin", SETTINGS), SETTINGS, expected_type="access"
    )
    expected = datetime.now(UTC) + timedelta(minutes=SETTINGS.access_token_minutes)
    assert abs((payload.exp - expected).total_seconds()) < 5


def test_token_without_sub_is_rejected_not_a_raw_keyerror() -> None:
    """Well-signed (real secret, real algorithm) but missing the "sub" claim --
    decode_token's contract is "return a TokenPayload or raise a domain error,"
    never a bare KeyError from indexing into claims."""
    forged = _forge({"type": "access", "role": "admin", "exp": _future_exp()})
    with pytest.raises(ValidationFailed) as exc:
        decode_token(forged, SETTINGS, expected_type="access")
    assert exc.value.details["field"] == "token"


def test_token_without_exp_is_rejected_not_a_raw_keyerror() -> None:
    """Well-signed but missing "exp". PyJWT only verifies expiry when the claim is
    present, so this reaches TokenPayload construction rather than failing inside
    jwt.decode itself."""
    forged = _forge({"type": "access", "role": "admin", "sub": str(uuid4())})
    with pytest.raises(ValidationFailed) as exc:
        decode_token(forged, SETTINGS, expected_type="access")
    assert exc.value.details["field"] == "token"


def test_token_with_unparseable_sub_is_rejected_not_a_raw_valueerror() -> None:
    """Well-signed, "sub" present, but not a UUID -- decode_token must not let
    uuid.UUID's ValueError escape either."""
    forged = _forge({"type": "access", "role": "admin", "sub": "not-a-uuid", "exp": _future_exp()})
    with pytest.raises(ValidationFailed) as exc:
        decode_token(forged, SETTINGS, expected_type="access")
    assert exc.value.details["field"] == "token"


def test_pat_is_returned_once_and_stored_only_as_a_hash(db_session: Session) -> None:
    user = _make_user(db_session, "pat@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session)

    record, raw = service.create("Claude locale", actor)

    assert raw.startswith(PAT_PREFIX)
    assert len(raw) > 40
    assert record.prefix == raw[: len(PAT_PREFIX) + 8]
    assert not hasattr(record, "token_hash"), "PatRead must never expose the hash"

    stored = service.list(actor)[0]
    assert stored.prefix == record.prefix
    assert raw not in str(stored.model_dump())


def test_pat_resolves_to_an_actor_and_records_last_use(db_session: Session) -> None:
    user = _make_user(db_session, "pat2@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    # Settings declared, not inherited: this repository's own `.env` opens the switch, so
    # a bare `PatService(db_session)` would answer `full_access=True` here and make the
    # assertion below say the opposite of what it claims while still passing. The same
    # trap the API and MCP fixtures were fixed for in 59f4842.
    service = PatService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    _, raw = service.create("Claude locale", actor)

    resolved = service.resolve(raw)
    assert resolved.id == user.id
    assert resolved.type == "mcp", "a PAT identifies an agent, not a browser session"
    assert resolved.role == "admin"
    assert resolved.full_access is False, "the default installation does not opt in"
    assert service.list(actor)[0].last_used_at is not None


def test_a_token_reaches_the_privileged_surface_only_where_it_and_the_space_both_say_so(
    db_session: Session,
) -> None:
    """`full_access` is stamped here or nowhere, from two things (REB-810): the token's own
    flag, which its owner chose, and the space's `mcp_full_access`, the ceiling.

    It is the one place an `mcp` actor is built from a credential, so it is the one place
    that can answer "what may this token do" once rather than at every point of use. A
    check that read the setting where the operation happens would leave the REST adapter
    and the MCP adapter free to disagree -- which is exactly the asymmetry that once let a
    `curl` issue an invoice while the tool was unregistered.

    Asserted against `Settings(_env_file=None, ...)` and never against the ambient process
    settings: the owner of this repository runs with the switch **on**, and a test that
    inherited that would assert the opposite of what it claims on his machine and pass
    anyway.
    """
    user = _make_user(db_session, "pat-switch@test.it")
    actor = Actor(id=user.id, type="user", role="admin")

    chiusa = Settings(_env_file=None)  # type: ignore[call-arg]
    aperta = Settings(_env_file=None, mcp_full_access=True)  # type: ignore[call-arg]

    _, closed = PatService(db_session, aperta).create("Chiacchiere", actor)
    _, open_ = PatService(db_session, aperta).create("Fatturazione", actor, full_access=True)

    # The same user, the same role, the same space: only the token differs.
    assert PatService(db_session, aperta).resolve(closed).full_access is False
    assert PatService(db_session, aperta).resolve(open_).full_access is True
    # And the ceiling wins over a token that asks for more than the space offers.
    assert PatService(db_session, chiusa).resolve(open_).full_access is False


def test_a_token_cannot_be_made_with_what_the_space_does_not_offer(db_session: Session) -> None:
    """Stored closed instead of refused it would be a flag nobody can see doing anything,
    and the day an administrator opens the switch every such token would open with it."""
    user = _make_user(db_session, "pat-ceiling@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    chiusa = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(ValidationFailed) as exc:
        PatService(db_session, chiusa).create("Fatturazione", actor, full_access=True)

    assert exc.value.details["field"] == "full_access"
    assert PatService(db_session, chiusa).list(actor) == []


def test_an_agent_credential_cannot_mint_a_token_that_opens_the_privileged_surface(
    db_session: Session,
) -> None:
    """A token is accepted on every REST route and `POST /api/tokens` is one of them: the
    token a person kept closed would otherwise mint an open one for itself."""
    user = _make_user(db_session, "pat-mint@test.it")
    aperta = Settings(_env_file=None, mcp_full_access=True)  # type: ignore[call-arg]
    service = PatService(db_session, aperta)
    agent = Actor(id=user.id, type="mcp", role="admin")

    with pytest.raises(AgentForbidden):
        service.create("Per me", agent, full_access=True)

    _, raw = service.create("Chiuso", agent)  # the ordinary token is unchanged
    assert raw.startswith(PAT_PREFIX)


def _collaboratore(db_session: Session, email: str):
    return UserService(db_session).create(
        UserCreate(email=email, password="supersegreta1", nome="Colla", ruolo="collaboratore"),
        ADMIN,
    )


def _full_access_entries(db_session: Session, owner_id) -> list[Activity]:
    return list(
        db_session.scalars(
            select(Activity)
            .where(Activity.entity_id == owner_id, Activity.kind == "pat_full_access_changed")
            .order_by(Activity.occurred_at, Activity.id)
        )
    )


def test_the_owner_changes_the_flag_and_every_change_is_recorded(db_session: Session) -> None:
    user = _make_user(db_session, "pat-flag@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    aperta = Settings(_env_file=None, mcp_full_access=True)  # type: ignore[call-arg]
    service = PatService(db_session, aperta)
    record, raw = service.create("Claude", actor)

    opened = service.set_full_access(record.id, True, actor)
    assert opened.full_access is True
    assert service.resolve(raw).full_access is True
    closed = service.set_full_access(record.id, False, actor)
    assert closed.full_access is False
    assert service.resolve(raw).full_access is False

    first, second = _full_access_entries(db_session, user.id)
    assert (first.payload["full_access"], first.payload["full_access_prima"]) == (True, False)
    assert (second.payload["full_access"], second.payload["full_access_prima"]) == (False, True)
    assert first.payload["token_id"] == str(record.id)
    assert (first.actor_id, first.actor_type) == (user.id, "user")
    assert raw not in str([first.payload, second.payload])


def test_setting_the_flag_to_what_it_is_records_nothing(db_session: Session) -> None:
    user = _make_user(db_session, "pat-noop@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session, Settings(_env_file=None, mcp_full_access=True))  # type: ignore[call-arg]
    record, _ = service.create("Claude", actor)

    assert service.set_full_access(record.id, False, actor).full_access is False

    assert _full_access_entries(db_session, user.id) == []


def test_an_administrator_changes_a_colleagues_token_and_the_entry_names_who(
    db_session: Session,
) -> None:
    admin = _make_user(db_session, "pat-admin@test.it")
    colleague = _collaboratore(db_session, "pat-colla@test.it")
    aperta = Settings(_env_file=None, mcp_full_access=True)  # type: ignore[call-arg]
    service = PatService(db_session, aperta)
    record, raw = service.create(
        "Colla", Actor(id=colleague.id, type="user", role="collaboratore"), full_access=True
    )
    admin_actor = Actor(id=admin.id, type="user", role="admin")

    assert [token.owner_nome for token in service.list_team(admin_actor)] == ["Colla"]
    service.set_full_access(record.id, False, admin_actor)

    assert service.resolve(raw).full_access is False
    (entry,) = _full_access_entries(db_session, colleague.id)
    assert entry.actor_id == admin.id, "the owner's timeline names the administrator"
    assert entry.payload["full_access"] is False


def test_a_member_cannot_change_or_list_a_colleagues_tokens(db_session: Session) -> None:
    owner = _collaboratore(db_session, "pat-own@test.it")
    other = _collaboratore(db_session, "pat-other@test.it")
    aperta = Settings(_env_file=None, mcp_full_access=True)  # type: ignore[call-arg]
    service = PatService(db_session, aperta)
    record, _ = service.create("Mio", Actor(id=owner.id, type="user", role="collaboratore"))
    stranger = Actor(id=other.id, type="user", role="collaboratore")

    with pytest.raises(PermissionDenied):
        service.set_full_access(record.id, True, stranger)
    with pytest.raises(PermissionDenied):
        service.list_team(stranger)

    assert _full_access_entries(db_session, owner.id) == []


def test_an_administrator_scoped_to_some_aziende_does_not_administer_tokens(
    db_session: Session,
) -> None:
    """Another member's token is account administration, like changing the member: the
    role is not enough, the whole space has to be in view (spec 2026-10-03 §1.11)."""
    owner = _collaboratore(db_session, "pat-scope-own@test.it")
    service = PatService(db_session, Settings(_env_file=None, mcp_full_access=True))  # type: ignore[call-arg]
    record, _ = service.create("Mio", Actor(id=owner.id, type="user", role="collaboratore"))
    scoped_admin = Actor(id=uuid4(), type="user", role="admin", aziende=())

    with pytest.raises(ScopedAdmin):
        service.set_full_access(record.id, True, scoped_admin)
    with pytest.raises(ScopedAdmin):
        service.list_team(scoped_admin)


def test_a_token_cannot_open_itself_nor_a_sibling(db_session: Session) -> None:
    """The person's decision is not the agent's, whatever the agent's role: this is the
    REST route an open `PATCH` would be reachable by with the token alone."""
    user = _make_user(db_session, "pat-self@test.it")
    aperta = Settings(_env_file=None, mcp_full_access=True)  # type: ignore[call-arg]
    service = PatService(db_session, aperta)
    record, raw = service.create("Chiuso", Actor(id=user.id, type="user", role="admin"))
    agent = service.resolve(raw)

    with pytest.raises(AgentForbidden):
        service.set_full_access(record.id, True, agent)

    assert service.resolve(raw).full_access is False
    assert _full_access_entries(db_session, user.id) == []


def test_the_flag_cannot_be_opened_above_the_ceiling_but_can_always_be_closed(
    db_session: Session,
) -> None:
    user = _make_user(db_session, "pat-above@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    record, _ = PatService(
        db_session,
        Settings(_env_file=None, mcp_full_access=True),  # type: ignore[call-arg]
    ).create("Claude", actor, full_access=True)
    chiusa = PatService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]

    # The administrator closed the switch afterwards: the flag keeps what it was, and the
    # owner can still close it.
    assert chiusa.set_full_access(record.id, False, actor).full_access is False
    with pytest.raises(ValidationFailed):
        chiusa.set_full_access(record.id, True, actor)


def test_a_revoked_token_has_nothing_left_to_decide(db_session: Session) -> None:
    user = _make_user(db_session, "pat-dead@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session, Settings(_env_file=None, mcp_full_access=True))  # type: ignore[call-arg]
    record, _ = service.create("Claude", actor)
    service.revoke(record.id, actor)

    with pytest.raises(Conflict):
        service.set_full_access(record.id, True, actor)

    with pytest.raises(NotFound):
        service.set_full_access(uuid4(), True, actor)


def test_revoked_pat_stops_working(db_session: Session) -> None:
    user = _make_user(db_session, "pat3@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session)
    record, raw = service.create("Da revocare", actor)

    service.revoke(record.id, actor)

    with pytest.raises(ValidationFailed):
        service.resolve(raw)


def test_unknown_pat_is_rejected(db_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        PatService(db_session).resolve("pgc_totally-made-up-token-value-here")


def test_a_user_cannot_revoke_another_users_pat(db_session: Session) -> None:
    owner = _make_user(db_session, "owner@test.it")
    other = _make_user(db_session, "other@test.it")
    service = PatService(db_session)
    record, _ = service.create("Mio", Actor(id=owner.id, type="user", role="admin"))

    with pytest.raises(NotFound):
        service.revoke(record.id, Actor(id=other.id, type="user", role="admin"))


def test_pat_for_deactivated_user_stops_working(db_session: Session) -> None:
    from pigrocrm.core.auth.schemas import UserUpdate

    user = _make_user(db_session, "pat4@test.it")
    # A second active admin: REB-292 refuses to take a space's last one, and this
    # test is about the PAT stopping, not about the guard.
    _make_user(db_session, "pat4-superstite@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    _, raw = PatService(db_session).create("Token", actor)
    UserService(db_session).update(user.id, UserUpdate(attivo=False), ADMIN)

    with pytest.raises(ValidationFailed):
        PatService(db_session).resolve(raw)


def test_unknown_revoked_and_deactivated_user_tokens_are_indistinguishable(
    db_session: Session,
) -> None:
    """Someone holding a leaked PAT must not be able to tell "revoked, dead end"
    apart from "still valid, just needs its user reactivated" -- that distinction
    would tell them whether it is worth pursuing. Same discipline as
    UserService.authenticate's dummy hash, applied to error content rather than
    timing: asserts message and details are identical, not merely that all three
    raise ValidationFailed."""
    from pigrocrm.core.auth.schemas import UserUpdate

    service = PatService(db_session)

    revoked_owner = _make_user(db_session, "revoked-owner@test.it")
    revoked_actor = Actor(id=revoked_owner.id, type="user", role="admin")
    revoked_record, revoked_raw = service.create("Revocato", revoked_actor)
    service.revoke(revoked_record.id, revoked_actor)

    deactivated_owner = _make_user(db_session, "deactivated-owner@test.it")
    deactivated_actor = Actor(id=deactivated_owner.id, type="user", role="admin")
    _, deactivated_raw = service.create("Utente disattivato", deactivated_actor)
    UserService(db_session).update(deactivated_owner.id, UserUpdate(attivo=False), ADMIN)

    unknown_raw = "pgc_this-token-was-never-issued-by-anyone"

    caught: list[ValidationFailed] = []
    for raw in (unknown_raw, revoked_raw, deactivated_raw):
        with pytest.raises(ValidationFailed) as exc:
            service.resolve(raw)
        caught.append(exc.value)

    unknown_err, revoked_err, deactivated_err = caught
    assert revoked_err.message == unknown_err.message
    assert revoked_err.details == unknown_err.details
    assert deactivated_err.message == unknown_err.message, (
        "a deactivated user's token must fail identically to an unknown or revoked "
        "one, or the error itself becomes a way to tell a live token from a dead one"
    )
    assert deactivated_err.details == unknown_err.details


def test_pat_hash_collision_becomes_a_domain_conflict_not_a_raw_integrity_error(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """token_hash is unique=True. A collision on 32 random bytes is astronomically
    unlikely, but if it ever happened the second create() must not leak a raw
    IntegrityError, and the session must stay usable for whatever the caller does
    next -- the same discipline UserService.create applies to the email race."""
    user = _make_user(db_session, "collide@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session)

    monkeypatch.setattr(
        "pigrocrm.core.auth.pat_service.secrets.token_urlsafe", lambda n: "not-random-at-all"
    )
    service.create("Primo", actor)

    with pytest.raises(Conflict):
        service.create("Secondo", actor)

    # The session must still be usable right after -- a leftover PendingRollbackError
    # would blow up on the very next statement issued on it.
    assert len(service.list(actor)) == 1


# --- REB-295: the credential answers with the owner's CURRENT state -----------------
#
# A PAT outlives the session that minted it, so everything it is allowed to do has to
# be decided at every `resolve`, not copied at `create`. Two mechanisms make that true,
# and each gets its own test because they fail differently:
# `populate_existing` on the owner read (a second resolve on the same long-lived
# session must not answer from the identity map), and the deactivation cascade in
# `UserService.update` (a token of a deactivated owner is revoked, in the same
# transaction as the deactivation, not merely refused).


def test_a_second_resolve_on_the_same_session_sees_the_current_role(
    db_session: Session,
) -> None:
    """The `populate_existing` half of REB-295, pinned at the service level.

    `session_factory` sets `expire_on_commit=False`, so the first resolve leaves the
    owner in the identity map and a plain `session.get` on the second would answer from
    that cached copy: on a session shared between two calls (the stdio server's, a
    test's), a demoted admin's token would keep resolving as `admin`. The raw UPDATE is
    what the panel's own service would do through a *different* session; doing it here
    on the same one isolates the caching, not the transaction."""
    user = _make_user(db_session, "fresh-role@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    _, raw = service.create("Claude locale", actor)

    assert service.resolve(raw).role == "admin"
    db_session.execute(
        text("UPDATE users SET ruolo = 'collaboratore' WHERE id = :id"), {"id": str(user.id)}
    )
    db_session.commit()

    assert service.resolve(raw).role == "collaboratore", (
        "the Actor must carry the owner's CURRENT role, not the one cached by the "
        "first resolve on this session"
    )


def test_a_second_resolve_on_the_same_session_refuses_a_deactivation(
    db_session: Session,
) -> None:
    """The other state the identity map could freeze: `attivo`. Same setup as the role
    test, raw UPDATE so the deactivation bypasses the ORM and only the cache can make
    it invisible; the second resolve must raise even though this session still holds a
    snapshot of the owner while active."""
    user = _make_user(db_session, "fresh-attivo@test.it")
    actor = Actor(id=user.id, type="user", role="admin")
    service = PatService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    _, raw = service.create("Claude locale", actor)

    assert service.resolve(raw).id == user.id
    db_session.execute(text("UPDATE users SET attivo = false WHERE id = :id"), {"id": str(user.id)})
    db_session.commit()

    with pytest.raises(ValidationFailed) as refused:
        service.resolve(raw)
    assert refused.value.details["reason"] == INVALID_TOKEN


def test_deactivating_a_user_revokes_their_tokens_in_the_same_transaction(
    db_session: Session,
) -> None:
    """The cascade half of REB-295: the panel's deactivation must not merely make the
    tokens refused on the next call, it must end them. `resolve()` already answers 401
    for an inactive owner, but the rows stayed live underneath -- reactivating the
    account would have silently handed every old token back, including ones the owner
    had lost track of. One `UserService.update` call: the user row and the token rows
    commit together (there is exactly one commit), and each revocation carries its own
    `pat_revoked` audit entry, mirroring what `revoke` does for a single token.

    The already-revoked token keeps its own earlier date: the cascade touches live
    rows only."""
    user = _make_user(db_session, "cascade@test.it")
    # A second active admin: REB-292 refuses to take a space's last one.
    _make_user(db_session, "cascade-superstite@test.it")
    owner_actor = Actor(id=user.id, type="user", role="admin")
    tokens = PatService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    first, _ = tokens.create("Vivace", owner_actor)
    second, raw_second = tokens.create("Anche questo", owner_actor)
    third, _ = tokens.create("Gia spento", owner_actor)
    tokens.revoke(third.id, owner_actor)
    dead_date = db_session.get(PersonalAccessToken, third.id).revoked_at

    UserService(db_session).update(user.id, UserUpdate(attivo=False), ADMIN)

    rows = db_session.scalars(
        select(PersonalAccessToken).where(PersonalAccessToken.user_id == user.id)
    ).all()
    assert all(row.revoked_at is not None for row in rows)
    assert db_session.get(PersonalAccessToken, third.id).revoked_at == dead_date, (
        "the cascade must not rewrite the date of an already-revoked token"
    )
    revoked_ids = {
        str(entry.payload["token_id"])
        for entry in db_session.scalars(
            select(Activity).where(
                Activity.entity_type == "user",
                Activity.entity_id == user.id,
                Activity.kind == "pat_revoked",
            )
        )
    }
    assert revoked_ids == {str(first.id), str(second.id), str(third.id)}
    # And of course the credential is dead: this is the state the HTTP transport will
    # answer 401 from (pinned end-to-end in apps/mcp/tests/test_http_transport.py).
    with pytest.raises(ValidationFailed):
        tokens.resolve(raw_second)


def test_a_deactivation_that_changed_nothing_revokes_nothing(
    db_session: Session,
) -> None:
    """The cascade keys to the real delta, not to the patch. A user made inactive by
    some path outside `UserService.update` (the raw UPDATE here stands for it, exactly
    as the caching tests use it) still has live tokens; a PATCH that restates
    `attivo: false` changes nothing, and a no-op edit must not burn credentials -- the
    same line `update` already draws for the `updated` audit entry."""
    user = _make_user(db_session, "noop-deactivate@test.it")
    owner_actor = Actor(id=user.id, type="user", role="admin")
    tokens = PatService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    _, raw = tokens.create("Vivace", owner_actor)
    db_session.execute(text("UPDATE users SET attivo = false WHERE id = :id"), {"id": str(user.id)})
    db_session.commit()

    UserService(db_session).update(user.id, UserUpdate(attivo=False), ADMIN)

    assert (
        db_session.scalar(
            select(PersonalAccessToken.revoked_at).where(PersonalAccessToken.user_id == user.id)
        )
        is None
    )
    # Still refused at resolve, of course -- the owner-inactive check (tests above),
    # just not by a revocation nobody decided today.
    with pytest.raises(ValidationFailed):
        tokens.resolve(raw)


def test_a_demotion_leaves_the_tokens_live_at_the_new_role(
    db_session: Session,
) -> None:
    """A demotion is not a deactivation. The card REB-278 drew the line for hub's admin
    tokens is "stop resolving"; here the answer is "keep resolving, as less", because a
    PigroCRM token is scoped to its owner's account, not to the admin role. The
    cascade deliberately fires on `attivo` only."""
    user = _make_user(db_session, "demote@test.it")
    _make_user(db_session, "demote-superstite@test.it")
    owner_actor = Actor(id=user.id, type="user", role="admin")
    tokens = PatService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    _, raw = tokens.create("Claude locale", owner_actor)

    UserService(db_session).update(user.id, UserUpdate(ruolo="collaboratore"), ADMIN)

    assert (
        db_session.scalar(
            select(PersonalAccessToken.revoked_at).where(PersonalAccessToken.user_id == user.id)
        )
        is None
    )
    resolved = tokens.resolve(raw)
    assert resolved.role == "collaboratore"


def test_a_revoked_invitation_that_never_became_a_user_has_no_token_to_revoke(
    db_session: Session,
) -> None:
    """The invitation path needs nothing from the cascade, and this test says why
    rather than leaving the next reader to re-derive it: a PAT's `user_id` is a real
    FK to `users`, so a token can only exist for an account that was created. A
    revoked invitation never created one (its raw link is a different table and a
    different hash, and `PatService` can never resolve it), so there is no credential
    left behind to revoke."""
    inviter = _make_user(db_session, "inviter@test.it")
    invites = InvitationService(db_session)
    invite, link = invites.create(
        InvitationCreate(email="mai-accettato@test.it"),
        Actor(id=inviter.id, type="user", role="admin"),
    )
    invites.revoke(invite.id, Actor(id=inviter.id, type="user", role="admin"))

    assert db_session.scalar(select(func.count()).select_from(User)) == 1
    with pytest.raises(ValidationFailed):
        PatService(db_session, Settings(_env_file=None)).resolve(link)  # type: ignore[call-arg]
