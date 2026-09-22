import statistics
import time
from collections.abc import Callable

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.passwords import hash_password, verify_password
from pigrocrm.core.auth.schemas import NOME_MAX_LENGTH, UserCreate, UserUpdate
from pigrocrm.core.auth.service import LastActiveAdmin, SelfAccountChange, UserService
from pigrocrm.core.errors import Conflict, PermissionDenied, ValidationFailed

ADMIN = Actor(id=None, type="system", role="admin")
COLLAB = Actor(id=None, type="user", role="collaboratore")


def test_password_hash_is_argon2_and_never_the_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2")
    assert "correct horse" not in hashed
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_the_same_password_hashes_differently_each_time() -> None:
    assert hash_password("same") != hash_password("same"), "argon2 must salt per hash"


def test_create_user_stores_a_hash_and_normalises_the_email(db_session: Session) -> None:
    service = UserService(db_session)
    user = service.create(
        UserCreate(
            email="  Mario@Example.IT ", password="supersegreta1", nome="Mario", ruolo="admin"
        ),
        ADMIN,
    )
    assert user.email == "mario@example.it"
    assert user.ruolo == "admin"
    assert user.attivo is True
    assert not hasattr(user, "password_hash"), "UserRead must never expose the hash"


def test_duplicate_email_is_a_conflict(db_session: Session) -> None:
    service = UserService(db_session)
    service.create(
        UserCreate(email="a@b.it", password="supersegreta1", nome="A", ruolo="admin"), ADMIN
    )
    with pytest.raises(Conflict) as exc:
        service.create(
            UserCreate(email="A@B.it", password="supersegreta1", nome="A2", ruolo="admin"), ADMIN
        )
    assert exc.value.details["entity"] == "user"


def test_short_password_is_rejected(db_session: Session) -> None:
    service = UserService(db_session)
    with pytest.raises(ValidationFailed) as exc:
        service.create(UserCreate(email="c@d.it", password="corta", nome="C", ruolo="admin"), ADMIN)
    assert exc.value.details["field"] == "password"


def test_only_admins_manage_users(db_session: Session) -> None:
    service = UserService(db_session)
    with pytest.raises(PermissionDenied) as exc:
        service.create(
            UserCreate(email="e@f.it", password="supersegreta1", nome="E", ruolo="readonly"), COLLAB
        )
    assert exc.value.details["required_roles"] == ["admin"]


def test_authenticate_accepts_correct_credentials(db_session: Session) -> None:
    service = UserService(db_session)
    service.create(
        UserCreate(email="g@h.it", password="supersegreta1", nome="G", ruolo="admin"), ADMIN
    )
    assert service.authenticate("G@H.it", "supersegreta1").email == "g@h.it"


@pytest.mark.parametrize(
    "email,password", [("g@h.it", "sbagliata"), ("nope@h.it", "supersegreta1")]
)
def test_authenticate_rejects_bad_credentials_without_saying_which(
    db_session: Session, email: str, password: str
) -> None:
    """Distinguishing 'unknown user' from 'wrong password' leaks which emails exist."""
    service = UserService(db_session)
    service.create(
        UserCreate(email="g@h.it", password="supersegreta1", nome="G", ruolo="admin"), ADMIN
    )
    with pytest.raises(ValidationFailed) as exc:
        service.authenticate(email, password)
    assert exc.value.details["reason"] == "credenziali non valide"


def test_deactivated_user_cannot_authenticate(db_session: Session) -> None:
    service = UserService(db_session)
    # A second active admin first: REB-292's guard refuses to take the space's last
    # one, and this test is about authentication, not about the guard (its own tests
    # are at the bottom of this file).
    service.create(
        UserCreate(email="altro@j.it", password="supersegreta1", nome="Altro", ruolo="admin"),
        ADMIN,
    )
    user = service.create(
        UserCreate(email="i@j.it", password="supersegreta1", nome="I", ruolo="admin"), ADMIN
    )
    service.update(user.id, UserUpdate(attivo=False), ADMIN)
    with pytest.raises(ValidationFailed) as exc:
        service.authenticate("i@j.it", "supersegreta1")
    assert exc.value.details["reason"] == "credenziali non valide", (
        "a deactivated account must fail identically to a wrong password or unknown "
        "email, or the error itself becomes a way to tell active accounts apart"
    )


def _interleaved_median_seconds(
    action_a: Callable[[], None],
    action_b: Callable[[], None],
    repeats: int = 10,
    warmup: int = 3,
) -> tuple[float, float]:
    """Median wall-clock time of two actions, measured side by side rather than one
    after the other. Each action is expected to always raise `ValidationFailed` -- that
    is the behaviour under measurement, not an error in the measurement itself.

    A naive "time A ten times to completion, then time B ten times, then compare means"
    measurement has two flaws that make it unreliable on a loaded machine (e.g. the full
    suite, with Docker-backed fixtures competing for the CPU):

    - Order bias: whichever path is timed first absorbs all of the warm-up --
      allocator warmth, connection state, CPU frequency scaling, page faults -- which
      inflates it relative to whichever path is timed second, regardless of which one
      is actually slower. A few untimed warm-up rounds on *both* actions before
      recording anything drains that one-time cost up front; interleaving the recorded
      samples (A, B, A, B, ...) instead of running them in two blocks means any warm-up
      residue, load spike, or CPU frequency change that shows up during the recorded
      run still lands on both series about equally, instead of only on whichever one
      happened to run first.
    - Mean over few samples: a single scheduler preemption or GC pause of a few hundred
      milliseconds -- ordinary on a machine running hundreds of tests plus Docker -- is
      an outlier that shifts a 10-sample mean by tens of milliseconds, which is
      comparable in size to the real effect being measured. The median barely moves for
      one such stall.
    """
    for _ in range(warmup):
        with pytest.raises(ValidationFailed):
            action_a()
        with pytest.raises(ValidationFailed):
            action_b()

    samples_a: list[float] = []
    samples_b: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        with pytest.raises(ValidationFailed):
            action_a()
        samples_a.append(time.perf_counter() - start)

        start = time.perf_counter()
        with pytest.raises(ValidationFailed):
            action_b()
        samples_b.append(time.perf_counter() - start)

    return statistics.median(samples_a), statistics.median(samples_b)


def test_authenticate_timing_does_not_reveal_whether_the_email_exists(
    db_session: Session,
) -> None:
    """Measured, not deduced. An attacker who can time login attempts must not be able
    to tell 'no such email' apart from 'right email, wrong password' from latency alone.
    Asserted as a ratio of two medians, not as absolute milliseconds, so this does not
    flake on a slower or faster machine than whatever ran it last."""
    service = UserService(db_session)
    service.create(
        UserCreate(email="timing@race.it", password="supersegreta1", nome="T", ruolo="admin"),
        ADMIN,
    )

    unknown_email, wrong_password = _interleaved_median_seconds(
        lambda: service.authenticate("nobody@race.it", "whatever12"),
        lambda: service.authenticate("timing@race.it", "wrongpass1"),
    )
    ratio = unknown_email / wrong_password

    # Visible with `-s`: real measured numbers for a human reviewing this, not guessed.
    print(
        f"\ntiming: unknown-email={unknown_email * 1000:.1f}ms "
        f"wrong-password={wrong_password * 1000:.1f}ms ratio={ratio:.2f}"
    )
    # Bound is 1.5, not 2.0: hash and verify cost almost exactly the same in argon2, so
    # an unfixed miss path (hash + verify) measures ~1.9-2.05x a hit path (verify only)
    # on this machine across repeated runs -- right on top of a 2.0 boundary, which made
    # that boundary catch the regression in only 1 of 5 trial runs. 1.5 sits with margin
    # on both sides of the two real clusters (~1.0x fixed, ~2.0x broken) instead of on
    # top of one of them.
    #
    # That margin is what the interleaved-median measurement above protects: an earlier
    # version of this helper timed ten unknown-email calls to completion and then ten
    # wrong-password calls, and compared their means. Unknown-email always ran first, so
    # it always absorbed the run's warm-up; and a single GC pause or scheduler stall
    # landing in just one of the two ten-sample blocks (not unusual under a full-suite
    # load) could move that block's mean by tens of milliseconds on its own. Either
    # effect alone was enough to push a genuinely ~1.0x machine past a 1.5 bound.
    # Warming up both paths first, interleaving the timed samples, and comparing
    # medians removes both effects without touching what is actually being asserted.
    assert 0.5 < ratio < 1.5, (
        f"unknown-email path took {unknown_email * 1000:.1f}ms, known-email-wrong-password "
        f"path took {wrong_password * 1000:.1f}ms (ratio {ratio:.2f}) -- response time "
        "leaks whether the email is registered"
    )


def test_duplicate_email_race_past_the_precheck_still_becomes_a_domain_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SELECT-then-INSERT precheck cannot see a row another request commits between
    its own SELECT and its own INSERT -- that gap is exactly what makes it a race. This
    simulates that race deterministically (no threads, no flakiness): force the precheck
    to report "not found" while a real duplicate already exists, so the INSERT hits the
    database's own unique constraint. `create()` must convert that into a domain
    `Conflict`, never let a raw `IntegrityError` escape, and must leave the session
    usable for whatever the caller does next."""
    service = UserService(db_session)
    service.create(
        UserCreate(email="race@conflict.it", password="supersegreta1", nome="R1", ruolo="admin"),
        ADMIN,
    )

    monkeypatch.setattr(service.repo, "get_by_email", lambda email: None)

    with pytest.raises(Conflict) as exc:
        service.create(
            UserCreate(
                email="race@conflict.it", password="supersegreta1", nome="R2", ruolo="admin"
            ),
            ADMIN,
        )
    assert exc.value.details["entity"] == "user"

    # The session must still be usable right after -- a leftover PendingRollbackError
    # would blow up on the very next statement issued on it.
    assert service.count() == 1


def test_case_insensitive_email_uniqueness_is_enforced_by_the_database(
    db_session: Session,
) -> None:
    """Constructs `User` rows directly, bypassing `UserCreate`'s normalising validator,
    so only the database's own constraint can catch a same-email-different-case
    duplicate. A plain `unique=True` on the raw column is case-sensitive and would let
    both rows through silently."""
    db_session.add(
        User(
            email="CaseTest@Example.com",
            password_hash=hash_password("supersegreta1"),
            nome="Case1",
            ruolo="admin",
            attivo=True,
        )
    )
    db_session.commit()

    db_session.add(
        User(
            email="casetest@example.com",
            password_hash=hash_password("supersegreta1"),
            nome="Case2",
            ruolo="admin",
            attivo=True,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_users_email_has_a_case_insensitive_unique_index_in_postgres(
    db_session: Session,
) -> None:
    """Measures the actual database catalog rather than only inferring it from
    behaviour: the previous test would also pass with, say, a `citext` column, an
    application-level lock, or (before the fix) not pass at all for a different reason.
    This pins down the specific mechanism this schema commits to."""
    indexdefs = [
        row[0]
        for row in db_session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE tablename = 'users'")
        ).all()
    ]
    # Postgres renders the functional index with an explicit cast, e.g.
    # "lower((email)::text)" rather than the "lower(email)" written in the model --
    # measured here rather than assumed, matching indexdefs loosely enough to survive
    # that rendering while still requiring a real UNIQUE index over lower(...email...).
    assert any(
        "unique" in d.lower() and "lower(" in d.lower() and "email" in d.lower() for d in indexdefs
    ), indexdefs


# --- Final review item 4 (CRITICAL): UserCreate/UserUpdate.nome had no max_length -
#
# `users.nome` is `String(200)` (auth/models.py). Every other domain's Create/Update
# schema mirrors its own String columns' widths; `UserCreate`/`UserUpdate` predate
# that sweep and were missed until the final review. Without this bound, an
# over-length value sails past Pydantic, reaches flush(), and comes back as a raw
# sqlalchemy.exc.DataError (StringDataRightTruncation) -- not a subclass of
# IntegrityError, so `UserService.create`'s own `except IntegrityError` (guarding the
# email-uniqueness race) does not catch it, and it poisons the session.


def test_nome_over_the_column_width_is_rejected_on_create() -> None:
    with pytest.raises(ValidationError):
        UserCreate(email="x@example.it", password="supersegreta1", nome="x" * (NOME_MAX_LENGTH + 1))


def test_nome_over_the_column_width_is_rejected_on_update() -> None:
    with pytest.raises(ValidationError):
        UserUpdate(nome="x" * (NOME_MAX_LENGTH + 1))


def test_nome_at_the_column_width_is_accepted_on_create() -> None:
    user = UserCreate(email="x@example.it", password="supersegreta1", nome="x" * NOME_MAX_LENGTH)
    assert len(user.nome) == NOME_MAX_LENGTH


def test_a_nul_byte_in_nome_is_rejected_not_stored(db_session: Session) -> None:
    """Same family as the max_length gap above (final review item 1): a NUL byte in
    a native string column reaches Postgres raw unless SafeStr catches it first."""
    with pytest.raises(ValidationError):
        UserCreate(email="y@example.it", password="supersegreta1", nome="Mario\x00Rossi")


def test_reset_password_replaces_the_hash_and_records_only_that_it_happened(
    db_session: Session,
) -> None:
    from pigrocrm.core.auth.service import UserService

    service = UserService(db_session)
    admin = Actor.system()
    user = service.create(
        UserCreate(email="reset@studio.it", password="vecchia-password-1", nome="R", ruolo="admin"),
        admin,
    )
    service.reset_password("Reset@Studio.it", "nuova-password-2026", admin)
    row = db_session.get(User, user.id)
    assert row is not None
    assert verify_password("nuova-password-2026", row.password_hash)
    assert not verify_password("vecchia-password-1", row.password_hash)
    entry = db_session.execute(
        text(
            "select kind, payload::text from activities where entity_id = :id "
            "order by occurred_at desc limit 1"
        ),
        {"id": str(user.id)},
    ).first()
    assert entry is not None and entry[0] == "password_reset"
    assert "nuova-password-2026" not in entry[1]


def test_reset_password_refuses_a_short_password_and_an_unknown_email(db_session: Session) -> None:
    from pigrocrm.core.auth.service import UserService
    from pigrocrm.core.errors import NotFound, ValidationFailed

    service = UserService(db_session)
    with pytest.raises(NotFound):
        service.reset_password("nessuno@studio.it", "lunghissima-1", Actor.system())
    service.create(
        UserCreate(email="corta@studio.it", password="vecchia-password-1", nome="C", ruolo="admin"),
        Actor.system(),
    )
    with pytest.raises(ValidationFailed):
        service.reset_password("corta@studio.it", "corta", Actor.system())


def test_a_user_without_a_password_cannot_log_in_with_one(db_session: Session) -> None:
    """A link-by-mail account (spec 2026-09-12 §6.2): same sentence as a wrong password."""
    from pigrocrm.core.auth.repository import UserRepository
    from pigrocrm.core.auth.service import INVALID_CREDENTIALS

    service = UserService(db_session)
    created = service.create(
        UserCreate(email="link@x.it", password="lunghissima1", nome="Link", ruolo="admin"),
        Actor.system(),
    )
    row = UserRepository(db_session).get(created.id)
    assert row is not None
    row.password_hash = None
    db_session.flush()
    with pytest.raises(ValidationFailed) as excinfo:
        service.authenticate("link@x.it", "lunghissima1")
    assert INVALID_CREDENTIALS in str(excinfo.value)


def test_only_the_system_may_create_a_user_without_a_password(db_session: Session) -> None:
    """A space's first admin enters with a link by mail (spec 2026-09-12 §6.4); an admin
    adding a colleague still hands them a password."""
    from pigrocrm.core.auth.repository import UserRepository

    service = UserService(db_session)
    admin = service.create(
        UserCreate(email="capo@x.it", password="lunghissima1", nome="Capo", ruolo="admin"),
        Actor.system(),
    )
    human = Actor(id=admin.id, type="user", role="admin")
    with pytest.raises(ValidationFailed):
        service.create(UserCreate(email="link@x.it", password=None, nome="Link"), human)
    created = service.create(
        UserCreate(email="link@x.it", password=None, nome="Link"), Actor.system()
    )
    row = UserRepository(db_session).get(created.id)
    assert row is not None and row.password_hash is None


def test_an_unverified_passwordless_admin_cannot_create_users_until_the_first_link(
    db_session: Session,
) -> None:
    """Spec 2026-09-12 §6.4: an address somebody typed at the signup may not add a user
    (or mint a token, `test_pat_service.py`) before a link by mail proves it."""
    from datetime import UTC, datetime

    from pigrocrm.core.auth.repository import UserRepository

    service = UserService(db_session)
    admin = service.create(
        UserCreate(email="link@x.it", password=None, nome="Link", ruolo="admin"), Actor.system()
    )
    actor = Actor(id=admin.id, type="user", role="admin")
    with pytest.raises(ValidationFailed) as excinfo:
        service.create(UserCreate(email="c@x.it", password="lunghissima1", nome="C"), actor)
    assert "conferma il tuo indirizzo" in str(excinfo.value)
    row = UserRepository(db_session).get(admin.id)
    assert row is not None
    row.email_verificata_il = datetime.now(UTC)
    db_session.flush()
    assert (
        service.create(UserCreate(email="c@x.it", password="lunghissima1", nome="C"), actor).email
        == "c@x.it"
    )


def test_update_own_digest_needs_no_admin(db_session: Session) -> None:
    """Spec 2026-09-16 §3.6: the weekly digest's own opt-out link must work for
    whoever received the mail, not only for an administrator of the space -- unlike
    `update`, which raises `PermissionDenied` for a non-admin actor via
    `actor.require_admin`, `update_own_digest` takes no such check."""
    from pigrocrm.core.auth.repository import UserRepository

    service = UserService(db_session)
    collab = service.create(
        UserCreate(
            email="collab@x.it", password="lunghissima1", nome="Collab", ruolo="collaboratore"
        ),
        Actor.system(),
    )
    actor = Actor(id=collab.id, type="user", role="collaboratore")

    updated = service.update_own_digest(actor, False)

    assert updated.digest_settimanale is False
    row = UserRepository(db_session).get(collab.id)
    assert row is not None and row.digest_settimanale is False


def test_setting_the_digest_to_what_it_already_is_records_nothing(db_session: Session) -> None:
    """A switch is a control people click twice. The timeline records what changed, so a
    `PATCH` that changes nothing has nothing to say on it -- `update` draws the same line
    with its `delta`, and this is that line for a single field."""
    from pigrocrm.core.activities.repository import ActivityRepository

    service = UserService(db_session)
    user = service.create(
        UserCreate(email="switch@x.it", password="lunghissima1", nome="Switch"),
        Actor.system(),
    )
    actor = Actor(id=user.id, type="user", role="admin")
    attivita = ActivityRepository(db_session)

    service.update_own_digest(actor, False)
    dopo_il_cambio = len(attivita.timeline("user", user.id, limit=50))

    assert service.update_own_digest(actor, False).digest_settimanale is False

    assert len(attivita.timeline("user", user.id, limit=50)) == dopo_il_cambio
    # The one that did change something is still on the timeline, with the new value.
    ultima = attivita.timeline("user", user.id, limit=50)[0]
    assert ultima.kind == "updated"
    assert ultima.payload == {"digest_settimanale": False}


def _admin_actor(service: UserService, db_session: Session, email: str, nome: str):
    admin = service.create(
        UserCreate(email=email, password="lunghissima1", nome=nome, ruolo="admin"),
        Actor.system(),
    )
    return Actor(id=admin.id, type="user", role="admin"), admin


def test_the_last_admin_cannot_demote_themselves(db_session: Session) -> None:
    """REB-292: a space with one admin is one click from having none, and the SPA's
    disabled select was the only thing standing between. The service refuses, and the
    sentence says what is missing, not who is not allowed: the caller IS an admin."""
    service = UserService(db_session)
    actor, solo = _admin_actor(service, db_session, "solo@x.it", "Solo")

    with pytest.raises(LastActiveAdmin) as excinfo:
        service.update(solo.id, UserUpdate(ruolo="readonly"), actor)

    assert excinfo.value.details["field"] == "ruolo"
    assert "almeno un amministratore attivo" in str(excinfo.value)


def test_the_last_admin_cannot_deactivate_themselves(db_session: Session) -> None:
    service = UserService(db_session)
    actor, solo = _admin_actor(service, db_session, "solo2@x.it", "Solo")

    with pytest.raises(LastActiveAdmin):
        service.update(solo.id, UserUpdate(attivo=False), actor)


def test_an_admin_can_demote_the_other_admin_while_two_exist(db_session: Session) -> None:
    """The count rule alone would allow this pair: two admins, one demotes the other,
    one remains, the invariant holds. This is the allowed half of the two-rule split,
    pinned next to its mirror in `test_the_self_change_rule...`."""
    service = UserService(db_session)
    carla = service.create(
        UserCreate(email="carla@x.it", password="lunghissima1", nome="Carla", ruolo="admin"),
        Actor.system(),
    )
    actor, _ = _admin_actor(service, db_session, "giulia@x.it", "Giulia")

    demoted = service.update(carla.id, UserUpdate(ruolo="readonly"), actor)

    assert demoted.ruolo == "readonly"


def test_an_admin_cannot_demote_themselves_while_two_exist(db_session: Session) -> None:
    service = UserService(db_session)
    # Two admins: the count rule passes, and the self-change rule is what refuses.
    service.create(
        UserCreate(email="prima@x.it", password="lunghissima1", nome="Prima", ruolo="admin"),
        Actor.system(),
    )
    actor, second = _admin_actor(service, db_session, "seconda@x.it", "Seconda")
    with pytest.raises(SelfAccountChange) as excinfo:
        service.update(second.id, UserUpdate(ruolo="collaboratore"), actor)

    assert excinfo.value.details["field"] == "ruolo"
    assert "un altro amministratore" in str(excinfo.value)


def test_an_admin_can_still_edit_their_own_ordinary_fields(db_session: Session) -> None:
    """The refusal is about the privileged fields, not about the person: the same
    actor patching their own name still writes."""
    service = UserService(db_session)
    actor, me = _admin_actor(service, db_session, "nome@x.it", "Nome")

    renamed = service.update(me.id, UserUpdate(nome="Nome Secondo"), actor)

    assert renamed.nome == "Nome Secondo"


def test_nothing_refused_leaves_the_row_untouched(db_session: Session) -> None:
    """Both refusals raise before the first `setattr`, so the session's identity map
    never sees the pending change: a refused self-demote must not leave the row
    demoted for whoever commits next on this session."""
    from pigrocrm.core.auth.repository import UserRepository

    service = UserService(db_session)
    actor, solo = _admin_actor(service, db_session, "pulito@x.it", "Pulito")

    with pytest.raises(LastActiveAdmin):
        service.update(solo.id, UserUpdate(ruolo="readonly", attivo=False), actor)
    db_session.commit()

    row = UserRepository(db_session).get(solo.id)
    assert row is not None and row.ruolo == "admin" and row.attivo is True
