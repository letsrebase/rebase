"""What each scheduled job last did, and how that reads (REB-764).

The record is `reminder_job_run`, one row per job, written by the four cron commands
(their tests are `test_cli_*.py`) and read here by `ScheduledJobService`, which Impostazioni
→ Esecuzioni and the `describe_scheduled_jobs` tool render. These tests hold the clock's two
rules, that a failure never moves the last success and a success ends a failure, and the
reading's states, their precedence and the boundary of each.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.errors import PermissionDenied
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.reminders.jobs import (
    AUTOMATIONS_JOB,
    DIGEST_JOB,
    FAILURE_MAX_LENGTH,
    GMAIL_SYNC_JOB,
    JOBS_BY_KEY,
    REMINDER_PASS_JOB,
    SCHEDULED_JOBS,
    JobClock,
    ScheduledJobRead,
    ScheduledJobService,
)
from pigrocrm.core.reminders.models import ReminderJobRun

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
ADMIN = Actor(id=uuid7(), type="user", role="admin")


def _read(session: Session, now: datetime = NOW) -> dict[str, ScheduledJobRead]:
    session.flush()
    described = ScheduledJobService(session).describe(ADMIN, now=now)
    return {job.key: job for job in described.jobs}


def _row(session: Session, job: str) -> ReminderJobRun:
    session.flush()
    return session.scalars(select(ReminderJobRun).where(ReminderJobRun.job == job)).one()


def _mailbox(session: Session, *, status: str = "active") -> None:
    owner = User(email=f"u-{uuid4()}@example.test", password_hash="x", nome="P", ruolo="admin")
    session.add(owner)
    session.flush()
    session.add(
        GoogleAccount(
            user_id=owner.id,
            google_sub=f"sub-{uuid4()}",
            email_address=f"box-{uuid4().hex[:6]}@example.test",
            refresh_token_ciphertext=b"x",
            refresh_token_nonce=b"x",
            scopes_granted=[],
            status=status,
        )
    )
    session.flush()


# -- the clock ---------------------------------------------------------------------------


def test_a_success_moves_the_time_and_is_one_row_per_job(db_session: Session) -> None:
    clock = JobClock(db_session)

    clock.succeeded(DIGEST_JOB, NOW - timedelta(days=7))
    clock.succeeded(DIGEST_JOB, NOW)
    clock.succeeded(AUTOMATIONS_JOB, NOW)
    db_session.flush()

    assert _row(db_session, DIGEST_JOB).ran_at == NOW
    assert db_session.scalar(select(func.count()).select_from(ReminderJobRun)) == 2


def test_a_failure_leaves_the_last_success_where_it_was(db_session: Session) -> None:
    clock = JobClock(db_session)
    clock.succeeded(DIGEST_JOB, NOW - timedelta(days=7))

    clock.failed(DIGEST_JOB, "OperationalError", NOW)

    row = _row(db_session, DIGEST_JOB)
    assert row.ran_at == NOW - timedelta(days=7), "lateness is measured from this"
    assert row.failed_at == NOW and row.failure == "OperationalError"


def test_a_job_whose_first_attempt_failed_has_a_failure_and_no_success(
    db_session: Session,
) -> None:
    JobClock(db_session).failed(AUTOMATIONS_JOB, "RuntimeError", NOW)

    row = _row(db_session, AUTOMATIONS_JOB)
    assert row.ran_at is None and row.failure == "RuntimeError"


def test_the_next_success_ends_the_failure(db_session: Session) -> None:
    clock = JobClock(db_session)
    clock.failed(AUTOMATIONS_JOB, "RuntimeError", NOW - timedelta(hours=1))

    clock.succeeded(AUTOMATIONS_JOB, NOW)

    row = _row(db_session, AUTOMATIONS_JOB)
    assert (row.ran_at, row.failed_at, row.failure) == (NOW, None, None)


def test_a_failure_text_is_one_bounded_line(db_session: Session) -> None:
    JobClock(db_session).failed(GMAIL_SYNC_JOB, "prima riga\n  seconda " + "x" * 500, NOW)

    failure = _row(db_session, GMAIL_SYNC_JOB).failure
    assert failure is not None
    assert len(failure) == FAILURE_MAX_LENGTH and "\n" not in failure
    assert failure.startswith("prima riga seconda x")


# -- the reading -------------------------------------------------------------------------


def test_the_four_jobs_are_listed_in_the_panels_order_with_their_commands(
    db_session: Session,
) -> None:
    jobs = ScheduledJobService(db_session).describe(ADMIN, now=NOW).jobs

    assert [job.key for job in jobs] == [
        GMAIL_SYNC_JOB,
        REMINDER_PASS_JOB,
        AUTOMATIONS_JOB,
        DIGEST_JOB,
    ]
    assert [job.command for job in jobs] == [
        "pigrocrm gmail-sync",
        "pigrocrm reminders",
        "pigrocrm automations",
        "pigrocrm digest",
    ]
    assert {job.key: job.stale_after_minutes for job in jobs} == {
        GMAIL_SYNC_JOB: 30,
        REMINDER_PASS_JOB: 30,
        AUTOMATIONS_JOB: 26 * 60,
        DIGEST_JOB: 8 * 24 * 60,
    }


def test_a_space_where_nothing_has_run_says_never_run_not_a_time(db_session: Session) -> None:
    _mailbox(db_session)

    jobs = _read(db_session)

    assert {job.state for job in jobs.values()} == {"never_run"}
    assert all(
        job.last_run_at is None and job.last_failure_at is None and job.failure is None
        for job in jobs.values()
    )


def test_the_mailbox_sync_is_not_configured_where_no_mailbox_is_connected(
    db_session: Session,
) -> None:
    """The command prints nothing for such a space, so there is nothing to record, and the
    panel must not read that silence as a fault."""
    jobs = _read(db_session)
    assert jobs[GMAIL_SYNC_JOB].state == "not_configured"
    assert jobs[REMINDER_PASS_JOB].state == "never_run", "the other jobs run everywhere"

    _mailbox(db_session, status="disconnected")
    assert _read(db_session)[GMAIL_SYNC_JOB].state == "not_configured", "unhooked is not one"

    _mailbox(db_session, status="revoked")
    assert _read(db_session)[GMAIL_SYNC_JOB].state == "never_run", "a broken one still is"


@pytest.mark.parametrize("job", [job.key for job in SCHEDULED_JOBS])
def test_a_job_is_late_exactly_at_its_own_late_point(db_session: Session, job: str) -> None:
    _mailbox(db_session)
    late_after = JOBS_BY_KEY[job].stale_after_minutes
    JobClock(db_session).succeeded(job, NOW - timedelta(minutes=late_after - 1))
    assert _read(db_session)[job].state == "ok"

    JobClock(db_session).succeeded(job, NOW - timedelta(minutes=late_after))

    described = _read(db_session)[job]
    assert described.state == "late"
    assert described.last_run_at == NOW - timedelta(minutes=late_after)


def test_a_failure_outranks_lateness_and_says_what_it_was_and_when(
    db_session: Session,
) -> None:
    clock = JobClock(db_session)
    clock.succeeded(DIGEST_JOB, NOW - timedelta(days=20))
    clock.failed(DIGEST_JOB, "invio_rifiutato", NOW - timedelta(days=2))

    described = _read(db_session)[DIGEST_JOB]

    assert described.state == "failed"
    assert described.last_run_at == NOW - timedelta(days=20), "the last success is still shown"
    assert described.last_failure_at == NOW - timedelta(days=2)
    assert described.failure == "invio_rifiutato"


def test_a_job_that_only_ever_failed_is_failed_and_has_no_run_time(
    db_session: Session,
) -> None:
    JobClock(db_session).failed(AUTOMATIONS_JOB, "RuntimeError", NOW)

    described = _read(db_session)[AUTOMATIONS_JOB]

    assert described.state == "failed" and described.last_run_at is None


def test_a_recovered_job_shows_no_failure(db_session: Session) -> None:
    clock = JobClock(db_session)
    clock.failed(AUTOMATIONS_JOB, "RuntimeError", NOW - timedelta(hours=2))
    clock.succeeded(AUTOMATIONS_JOB, NOW - timedelta(hours=1))

    described = _read(db_session)[AUTOMATIONS_JOB]

    assert described.state == "ok"
    assert described.last_failure_at is None and described.failure is None


def test_a_job_that_ran_a_moment_after_now_is_not_late(db_session: Session) -> None:
    """Two hosts a second apart must not read as a fault: a row newer than `now` is ok."""
    JobClock(db_session).succeeded(REMINDER_PASS_JOB, NOW + timedelta(seconds=3))

    assert _read(db_session)[REMINDER_PASS_JOB].state == "ok"


@pytest.mark.parametrize("role", ["collaboratore", "readonly"])
def test_only_an_admin_may_read_it(db_session: Session, role: str) -> None:
    actor = Actor(id=uuid7(), type="user", role=role)  # type: ignore[arg-type]

    with pytest.raises(PermissionDenied):
        ScheduledJobService(db_session).describe(actor)
