import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

import pigrocrm.core.auth.refresh_service as refresh_service_module
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.refresh_models import RefreshToken
from pigrocrm.core.auth.refresh_service import REFRESH_GRACE_SECONDS, RefreshTokenService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import UserCreate, UserUpdate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.auth.tokens import decode_token
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.errors import ValidationFailed

SETTINGS = Settings(jwt_secret="test-secret-not-for-production-and-32-chars-long")
ADMIN = Actor(id=None, type="system", role="admin")


def _make_user(db_session: Session, email: str = "refresh@test.it"):
    return UserService(db_session).create(
        UserCreate(email=email, password="supersegreta1", nome="Refresh", ruolo="admin"), ADMIN
    )


def test_issuing_twice_produces_two_different_tokens(db_session: Session) -> None:
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)

    token_a = service.issue(user.id, SETTINGS)
    token_b = service.issue(user.id, SETTINGS)

    assert token_a != token_b


def test_consuming_marks_the_row_consumed(db_session: Session) -> None:
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)
    token = service.issue(user.id, SETTINGS)
    jti = decode_token(token, SETTINGS, expected_type="refresh").jti
    assert jti is not None

    service.consume(jti, user.id)

    record = db_session.execute(select(RefreshToken).where(RefreshToken.jti == jti)).scalar_one()
    assert record.consumed_at is not None


def test_consuming_an_unknown_jti_is_rejected(db_session: Session) -> None:
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)

    with pytest.raises(ValidationFailed):
        service.consume(uuid4(), user.id)


def test_consuming_the_same_jti_twice_is_rejected(db_session: Session) -> None:
    """This is the exact bug the review caught: rotation must actually rotate. Before
    the fix, nothing stopped the same refresh token from being consumed over and over,
    which is what let a stolen token keep working after the legitimate user rotated
    past it."""
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)
    token = service.issue(user.id, SETTINGS)
    jti = decode_token(token, SETTINGS, expected_type="refresh").jti
    assert jti is not None

    service.consume(jti, user.id)

    with pytest.raises(ValidationFailed):
        service.consume(jti, user.id)


def test_reusing_a_consumed_token_revokes_every_other_valid_token_for_that_user(
    db_session: Session,
) -> None:
    """The standard response to replay: a consumed token being presented again is the
    signal that it was stolen, not that the legitimate user is confused. Rewarding that
    replay with a fresh pair of tokens would leave the thief inside, so the whole
    session family for this user is killed, not just the one token replayed."""
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)

    token_a = service.issue(user.id, SETTINGS)  # e.g. session on device A
    token_b = service.issue(user.id, SETTINGS)  # e.g. session on device B, still unused
    jti_a = decode_token(token_a, SETTINGS, expected_type="refresh").jti
    jti_b = decode_token(token_b, SETTINGS, expected_type="refresh").jti
    assert jti_a is not None and jti_b is not None

    service.consume(jti_a, user.id)  # normal rotation of session A

    with pytest.raises(ValidationFailed):
        service.consume(jti_a, user.id)  # jti_a replayed -- triggers mass revocation

    # jti_b was never itself replayed, and was never even used -- but it must now be
    # dead too, because the replay above is exactly the case this design cannot ignore.
    with pytest.raises(ValidationFailed):
        service.consume(jti_b, user.id)


def test_revoke_all_valid_select_is_ordered_by_id(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies the deadlock fix mechanically instead of trying to reproduce a real
    deadlock: Postgres returns a stable row order for a small, freshly populated
    table, so the natural race is not reliably reproducible in a test (this project's
    own 15 runs and the reviewer's 25 never triggered it on the real code) -- but the
    reviewer *did* force a genuine deadlock with raw SQL, two connections updating the
    same two rows in opposite order, 3 times out of 3, and confirmed zero deadlocks
    with both in ascending id order. Without `order_by(id)`, `_revoke_all_valid`
    UPDATEs whatever order `session.dirty` happens to hand SQLAlchemy; two concurrent
    replays revoking an overlapping set of sibling rows could then acquire those
    rows' locks in different orders on each side. A fixed order removes that
    possibility outright. This test does not exercise concurrency at all -- it
    inspects the actual SQL `_revoke_all_valid` sends, which is a deterministic,
    non-flaky way to pin down that the fix is really in the generated query, not just
    in the source."""
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)
    token_a = service.issue(user.id, SETTINGS)
    service.issue(user.id, SETTINGS)  # sibling, so there is something to order
    jti_a = decode_token(token_a, SETTINGS, expected_type="refresh").jti
    assert jti_a is not None
    service.consume(jti_a, user.id)  # first, legitimate consumption

    captured: list[object] = []
    original_execute = db_session.execute

    def spy_execute(stmt: object, *args: object, **kwargs: object) -> object:
        captured.append(stmt)
        return original_execute(stmt, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(db_session, "execute", spy_execute)

    with pytest.raises(ValidationFailed):
        service.consume(jti_a, user.id)  # replay -- triggers _revoke_all_valid

    # _revoke_all_valid's SELECT is the only one of the two this call issues that
    # filters on `consumed_at IS NULL`; consume()'s own jti lookup does not.
    revoke_statements = [
        stmt
        for stmt in captured
        if "consumed_at IS NULL" in str(stmt.compile(compile_kwargs={"literal_binds": False}))  # type: ignore[attr-defined]
    ]
    assert revoke_statements, f"did not observe _revoke_all_valid's SELECT among {captured!r}"
    compiled = str(revoke_statements[-1].compile(compile_kwargs={"literal_binds": False}))  # type: ignore[attr-defined]
    assert "ORDER BY refresh_tokens.id" in compiled, (
        f"_revoke_all_valid's SELECT is missing ORDER BY id: {compiled}"
    )


def test_a_refresh_token_from_a_different_user_is_rejected(db_session: Session) -> None:
    owner = _make_user(db_session, "owner@refresh.it")
    other = _make_user(db_session, "other@refresh.it")
    service = RefreshTokenService(db_session)
    token = service.issue(owner.id, SETTINGS)
    jti = decode_token(token, SETTINGS, expected_type="refresh").jti
    assert jti is not None

    with pytest.raises(ValidationFailed):
        service.consume(jti, other.id)


def test_an_expired_refresh_token_row_is_rejected(db_session: Session) -> None:
    """Independent of the JWT's own `exp` claim: `consume()` checks the row's
    `expires_at` itself, so an expired row is dead even if something upstream never
    decoded (or could not decode) the token to notice."""
    user = _make_user(db_session)
    already_expired = RefreshToken(
        jti=uuid4(),
        user_id=user.id,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db_session.add(already_expired)
    db_session.commit()

    with pytest.raises(ValidationFailed):
        RefreshTokenService(db_session).consume(already_expired.jti, user.id)


def test_is_live_answers_only_for_an_unspent_unexpired_row_of_that_user(
    db_session: Session,
) -> None:
    """The Google consent's way back asks this (REB-446), and it must answer without
    touching the session it asks about: a consumed token reads as dead but burns
    nothing, so the successor it was rotated into still refreshes afterwards."""
    owner = _make_user(db_session, "owner@live.it")
    other = _make_user(db_session, "other@live.it")
    service = RefreshTokenService(db_session)
    jti = decode_token(service.issue(owner.id, SETTINGS), SETTINGS, expected_type="refresh").jti
    assert jti is not None

    assert service.is_live(jti, owner.id) is True
    assert service.is_live(jti, other.id) is False
    assert service.is_live(uuid4(), owner.id) is False

    successor = service.rotate(jti, owner.id, SETTINGS).refresh_token
    assert service.is_live(jti, owner.id) is False
    next_jti = decode_token(successor, SETTINGS, expected_type="refresh").jti
    assert next_jti is not None
    assert service.is_live(next_jti, owner.id) is True

    expired = RefreshToken(
        jti=uuid4(), user_id=owner.id, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    db_session.add(expired)
    db_session.commit()
    assert service.is_live(expired.jti, owner.id) is False


def test_get_active_returns_the_user(db_session: Session) -> None:
    user = _make_user(db_session)
    fetched = UserRepository(db_session).get_active(user.id)
    assert fetched.id == user.id


def test_get_active_rejects_an_unknown_user(db_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        UserRepository(db_session).get_active(uuid4())


def test_get_active_rejects_a_deactivated_user(db_session: Session) -> None:
    user = _make_user(db_session)
    _make_user(db_session, "refresh-superstite@test.it")  # REB-292: the guard needs a survivor
    UserService(db_session).update(user.id, UserUpdate(attivo=False), ADMIN)

    with pytest.raises(ValidationFailed):
        UserRepository(db_session).get_active(user.id)


def test_two_concurrent_consumes_of_the_same_jti_do_not_both_succeed(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact race the review reproduced: two real sessions, two threads, both
    racing to consume the *same* jti for the first time -- e.g. an attacker replaying
    a stolen token at the same moment the legitimate user's browser rotates it. This
    does not use the db_session fixture: that fixture is one connection wrapped in a
    savepoint, which cannot exhibit real cross-transaction row locking against itself.

    The delay is injected as an unconditional sleep inside each thread's own call to
    `datetime.now()` -- not a mutual barrier requiring both SELECTs to complete before
    either commits. A mutual barrier there would deadlock the *fixed* code: once
    `with_for_update()` is in place, the second thread's SELECT itself blocks at the
    database until the first thread commits, so it would never reach a "both SELECTs
    done" checkpoint. The sleep widens each thread's own window between its SELECT and
    its write without requiring the other thread to have reached the same point,
    which is what lets this test mean something in both the broken and the fixed case
    instead of hanging in one of them.
    """
    factory = session_factory(db_engine)

    setup_session = factory()
    try:
        user = UserService(setup_session).create(
            UserCreate(
                email=f"race-{uuid4()}@test.it",
                password="supersegreta1",
                nome="Race",
                ruolo="admin",
            ),
            ADMIN,
        )
        setup_service = RefreshTokenService(setup_session)
        token_a = setup_service.issue(user.id, SETTINGS)  # the token under the race
        token_b = setup_service.issue(user.id, SETTINGS)  # sibling, never touched directly
    finally:
        setup_session.close()

    jti_a = decode_token(token_a, SETTINGS, expected_type="refresh").jti
    jti_b = decode_token(token_b, SETTINGS, expected_type="refresh").jti
    assert jti_a is not None
    assert jti_b is not None

    real_datetime = refresh_service_module.datetime

    class _SlowDateTime(real_datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            time.sleep(0.3)
            return real_datetime.now(tz)

    monkeypatch.setattr(refresh_service_module, "datetime", _SlowDateTime)

    start_barrier = threading.Barrier(2)
    outcomes: list[BaseException | None] = []
    outcomes_lock = threading.Lock()

    def worker() -> None:
        session = factory()
        outcome: BaseException | None = None
        try:
            start_barrier.wait()  # release both threads together
            RefreshTokenService(session).consume(jti_a, user.id)
        except BaseException as exc:  # noqa: BLE001 - captured and asserted on below
            outcome = exc
        finally:
            session.close()
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads), (
        "a thread is still blocked after 10s -- with_for_update() may have deadlocked "
        "or hung instead of serialising the two consume() calls"
    )
    assert len(outcomes) == 2, f"expected both threads to report an outcome: {outcomes!r}"

    unexpected = [o for o in outcomes if o is not None and not isinstance(o, ValidationFailed)]
    assert not unexpected, f"unexpected exception(s) from concurrent consume(): {unexpected!r}"

    successes = [o for o in outcomes if o is None]
    failures = [o for o in outcomes if isinstance(o, ValidationFailed)]
    assert len(successes) == 1, (
        f"expected exactly one of the two concurrent consume() calls to succeed, "
        f"got {len(successes)} (outcomes={outcomes!r}) -- without with_for_update() "
        "both read consumed_at IS NULL before either writes, and both succeed"
    )
    assert len(failures) == 1

    # The loser's consume() lands in the "already consumed" branch -- exactly like any
    # other replay -- which must trigger the same mass revocation: jti_b, never itself
    # touched by the race, has to be dead too.
    verify_session = factory()
    try:
        with pytest.raises(ValidationFailed):
            RefreshTokenService(verify_session).consume(jti_b, user.id)
    finally:
        verify_session.close()


def _shifted_by(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """Move the service's own clock forward without waiting for it.

    The service reads the wall clock through its module-level `datetime`, which is the
    single point every other test in this file already freezes (see
    `test_two_concurrent_consumes_of_the_same_jti_do_not_both_succeed`), so the grace
    window can be crossed in a test that runs in milliseconds. A real `time.sleep(11)`
    would prove the same thing eleven seconds slower, once per assertion.
    """
    real_datetime = refresh_service_module.datetime

    class _Shifted(real_datetime):  # type: ignore[valid-type,misc]
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return real_datetime.now(tz) + timedelta(seconds=seconds)

    monkeypatch.setattr(refresh_service_module, "datetime", _Shifted)


def test_rotating_consumes_the_presented_token_and_records_its_successor(
    db_session: Session,
) -> None:
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)
    token = service.issue(user.id, SETTINGS)
    jti = decode_token(token, SETTINGS, expected_type="refresh").jti
    assert jti is not None

    rotation = service.rotate(jti, user.id, SETTINGS)

    successor_jti = decode_token(rotation.refresh_token, SETTINGS, expected_type="refresh").jti
    assert successor_jti is not None and successor_jti != jti
    record = db_session.execute(select(RefreshToken).where(RefreshToken.jti == jti)).scalar_one()
    assert record.consumed_at is not None
    # The successor reference is what makes the grace window below possible at all: on a
    # second presentation there is otherwise nothing left on the row that says which
    # token the first presentation handed out.
    assert record.successor_jti == successor_jti


def test_a_replay_within_the_grace_window_returns_the_very_same_successor(
    db_session: Session,
) -> None:
    """Two tabs, one refresh cookie. Both wake up past the fifteen-minute access cookie
    and both POST /api/auth/refresh with the same rotating token: the second is not an
    attacker, it is the same browser, and treating it as a replay logged the owner out
    of every tab at once. Within ten seconds the answer is the pair the first
    presentation already produced -- byte for byte, so the second tab's cookie jar ends
    up holding exactly what the first tab's does."""
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)
    token = service.issue(user.id, SETTINGS)
    jti = decode_token(token, SETTINGS, expected_type="refresh").jti
    assert jti is not None

    first = service.rotate(jti, user.id, SETTINGS)
    second = service.rotate(jti, user.id, SETTINGS)

    assert second.refresh_token == first.refresh_token
    assert second.issued_at == first.issued_at
    # No third row: the grace path hands back the successor, it does not mint another.
    rows = db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)).scalars()
    assert len(list(rows)) == 2
    # And the successor is still unconsumed, so the next genuine rotation works.
    successor_jti = decode_token(first.refresh_token, SETTINGS, expected_type="refresh").jti
    assert successor_jti is not None
    assert service.rotate(successor_jti, user.id, SETTINGS).refresh_token != first.refresh_token


def test_a_replay_within_the_grace_window_does_not_revoke_the_family(
    db_session: Session,
) -> None:
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)
    token_a = service.issue(user.id, SETTINGS)  # the session being rotated
    token_b = service.issue(user.id, SETTINGS)  # another device, untouched
    jti_a = decode_token(token_a, SETTINGS, expected_type="refresh").jti
    jti_b = decode_token(token_b, SETTINGS, expected_type="refresh").jti
    assert jti_a is not None and jti_b is not None

    service.rotate(jti_a, user.id, SETTINGS)
    service.rotate(jti_a, user.id, SETTINGS)  # the second tab, inside the window

    # The other device's token is the tell: mass revocation would have killed it.
    service.rotate(jti_b, user.id, SETTINGS)


def test_a_replay_after_the_grace_window_revokes_the_family(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the window the old reading stands: a consumed token turning up again is the
    signal it was stolen, and the whole family dies. The window forgives the ordinary
    race between two tabs, not a token resurfacing minutes or days later."""
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)
    token_a = service.issue(user.id, SETTINGS)
    token_b = service.issue(user.id, SETTINGS)
    jti_a = decode_token(token_a, SETTINGS, expected_type="refresh").jti
    jti_b = decode_token(token_b, SETTINGS, expected_type="refresh").jti
    assert jti_a is not None and jti_b is not None

    service.rotate(jti_a, user.id, SETTINGS)

    _shifted_by(monkeypatch, REFRESH_GRACE_SECONDS + 1)
    with pytest.raises(ValidationFailed):
        service.rotate(jti_a, user.id, SETTINGS)

    with pytest.raises(ValidationFailed):
        service.rotate(jti_b, user.id, SETTINGS)


def test_a_replay_whose_successor_is_already_consumed_revokes_the_family(
    db_session: Session,
) -> None:
    """The grace window returns a *live* successor or nothing. Once the successor has
    itself been used -- rotated on by the tab that received it, or killed by a logout --
    there is no same-pair answer left to give, and a token presented after that is a
    replay like any other, inside ten seconds or not."""
    user = _make_user(db_session)
    service = RefreshTokenService(db_session)
    token_a = service.issue(user.id, SETTINGS)
    token_b = service.issue(user.id, SETTINGS)
    jti_a = decode_token(token_a, SETTINGS, expected_type="refresh").jti
    jti_b = decode_token(token_b, SETTINGS, expected_type="refresh").jti
    assert jti_a is not None and jti_b is not None

    first = service.rotate(jti_a, user.id, SETTINGS)
    successor_jti = decode_token(first.refresh_token, SETTINGS, expected_type="refresh").jti
    assert successor_jti is not None
    service.consume(successor_jti, user.id)  # e.g. the logout of the tab that got it

    with pytest.raises(ValidationFailed):
        service.rotate(jti_a, user.id, SETTINGS)

    with pytest.raises(ValidationFailed):
        service.rotate(jti_b, user.id, SETTINGS)
