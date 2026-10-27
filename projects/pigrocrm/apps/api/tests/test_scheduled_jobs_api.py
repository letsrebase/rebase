"""REB-764 over HTTP: the four cron jobs and how each last went, for an admin."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.reminders.jobs import AUTOMATIONS_JOB, DIGEST_JOB, REMINDER_PASS_JOB, JobClock


def test_an_admin_reads_the_four_jobs_and_what_the_clock_holds(
    logged_in: TestClient, api_session: Session
) -> None:
    now = datetime.now(UTC)
    clock = JobClock(api_session)
    clock.succeeded(REMINDER_PASS_JOB, now - timedelta(minutes=5))
    clock.succeeded(DIGEST_JOB, now - timedelta(days=30))
    clock.failed(AUTOMATIONS_JOB, "RuntimeError", now)
    api_session.commit()

    response = logged_in.get("/api/scheduled-jobs")

    assert response.status_code == 200, response.text
    jobs = {job["key"]: job for job in response.json()["jobs"]}
    assert list(jobs) == ["gmail-sync", "reminders", "automations", "digest"]
    assert jobs["gmail-sync"]["state"] == "not_configured", "no mailbox in this space"
    assert jobs["reminders"]["state"] == "ok"
    assert jobs["digest"]["state"] == "late"
    assert jobs["automations"]["state"] == "failed"
    assert jobs["automations"]["failure"] == "RuntimeError"
    assert jobs["automations"]["last_run_at"] is None
    assert jobs["reminders"]["command"] == "pigrocrm reminders"


def test_a_member_is_refused(collaborator_client: TestClient) -> None:
    assert collaborator_client.get("/api/scheduled-jobs").status_code == 403


def test_a_reader_is_refused(readonly_client: TestClient) -> None:
    assert readonly_client.get("/api/scheduled-jobs").status_code == 403
