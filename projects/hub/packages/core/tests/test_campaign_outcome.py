"""What a sent mail led to, stamped once by the tick (spec § 6.2)."""

import logging
from datetime import datetime, timedelta

import pytest
from campaign_fixtures import (  # noqa: F401  (fixture)
    NOW,
    SETTINGS,
    Clock,
    admin,
    as_admin,
    clean,
    company,
    draft,
    lead,
    person,
)
from sqlalchemy import update
from sqlalchemy.orm import Session

import rebase_core.campaigns.outcome as outcome_module
from rebase_core.campaigns.actions import CV_NOTE_PREFIX, done_at
from rebase_core.campaigns.outcome import STAMP_WINDOW, stamp_outcomes
from rebase_core.campaigns.schemas import ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.tick import run_tick
from rebase_core.models import (
    Campaign,
    CampaignRecipient,
    Company,
    Freelancer,
    Login,
    Note,
    User,
)

NO_PAUSE = lambda _seconds: None  # noqa: E731


def sent(session: Session, clock: Clock, **fields: object) -> Campaign:
    """A campaign created, tested, scheduled for now and sent by one tick, the people
    already in the tables."""
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    created = service.create(who.id, draft(**fields))
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    run_tick(session, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    return session.get(Campaign, created.id)  # type: ignore[return-value]


def only_row(session: Session, campaign: Campaign) -> CampaignRecipient:
    session.expire_all()
    return session.query(CampaignRecipient).filter_by(campaign_id=campaign.id).one()


def test_to_login_after_the_mail_stamps_the_entry_and_the_signed_in_action(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, journey_status="complete", action="signed_in")
    row = only_row(clean, campaign)
    assert row.status == "sent" and row.signed_in_at is None
    at = clock.at + timedelta(hours=2)
    clean.add(Login(user_id=card.user_id, logged_at=at))
    clean.commit()
    assert stamp_outcomes(clean, now=at + timedelta(minutes=1)) == 1
    row = only_row(clean, campaign)
    assert (row.signed_in_at, row.action_at) == (at, at)


def test_a_stamp_is_written_once_and_never_moves(clean: Session) -> None:  # noqa: F811  (fixture)
    """Review Focus 1: a second login does not move «entrato» to its own day."""
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, journey_status="complete", action="signed_in")
    first = clock.at + timedelta(hours=2)
    clean.add(Login(user_id=card.user_id, logged_at=first))
    clean.commit()
    stamp_outcomes(clean, now=first)
    clean.add(Login(user_id=card.user_id, logged_at=first + timedelta(days=2)))
    clean.commit()
    assert stamp_outcomes(clean, now=first + timedelta(days=2)) == 0
    assert only_row(clean, campaign).signed_in_at == first


def test_a_cv_is_stamped_at_the_comment_the_member_service_writes(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it", cv=False)
    campaign = sent(clean, clock)  # `missing_cv`, action `cv`
    at = clock.at + timedelta(hours=3)
    clean.execute(
        update(Freelancer).where(Freelancer.id == card.id).values(cv_size=4, cv_bytes=b"%PDF")
    )
    clean.add(
        Note(
            entity_type="freelancer",
            entity_id=card.id,
            text=f"{CV_NOTE_PREFIX}: cv.pdf",
            author="Ada",
            source="person",
            created_at=at,
        )
    )
    clean.commit()
    stamp_outcomes(clean, now=at + timedelta(hours=1))
    assert only_row(clean, campaign).action_at == at


def test_a_card_that_became_complete_is_stamped_at_the_tick_that_saw_it(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it", cv=False, rate=False)
    campaign = sent(clean, clock, journey_status="card_empty_new", action="card_complete")
    clean.execute(
        update(Freelancer)
        .where(Freelancer.id == card.id)
        .values(cv_size=4, cv_bytes=b"%PDF", daily_rate=450)
    )
    clean.commit()
    seen = clock.at + timedelta(days=1)
    stamp_outcomes(clean, now=seen)
    assert only_row(clean, campaign).action_at == seen


def test_a_card_created_after_the_mail_is_stamped_at_its_creation(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    lead(clean, "giulia@studio.it")
    campaign = sent(
        clean, clock, journey_status="lead", action="profile_created", button_meta="wizard"
    )
    at = clock.at + timedelta(hours=5)
    card = person(clean, "giulia@studio.it", first_name="Giulia")
    clean.execute(update(Freelancer).where(Freelancer.id == card.id).values(created_at=at))
    clean.commit()
    stamp_outcomes(clean, now=at + timedelta(minutes=5))
    assert only_row(clean, campaign).action_at == at


def test_an_updated_request_is_stamped_at_its_update(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    row_company = company(clean, "ciro@block.it")
    # Every moment here is the test's own: a database default is the real clock, and a
    # real «now» far from `NOW` would put the mail outside `STAMP_WINDOW`.
    clean.execute(
        update(Company)
        .where(Company.id == row_company.id)
        .values(updated_at=NOW - timedelta(days=1))
    )
    clean.commit()
    campaign = sent(
        clean,
        clock,
        journey_status="company_open",
        action="request_updated",
        button_meta="request",
    )
    at = clock.at + timedelta(days=2)
    clean.execute(update(Company).where(Company.id == row_company.id).values(updated_at=at))
    clean.commit()
    stamp_outcomes(clean, now=at + timedelta(minutes=1))
    assert only_row(clean, campaign).action_at == at


def test_a_mail_older_than_the_window_is_never_stamped(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, journey_status="complete", action="signed_in")
    long_ago = clock.at - STAMP_WINDOW - timedelta(days=1)
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .values(sent_at=long_ago)
    )
    clean.add(Login(user_id=card.user_id, logged_at=clock.at))
    clean.commit()
    assert stamp_outcomes(clean, now=clock.at) == 0
    assert only_row(clean, campaign).signed_in_at is None


def test_to_campaign_still_sending_past_its_window_is_still_read(clean: Session) -> None:  # noqa: F811  (fixture)
    """Greptile P1, CodeRabbit Major on #440 (REB-533): a campaign stuck `sending` past
    `STAMP_WINDOW` (a revoked Resend key, say) that then resumes sending must still be
    read by its own state, not its schedule. `scheduled_for` and `updated_at` are both
    set outside the window here, so only `stato == "in_invio"` can be what keeps it in
    scope -- every moment is the test's own, since a database default would use the
    real clock and an explicit UPDATE overrides `updated_at`'s `onupdate`."""
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, journey_status="complete", action="signed_in")
    old = clock.at - timedelta(days=40)
    sent_at = clock.at - timedelta(days=1)
    clean.execute(
        update(Campaign)
        .where(Campaign.id == campaign.id)
        .values(status="sending", scheduled_for=old, updated_at=old)
    )
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .values(sent_at=sent_at)
    )
    at = sent_at + timedelta(hours=2)
    clean.add(Login(user_id=card.user_id, logged_at=at))
    clean.commit()
    assert stamp_outcomes(clean, now=clock.at) == 1
    row = only_row(clean, campaign)
    assert (row.signed_in_at, row.action_at) == (at, at)


def test_a_campaign_written_within_the_window_is_read_regardless_of_its_schedule(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Same shape, but `sent`: `updated_at` moves on the tick's claim, on `_finish`
    and on `cancel`, all of which follow every row's send, so a recent `updated_at`
    alone -- not the old `scheduled_for` -- must be what keeps the campaign in scope."""
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, journey_status="complete", action="signed_in")
    old = clock.at - timedelta(days=40)
    sent_at = clock.at - timedelta(days=1)
    clean.execute(
        update(Campaign)
        .where(Campaign.id == campaign.id)
        .values(status="sent", scheduled_for=old, updated_at=sent_at)
    )
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .values(sent_at=sent_at)
    )
    at = sent_at + timedelta(hours=2)
    clean.add(Login(user_id=card.user_id, logged_at=at))
    clean.commit()
    assert stamp_outcomes(clean, now=clock.at) == 1
    row = only_row(clean, campaign)
    assert (row.signed_in_at, row.action_at) == (at, at)


def test_a_skipped_row_is_never_stamped(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, journey_status="complete", action="signed_in")
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .values(status="skipped")
    )
    clean.add(Login(user_id=card.user_id, logged_at=clock.at + timedelta(hours=1)))
    clean.commit()
    assert stamp_outcomes(clean, now=clock.at + timedelta(hours=2)) == 0


def test_a_person_without_a_user_has_no_entry_to_stamp(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    lead(clean, "giulia@studio.it")
    campaign = sent(
        clean, clock, journey_status="lead", action="profile_created", button_meta="wizard"
    )
    assert stamp_outcomes(clean, now=clock.at + timedelta(hours=1)) == 0
    row = only_row(clean, campaign)
    assert (row.signed_in_at, row.action_at) == (None, None)
    assert clean.query(User).filter(User.email == "giulia@studio.it").count() == 0


def test_the_pigro_action_is_left_to_phase_3(clean: Session) -> None:  # noqa: F811  (fixture)
    """`pigro_customer` needs the CRM's usage endpoint (spec § 6.3): never stamped here."""
    row = CampaignRecipient(
        email="a@b.it", kind="freelancer", code="1", before={"t": NOW.isoformat()}
    )
    assert done_at(clean, row, "pigro_customer", since=NOW) is None


def test_the_entry_stamp_survives_a_broken_action_stamp(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review Focus 2: the entry and the action are stamped in their own savepoints. A
    row whose action check raises must not lose the `signed_in_at` its own login already
    earned a moment earlier -- and every pass after this one, since a row stuck without
    `signed_in_at` never leaves the window."""
    logging.getLogger("rebase_core.campaigns.outcome").disabled = False
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it", cv=False)
    campaign = sent(clean, clock)  # `missing_cv`, action `cv`
    at = clock.at + timedelta(hours=2)
    clean.add(Login(user_id=card.user_id, logged_at=at))
    clean.commit()

    def raising(
        session: Session,
        recipient: CampaignRecipient,
        action: str,
        *,
        since: datetime | None = None,
        now: datetime | None = None,
    ) -> datetime | None:
        raise KeyError("t")

    monkeypatch.setattr(outcome_module, "done_at", raising)
    with caplog.at_level(logging.ERROR):
        assert stamp_outcomes(clean, now=at + timedelta(minutes=1)) == 1

    row = only_row(clean, campaign)
    assert row.signed_in_at == at
    assert row.action_at is None
    assert "KeyError" in caplog.text
    assert "ada@studio.it" not in caplog.text


def test_a_stamp_lost_after_being_set_is_not_counted(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CodeRabbit's adversarial pass, item 6: the row's flag must turn true only once
    its `with session.begin_nested():` block has exited without raising, not the
    instant the attribute is set inside it -- a failure in the block's own commit or
    release (a DB error mid-write) must not be counted as a stamp, the same as a
    failure the check itself raises. Monkeypatching `session.flush` directly could not
    target only this row's own savepoint without also catching the unrelated autoflush
    `entered_at`'s own select triggers, so this stands in with a check that assigns the
    attribute and then raises, the same shape: the value is written before the failure,
    and the row must still not be counted."""
    logging.getLogger("rebase_core.campaigns.outcome").disabled = False
    clock = Clock(NOW)
    person(clean, "ada@studio.it")
    campaign = sent(clean, clock, journey_status="complete", action="signed_in")
    at = clock.at + timedelta(hours=2)

    def raising(
        session: Session, recipient: CampaignRecipient, *, since: datetime
    ) -> datetime | None:
        recipient.signed_in_at = at  # the value the savepoint would have written...
        raise KeyError("t")  # ...but the savepoint's own commit then fails

    monkeypatch.setattr(outcome_module, "entered_at", raising)
    with caplog.at_level(logging.ERROR):
        assert stamp_outcomes(clean, now=at + timedelta(minutes=1)) == 0

    row = only_row(clean, campaign)
    assert row.signed_in_at is None
    assert row.action_at is None
    assert "KeyError" in caplog.text


def test_a_row_whose_stamping_raises_is_skipped_the_rest_still_stamped(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review finding: one row's own broken data (a malformed `before["requests"]`
    timestamp, say) must not wedge every other row's stamp for the rest of the pass."""
    # `hub_engine`'s `upgrade_to_head` runs Alembic's `env.py`, whose `fileConfig`
    # disables every logger that already existed (the trap `test_campaign_tick.py`
    # documents): undo it so `caplog` sees this module's line.
    logging.getLogger("rebase_core.campaigns.outcome").disabled = False
    clock = Clock(NOW)
    good = person(clean, "good@studio.it", cv=False)
    bad = person(clean, "bad@studio.it", cv=False)
    campaign = sent(clean, clock)  # `missing_cv`, action `cv`
    at = clock.at + timedelta(hours=3)
    for card in (good, bad):
        clean.execute(
            update(Freelancer).where(Freelancer.id == card.id).values(cv_size=4, cv_bytes=b"%PDF")
        )
        clean.add(
            Note(
                entity_type="freelancer",
                entity_id=card.id,
                text=f"{CV_NOTE_PREFIX}: cv.pdf",
                author="Ada",
                source="person",
                created_at=at,
            )
        )
    clean.commit()

    real_done_at = done_at

    def flaky(
        session: Session,
        recipient: CampaignRecipient,
        action: str,
        *,
        since: datetime | None = None,
        now: datetime | None = None,
    ) -> datetime | None:
        if recipient.email == "bad@studio.it":
            raise KeyError("t")
        return real_done_at(session, recipient, action, since=since, now=now)

    monkeypatch.setattr(outcome_module, "done_at", flaky)
    with caplog.at_level(logging.ERROR):
        assert stamp_outcomes(clean, now=at + timedelta(hours=1)) == 1

    clean.expire_all()
    by_email = {
        r.email: r for r in clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id)
    }
    assert by_email["good@studio.it"].action_at == at
    assert by_email["bad@studio.it"].action_at is None
    assert "KeyError" in caplog.text
    assert "bad@studio.it" not in caplog.text
