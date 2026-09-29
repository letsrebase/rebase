import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.db import session_factory
from pigrocrm.core.gmail.models import GoogleAccount, GoogleOAuthState
from pigrocrm.core.gmail.schemas import (
    REQUESTED_SCOPES,
    SCOPE_EMAIL,
    SCOPE_READONLY,
    SCOPE_SEND,
    GoogleAccountRead,
)


def _user(session: Session, email: str) -> User:
    user = User(email=email, nome="Tester", password_hash="x", ruolo="admin", attivo=True)
    session.add(user)
    session.flush()
    return user


def _account(session: Session, user: User, **overrides: object) -> GoogleAccount:
    defaults: dict[str, object] = {
        "user_id": user.id,
        "google_sub": f"sub-{user.email}",
        "email_address": user.email,
        "refresh_token_ciphertext": b"\x01\x02",
        "refresh_token_nonce": b"\x03" * 12,
        "scopes_granted": list(REQUESTED_SCOPES),
    }
    account = GoogleAccount(**{**defaults, **overrides})  # type: ignore[arg-type]
    session.add(account)
    session.flush()
    return account


def test_one_mailbox_per_user_is_enforced_by_the_database(db_session: Session) -> None:
    """Two mailboxes double the relevance question without anyone having asked it."""
    user = _user(db_session, "one@example.it")
    _account(db_session, user)
    with pytest.raises(IntegrityError):
        _account(db_session, user, google_sub="sub-other", email_address="other@example.it")
    db_session.rollback()


def test_status_and_granted_scopes_are_two_separate_facts(db_session: Session) -> None:
    """Spec 5.1: a valid credential missing a scope is healthy; it is the *feature*
    that is unavailable. Conflating them is the contradiction every other OAuth
    integration falls into.

    The row is expired and re-read rather than inspected in place: asserting on the
    list that was just handed to the constructor would prove only that Python can hold
    a list. What is under test is that `status` defaults to `active` on the way *in*
    (nothing here passes it) while a partial `scopes_granted` survives the JSONB round
    trip unchanged -- so a narrower grant never quietly downgrades the credential.
    """
    user = _user(db_session, "partial@example.it")
    account = _account(db_session, user, scopes_granted=["openid", SCOPE_EMAIL, SCOPE_SEND])
    db_session.expire(account)

    assert account.status == "active"
    assert SCOPE_READONLY not in account.scopes_granted
    assert SCOPE_SEND in account.scopes_granted


def test_bodies_are_stored_by_default_and_the_switch_is_per_account(db_session: Session) -> None:
    """Two accounts, one of each, because "per account" is the claim: a global default
    that ignored the column would pass a single-row test."""
    default_user = _user(db_session, "bodies-on@example.it")
    off_user = _user(db_session, "bodies-off@example.it")
    stored = _account(db_session, default_user)
    not_stored = _account(db_session, off_user, gmail_store_bodies=False)
    db_session.expire(stored)
    db_session.expire(not_stored)

    assert stored.gmail_store_bodies is True
    assert not_stored.gmail_store_bodies is False


def test_a_disconnected_account_is_a_row_that_still_exists(db_session: Session) -> None:
    """`disconnected_at` is nullable and unset on a fresh connection: the history a
    disconnection leaves behind (spec 5.3) needs the row to survive the disconnection,
    so this is a timestamp and not a `DELETE`."""
    user = _user(db_session, "disconnect@example.it")
    account = _account(db_session, user)
    db_session.expire(account)

    assert account.disconnected_at is None
    assert account.connected_at is not None


def test_no_representation_of_an_account_can_carry_the_sealed_token(
    db_session: Session,
) -> None:
    """The ciphertext and the nonce are the two halves of the stored credential, and
    `GoogleAccountRead` is what the settings page, the API and `describe_gmail_account`
    all render. Neither half may appear in any of them -- not in the schema's fields,
    not in its JSON, and not in the ORM object's `repr`, which is what a traceback
    frame and a pytest failure dump both print.
    """
    user = _user(db_session, "leak@example.it")
    ciphertext = b"\xde\xad\xbe\xef" * 8
    nonce = b"\xca\xfe" * 6
    account = _account(
        db_session, user, refresh_token_ciphertext=ciphertext, refresh_token_nonce=nonce
    )

    read = GoogleAccountRead.model_validate(account)
    assert "refresh_token_ciphertext" not in GoogleAccountRead.model_fields
    assert "refresh_token_nonce" not in GoogleAccountRead.model_fields

    rendered = f"{read.model_dump_json()} {read!r} {account!r}"
    assert ciphertext.hex() not in rendered
    assert nonce.hex() not in rendered
    assert str(ciphertext) not in rendered
    assert str(nonce) not in rendered
    # The bytes are not JSON-serialisable at all if they somehow reached the schema,
    # so also assert on the parsed document: a field added later that base64-encodes
    # them would slip past a substring check on the raw hex.
    assert set(json.loads(read.model_dump_json())) == set(GoogleAccountRead.model_fields)


def test_a_state_jti_can_only_exist_once(db_session: Session) -> None:
    """The registry that makes the JWT's `jti` single-use for real rather than in
    principle. It also holds the PKCE code_verifier, which must stay server-side: put
    it in the signed state and the browser can read it, which cancels PKCE."""
    user = _user(db_session, "state@example.it")
    expires = datetime.now(UTC) + timedelta(minutes=5)
    db_session.add(
        GoogleOAuthState(jti="j1", code_verifier="v" * 43, user_id=user.id, expires_at=expires)
    )
    db_session.flush()
    db_session.add(
        GoogleOAuthState(jti="j1", code_verifier="w" * 43, user_id=user.id, expires_at=expires)
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_fresh_state_row_has_not_been_consumed(db_session: Session) -> None:
    user = _user(db_session, "fresh@example.it")
    state = GoogleOAuthState(
        jti=str(uuid4()),
        code_verifier="v" * 43,
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(state)
    db_session.flush()
    assert state.consumed_at is None


# --- the single-use claim, under real concurrency ------------------------------------


@pytest.fixture
def committed_state(db_engine: Engine) -> Iterator[tuple[UUID, str]]:
    """A user and an unconsumed state, actually committed.

    The `db_session` fixture wraps every test in a transaction it rolls back, which is
    exactly what makes it useless here: two connections cannot race over a row that
    only one of them can see. So this writes for real and deletes afterwards, and the
    `finally` runs even when the assertions fail -- a leaked row would otherwise
    poison the session-scoped database for every test that follows.
    """
    factory = session_factory(db_engine)
    jti = f"jti-{uuid4()}"
    with factory() as session:
        user = User(
            email=f"race-{uuid4()}@example.it",
            nome="Race",
            password_hash="x",
            ruolo="admin",
            attivo=True,
        )
        session.add(user)
        session.flush()
        session.add(
            GoogleOAuthState(
                jti=jti,
                code_verifier="v" * 43,
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        session.commit()
        user_id = user.id
    try:
        yield user_id, jti
    finally:
        with factory() as session:
            # `google_oauth_states.user_id` is ON DELETE CASCADE, so deleting the user
            # takes the state with it whatever state the test left it in.
            session.execute(delete(User).where(User.id == user_id))
            session.commit()


def test_two_simultaneous_redemptions_of_one_state_produce_exactly_one_success(
    db_engine: Engine, committed_state: tuple[UUID, str]
) -> None:
    """ "Single-use" is a claim about concurrency, so it is tested as one.

    Two connections issue the same conditional update at the same moment -- the shape
    `GmailRepository.consume_state` will use in B1-6. Postgres serialises them on the
    row lock: the loser re-evaluates its `WHERE` against the winner's committed row,
    finds `consumed_at` no longer NULL, and updates nothing. Exactly one authorisation
    code is therefore exchangeable per state, even if a browser fires the callback
    twice or an attacker replays it against the real one.

    What makes this test bite rather than merely test Postgres: it is built from the
    model's own attributes. Drop `consumed_at`, make it NOT NULL with a `now()` default,
    or lose the unique `jti`, and this stops compiling or stops discriminating.
    """
    _, jti = committed_state
    barrier = Barrier(2)

    def claim() -> int:
        with db_engine.connect() as connection, connection.begin():
            # Both threads arrive here with an open transaction before either issues
            # its UPDATE, so the race is real and not an artefact of thread start-up
            # order.
            barrier.wait(timeout=10)
            result = connection.execute(
                update(GoogleOAuthState)
                .where(
                    GoogleOAuthState.jti == jti,
                    GoogleOAuthState.consumed_at.is_(None),
                )
                .values(consumed_at=datetime.now(UTC))
            )
            return result.rowcount

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            future.result(timeout=30) for future in [pool.submit(claim) for _ in range(2)]
        )

    assert outcomes == [0, 1], (
        f"a state was redeemed {sum(outcomes)} times; single-use means exactly once"
    )
