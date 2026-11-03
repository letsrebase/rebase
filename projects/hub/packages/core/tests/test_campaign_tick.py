"""The loop's one pass (spec § 5.3, § 5.4)."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from campaign_fixtures import (  # noqa: F401  (fixture)
    NOW,
    SETTINGS,
    Clock,
    admin,
    as_admin,
    clean,
    draft,
    person,
)
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from rebase_core.campaigns.audience import REASON_DISCARDED
from rebase_core.campaigns.optouts import OptoutService
from rebase_core.campaigns.schemas import ScheduleRequest, TalentsFilters
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.tick import (
    MAX_ATTEMPTS,
    ROW_PREPARE_ERROR,
    SEND_INTERVAL_SECONDS,
    STALLED_KEY,
    STALLED_LIST,
    STALLED_SHARE_DAY,
    STALLED_SHARE_MONTH,
    _claim,
    run_tick,
)
from rebase_core.db import session_factory
from rebase_core.models import Campaign, CampaignOptout, CampaignRecipient, Freelancer, User

NO_PAUSE = lambda _seconds: None  # noqa: E731


def scheduled(session: Session, clock: Clock, *emails: str) -> Campaign:
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    for email in emails:
        person(session, email, cv=False)
    created = service.create(who.id, draft())
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    return session.get(Campaign, created.id)  # type: ignore[return-value]


def scheduled_filters(session: Session, clock: Clock, *emails: str) -> Campaign:
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    for email in emails:
        # A card *with* a CV: a filtri campaign (no completeness filter) reaches it
        # regardless, but it must not also match a `missing_cv` stato campaign scheduled
        # afterwards in the same test: the two would otherwise double-book the address.
        person(session, email)
    created = service.create(
        who.id,
        draft(source="filters", journey_status=None, filters=TalentsFilters(list="talents")),
    )
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    return session.get(Campaign, created.id)  # type: ignore[return-value]


def rows(session: Session, campaign: Campaign) -> dict[str, CampaignRecipient]:
    session.expire_all()
    return {r.email: r for r in session.query(CampaignRecipient).filter_by(campaign_id=campaign.id)}


def test_a_due_campaign_is_sent_one_keyed_call_per_person(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert (result.campaigns, result.sent) == (1, 2)
    sent = rows(clean, campaign)
    assert {r.status for r in sent.values()} == {"sent"}
    assert sorted(recording.keys) == sorted(str(r.id) for r in sent.values())
    assert all(m.tags["r"] in recording.keys for m in recording.sent)
    clean.refresh(campaign)
    assert campaign.status == "sent"


def test_a_campaign_scheduled_later_waits(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    campaign.scheduled_for = clock.at + timedelta(hours=1)
    clean.commit()
    result = run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.campaigns == 0


def card_of(session: Session, email: str) -> Freelancer:
    return (
        session.query(Freelancer)
        .join(User, User.id == Freelancer.user_id)
        .filter(User.email == email)
        .one()
    )


def test_a_card_completed_or_deleted_after_freezing_is_skipped_not_sent(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Review Focus 4."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "done@studio.it", "gone@studio.it", "ok@studio.it")
    done = card_of(clean, "done@studio.it")
    done.cv_size, done.cv_filename, done.cv_bytes = 4, "cv.pdf", b"%PDF"
    card_of(clean, "gone@studio.it").deleted_at = datetime.now(UTC)
    clean.commit()
    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    sent = rows(clean, campaign)
    assert (sent["done@studio.it"].status, sent["done@studio.it"].reason) == (
        "skipped",
        "ha già fatto l'azione",
    )
    assert (sent["gone@studio.it"].status, sent["gone@studio.it"].reason) == (
        "skipped",
        "non più in lista",
    )
    assert sent["ok@studio.it"].status == "sent"
    assert [m.mail.to for m in recording.sent] == ["ok@studio.it"]


def test_an_opt_out_after_freezing_is_skipped(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    clean.add(CampaignOptout(email="a@studio.it", source="link"))
    clean.commit()
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert rows(clean, campaign)["a@studio.it"].reason == "si è disiscritto"


def test_an_opt_out_between_two_rows_of_the_same_pass_is_skipped(
    clean: Session,  # noqa: F811  (fixture)
    hub_engine: Engine,
) -> None:
    """Spec § 5.3: the exclusions are read right before each mail, not once per pass. A
    pass sends one mail a second, so the person who unsubscribes while it runs must not
    get the mail still queued for them. The `pause` hook between two rows is where the
    opt-out lands, from a second session, as the unsubscribe route would write it."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    other = session_factory(hub_engine)()
    done = {"optout": False}

    def unsubscribe_after_first_row(_seconds: float) -> None:
        if done["optout"]:
            return
        done["optout"] = True
        queued = other.scalars(
            select(CampaignRecipient.email).where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status == "in_coda",
            )
        ).one()
        OptoutService(other).record(queued, "link", None)
        other.close()

    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=unsubscribe_after_first_row)
    sent = rows(clean, campaign)
    assert sorted((r.status, r.reason) for r in sent.values()) == [
        ("sent", None),
        ("skipped", "si è disiscritto"),
    ]
    assert len(recording.sent) == 1


def test_a_retry_keeps_the_row_until_the_third_failure(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    flaky = RecordingCampaignSender([SendOutcome("retry")] * MAX_ATTEMPTS)
    for _ in range(MAX_ATTEMPTS - 1):
        run_tick(clean, flaky, SETTINGS, clock=clock, pause=NO_PAUSE)
        assert rows(clean, campaign)["a@studio.it"].status == "in_coda"
    run_tick(clean, flaky, SETTINGS, clock=clock, pause=NO_PAUSE)
    row = rows(clean, campaign)["a@studio.it"]
    assert (row.status, row.attempts) == ("failed", MAX_ATTEMPTS)
    assert len(set(flaky.keys)) == 1  # the same key every time


def test_a_refused_key_stops_the_campaign_and_burns_nothing(
    clean: Session,  # noqa: F811  (fixture)
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 401/403 (a revoked or restricted key, a domain no longer verified) would be
    the same answer for every row: the pass stops at the first, leaves every row
    `in_coda` with no attempt counted, and logs the status alone. Once the key is
    fixed, the next tick sends them all."""
    # `hub_engine`'s `upgrade_to_head` runs Alembic's `env.py`, whose `fileConfig`
    # disables every logger that already existed (the trap `test_member_api.py`
    # documents): undo it so `caplog` sees this module's line.
    logging.getLogger("rebase_core.campaigns.tick").disabled = False
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    refused = RecordingCampaignSender([SendOutcome("stop", detail="Resend 401")] * 2)
    with caplog.at_level(logging.ERROR, logger="rebase_core.campaigns.tick"):
        result = run_tick(clean, refused, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert len(refused.sent) == 1
    assert (result.sent, result.failed) == (0, 0)
    assert {(r.status, r.attempts) for r in rows(clean, campaign).values()} == {("in_coda", 0)}
    clean.refresh(campaign)
    assert campaign.status == "sending"
    assert "Resend 401" in caplog.text and "studio.it" not in caplog.text
    result = run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.sent == 2


def test_the_loop_leaves_a_second_between_two_mails(clean: Session) -> None:  # noqa: F811  (fixture)
    """Resend allows two requests a second per team, and the magic link shares them:
    a campaign takes at most one."""
    clock = Clock(NOW)
    scheduled(clean, clock, "a@studio.it", "b@studio.it")
    pauses: list[float] = []
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=pauses.append)
    assert SEND_INTERVAL_SECONDS == 1.0
    assert pauses == [1.0, 1.0]


def test_a_send_cut_short_resumes_on_the_next_tick(clean: Session) -> None:  # noqa: F811  (fixture)
    """A tick that died after moving the campaign to `sending`: the next one takes it."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    campaign.status = "sending"
    clean.commit()
    result = run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.sent == 1


def test_two_campaigns_in_the_same_minute_reach_a_person_once(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    scheduled(clean, clock, "a@studio.it")
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    second = service.create(who.id, draft(nome="Seconda"))
    service.send_test(second.id, as_admin(who), RecordingCampaignSender())
    service.schedule(second.id, ScheduleRequest())
    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert [m.mail.to for m in recording.sent] == ["a@studio.it"]
    second_campaign = clean.get(Campaign, second.id)
    reason = rows(clean, second_campaign)["a@studio.it"].reason  # type: ignore[arg-type]
    assert reason.startswith("ha ricevuto un'altra campagna")


def test_a_cancelled_campaign_is_never_marked_sent(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    CampaignService(clean, SETTINGS, clock=clock).cancel(campaign.id)
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.refresh(campaign)
    assert campaign.status == "cancelled"


def test_a_campaign_moved_back_to_draft_before_claiming_is_left_alone(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Controller ruling R12: the unlocked scan that finds a due campaign never flips it
    itself. Between that scan and the claim, an admin can still take it back to `draft`
    (deleting its frozen list); the claim re-reads the row under its own lock and, no
    longer seeing `scheduled` and due, leaves it exactly where the admin put it,
    never `sending`. Calls the internal claim directly with the campaign's id, which is
    all a real tick pass carries between the scan and the claim."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    CampaignService(clean, SETTINGS, clock=clock).back_to_draft(campaign.id)
    claimed = _claim(clean, campaign.id, clock.at)
    assert claimed is None
    clean.refresh(campaign)
    assert campaign.status == "draft"
    assert campaign.scheduled_for is None


def test_to_campaign_cancelled_between_two_rows_ends_cancelled_not_sent(
    clean: Session,  # noqa: F811  (fixture)
    hub_engine: Engine,
) -> None:
    """Controller ruling R13: claiming a campaign releases its lock for the rest of the
    send (only the claim itself, and the final transition, hold it), so a `cancel()`
    from a second session can land between two rows of the same send. The row already
    sent stays `sent`; the one still queued when the cancel lands is turned
    `skipped`; the campaign itself ends `cancelled`, never `sent`. The `pause` hook
    (the same one that throttles real sends between rows) is where that second
    session's call lands, deterministically, after the first row and before the
    second."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    other = session_factory(hub_engine)()
    cancelled = {"done": False}

    def cancel_after_first_row(_seconds: float) -> None:
        if not cancelled["done"]:
            cancelled["done"] = True
            CampaignService(other, SETTINGS, clock=Clock(clock.at)).cancel(campaign.id)
            other.close()

    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=cancel_after_first_row)
    clean.refresh(campaign)
    assert campaign.status == "cancelled"
    sent = rows(clean, campaign)
    inviata = [r for r in sent.values() if r.status == "sent"]
    skipped = [r for r in sent.values() if r.status == "skipped"]
    assert len(inviata) == 1
    assert [(r.status, r.reason) for r in skipped] == [("skipped", "campagna annullata")]
    assert len(recording.sent) == 1


def test_to_row_whose_checks_raise_is_marked_failed_the_rest_still_sends(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Controller ruling R14. `render` itself only fails on a campaign-wide setting (a
    `pigro` button, refused for every row alike), so it can't isolate to a single row;
    `done_at` can, and is named right alongside `render` in the finding: it indexes
    `recipient.before["t"]` unconditionally, and a broken snapshot on just one row (data
    this pass didn't choose, not a loop bug) must not be able to fail every row after
    it. One row's `before` is wiped straight in the table, bypassing `schedule()`'s own
    snapshot; the other row's is untouched."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "broken@studio.it", "ok@studio.it")
    clean.execute(
        update(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.email == "broken@studio.it",
        )
        .values(before={})
    )
    clean.commit()
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    sent = rows(clean, campaign)
    assert (sent["broken@studio.it"].status, sent["broken@studio.it"].reason) == (
        "failed",
        ROW_PREPARE_ERROR,
    )
    assert sent["ok@studio.it"].status == "sent"
    assert (result.failed, result.sent) == (1, 1)
    assert [m.mail.to for m in recording.sent] == ["ok@studio.it"]
    clean.refresh(campaign)
    assert campaign.status == "sent"


def test_a_campaign_whose_candidates_raises_does_not_stop_a_second_due_campaign(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Controller ruling R14: corrupt stored `filters` (data this pass didn't choose)
    makes `candidates()` raise while this campaign's own membership set is rebuilt for
    the send, before any row is touched. The tick rolls that one campaign's work back
    and leaves it `sending` for a later pass, without stopping the second due campaign
    in the same pass."""
    clock = Clock(NOW)
    broken = scheduled_filters(clean, clock, "a@studio.it")
    clean.execute(update(Campaign).where(Campaign.id == broken.id).values(filters={"list": "boom"}))
    clean.commit()
    healthy = scheduled(clean, clock, "b@studio.it")

    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)

    assert [m.mail.to for m in recording.sent] == ["b@studio.it"]
    clean.refresh(broken)
    assert broken.status == "sending"
    assert rows(clean, broken)["a@studio.it"].status == "in_coda"
    clean.refresh(healthy)
    assert healthy.status == "sent"


def test_a_row_with_only_a_bare_resend_id_falls_back_to_the_tick_clock(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """REB-522: a tick that dies after Resend accepted a mail but before its own commit
    leaves the row `in_coda` with none of the webhook's columns written yet. Once the
    webhook lands (matched by the row's own `r` tag, independently of that dead tick),
    the next tick must trust it rather than call Resend again. `resend_id` set with no
    webhook timestamp at all is not a shape `webhook.py` produces today (every branch
    that writes `resend_id` also writes one of the four moments first): this row is
    built straight in the table to pin the defensive fallback regardless."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    row = rows(clean, campaign)["a@studio.it"]
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.id == row.id)
        .values(status="in_coda", resend_id="re_abc123", sent_at=None)
    )
    clean.commit()
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert recording.sent == []
    sent = rows(clean, campaign)["a@studio.it"]
    assert sent.status == "sent"
    assert sent.sent_at == clock.at
    assert result.sent == 1


def test_a_row_with_only_a_first_click_is_stamped_with_the_click_time(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """A click, bounce or complaint webhook can beat `email.delivered` and fill
    `resend_id` with no `delivered_at` yet: `sent_at` must still take that event's
    own moment, not the retry tick's `now`, or outcome stamping and the gap rule would
    both measure "since" the wrong time after a long outage."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    row = rows(clean, campaign)["a@studio.it"]
    clicked_at = clock.at - timedelta(hours=26)
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.id == row.id)
        .values(status="in_coda", resend_id="re_abc123", first_click_at=clicked_at, sent_at=None)
    )
    clean.commit()
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert recording.sent == []
    sent = rows(clean, campaign)["a@studio.it"]
    assert sent.status == "sent"
    assert sent.sent_at == clicked_at
    assert result.sent == 1


def test_a_row_with_two_webhook_moments_is_stamped_with_the_earliest(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """`delivered_at` is written before `first_click_at` in the model, but nothing
    guarantees the events arrive in that order: a click event can carry an earlier
    timestamp than a delivery one still in flight. `_earliest_webhook_moment` takes
    the minimum across all four columns, not the first one written."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    row = rows(clean, campaign)["a@studio.it"]
    clicked_at = clock.at - timedelta(hours=27)
    delivered_at = clock.at - timedelta(hours=26)
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.id == row.id)
        .values(
            status="in_coda",
            resend_id="re_abc123",
            first_click_at=clicked_at,
            delivered_at=delivered_at,
            sent_at=None,
        )
    )
    clean.commit()
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert recording.sent == []
    sent = rows(clean, campaign)["a@studio.it"]
    assert sent.status == "sent"
    assert sent.sent_at == clicked_at
    assert result.sent == 1


def test_to_row_the_webhook_already_marked_delivered_is_not_sent_twice(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Same gap, but only `delivered_at` came back (`email.delivered`, no `resend_id`
    fallback needed): `sent_at` is stamped from that delivery moment, not from this
    retry tick's own clock, so outcome stamping still measures "since" the real send."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    row = rows(clean, campaign)["a@studio.it"]
    delivered_at = clock.at - timedelta(hours=26)
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.id == row.id)
        .values(status="in_coda", delivered_at=delivered_at, sent_at=None)
    )
    clean.commit()
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert recording.sent == []
    sent = rows(clean, campaign)["a@studio.it"]
    assert sent.status == "sent"
    assert sent.sent_at == delivered_at
    assert result.sent == 1


def test_a_plain_queued_row_still_sends_once(clean: Session) -> None:  # noqa: F811  (fixture)
    """The webhook check must not swallow the ordinary path: a row with neither
    `resend_id` nor `delivered_at` is sent exactly once, as before REB-522."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert [m.mail.to for m in recording.sent] == ["a@studio.it"]
    assert result.sent == 1
    sent = rows(clean, campaign)["a@studio.it"]
    assert sent.status == "sent"
    assert sent.resend_id is not None


def test_a_stamping_error_is_logged_and_the_send_still_happens(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review Focus 4: the outcome never stops a send."""
    import rebase_core.campaigns.tick as tick_module

    def broken(_session: Session, *, now: datetime) -> int:
        raise KeyError("t")

    monkeypatch.setattr(tick_module, "stamp_outcomes", broken)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    with caplog.at_level(logging.ERROR):
        result = run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.sent == 1 and result.stampate == 0
    assert rows(clean, campaign)["a@studio.it"].status == "sent"
    assert "outcome stamping failed this tick: KeyError" in caplog.text
    assert "a@studio.it" not in caplog.text


def test_a_refused_key_is_stored_on_the_campaign_until_a_mail_leaves(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """REB-524: a 401/403 leaves the campaign `sending`, retried every pass, while the
    page went on saying «Parte il…». The pass stores when and why it stopped, the page
    shows «Invio fermo», and the first mail that leaves afterwards clears it."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    refused = RecordingCampaignSender([SendOutcome("stop", detail="Resend 403")])
    run_tick(clean, refused, SETTINGS, clock=clock, pause=NO_PAUSE)
    stalled = CampaignService(clean, SETTINGS, clock=clock).detail(campaign.id).campaign
    assert (stalled.status, stalled.stalled_reason, stalled.stalled_at) == (
        "sending",
        STALLED_KEY,
        clock.at,
    )
    assert STALLED_KEY == "Resend rifiuta l'invio: controlla la chiave e il dominio del mittente"
    clock.at += timedelta(minutes=1)
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    sent = CampaignService(clean, SETTINGS, clock=clock).detail(campaign.id).campaign
    assert (sent.status, sent.stalled_reason, sent.stalled_at) == ("sent", None, None)


def test_a_stall_is_cleared_by_the_first_mail_that_leaves_even_mid_list(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """The key is fixed and the next pass sends one mail, then Resend times out on the
    second: the send is moving again, so the campaign no longer reads «Invio fermo»."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    run_tick(
        clean,
        RecordingCampaignSender([SendOutcome("stop", detail="Resend 401")]),
        SETTINGS,
        clock=clock,
        pause=NO_PAUSE,
    )
    flaky = RecordingCampaignSender(
        [SendOutcome("accepted", "re-1"), SendOutcome("retry", detail="Resend 503")]
    )
    run_tick(clean, flaky, SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.refresh(campaign)
    assert (campaign.status, campaign.stalled_reason, campaign.stalled_at) == (
        "sending",
        None,
        None,
    )


def test_a_campaign_whose_list_cannot_be_read_is_marked_stalled(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """The other stop that does not fix itself: corrupt stored `filters` make every pass
    roll the campaign back (R14). It says so on the campaign, and the healthy one due in
    the same pass does not."""
    clock = Clock(NOW)
    broken = scheduled_filters(clean, clock, "a@studio.it")
    clean.execute(update(Campaign).where(Campaign.id == broken.id).values(filters={"list": "boom"}))
    clean.commit()
    healthy = scheduled(clean, clock, "b@studio.it")
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.refresh(broken)
    clean.refresh(healthy)
    assert (broken.status, broken.stalled_reason, broken.stalled_at) == (
        "sending",
        STALLED_LIST,
        clock.at,
    )
    assert (healthy.status, healthy.stalled_reason) == ("sent", None)


def test_a_card_turned_down_after_freezing_is_skipped_with_its_reason(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """REB-524: «scartato» is checked again right before the mail, like an opt-out."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    user = clean.query(User).filter(User.email == "a@studio.it").one()
    clean.execute(
        update(Freelancer).where(Freelancer.user_id == user.id).values(status="discarded")
    )
    clean.commit()
    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert [m.mail.to for m in recording.sent] == ["b@studio.it"]
    skipped = rows(clean, campaign)["a@studio.it"]
    assert (skipped.status, skipped.reason) == ("skipped", REASON_DISCARDED)


def test_a_send_stalled_for_many_passes_keeps_the_first_stop(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Greptile on #473: every minute's retry rewrote `stalled_at`, so a send stopped for
    hours read as stopped a minute ago. The first stop stands; the reason follows the
    latest pass."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    refused = RecordingCampaignSender([SendOutcome("stop", detail="Resend 401")] * 2)
    run_tick(clean, refused, SETTINGS, clock=clock, pause=NO_PAUSE)
    first = clock.at
    clock.at += timedelta(minutes=1)
    run_tick(clean, refused, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert len(refused.sent) == 2
    clean.expire_all()
    stopped = clean.get(Campaign, campaign.id)
    assert stopped is not None
    assert (stopped.stalled_at, stopped.stalled_reason) == (first, STALLED_KEY)


def test_a_new_reason_replaces_the_old_one_but_not_the_first_stop(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    campaign = scheduled_filters(clean, clock, "a@studio.it")
    run_tick(
        clean,
        RecordingCampaignSender([SendOutcome("stop", detail="Resend 403")]),
        SETTINGS,
        clock=clock,
        pause=NO_PAUSE,
    )
    first = clock.at
    clean.execute(
        update(Campaign).where(Campaign.id == campaign.id).values(filters={"list": "boom"})
    )
    clean.commit()
    clock.at += timedelta(minutes=1)
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.expire_all()
    stopped = clean.get(Campaign, campaign.id)
    assert stopped is not None
    assert (stopped.stalled_at, stopped.stalled_reason) == (first, STALLED_LIST)


def test_a_failure_after_the_list_is_read_does_not_say_the_list_is_unreadable(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Greptile and CodeRabbit on #473: «la lista non si legge» was written for any
    exception out of the campaign's send, `_finish` and the sender included. Only the
    list's own read records it; anything else is logged and rolled back, as before."""
    clock = Clock(NOW)
    finishing = scheduled(clean, clock, "a@studio.it")

    def broken_finish(*_args: object) -> None:
        raise RuntimeError("commit lost")

    monkeypatch.setattr("rebase_core.campaigns.tick._finish", broken_finish)
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.expire_all()
    after_finish = clean.get(Campaign, finishing.id)
    assert after_finish is not None
    assert (after_finish.status, after_finish.stalled_at, after_finish.stalled_reason) == (
        "sending",
        None,
        None,
    )


def test_a_sender_that_raises_does_not_say_the_list_is_unreadable(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    class Exploding(RecordingCampaignSender):
        def send(self, rendered: object, idempotency_key: str) -> SendOutcome:  # type: ignore[override]
            raise RuntimeError("socket closed")

    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    run_tick(clean, Exploding(), SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.expire_all()
    still = clean.get(Campaign, campaign.id)
    assert still is not None
    assert (still.status, still.stalled_reason) == ("sending", None)


@pytest.mark.parametrize(
    ("delivered_after_the_stop", "stall_stays"), [(True, False), (False, True)]
)
def test_a_mail_the_webhook_confirms_clears_the_stall_only_if_it_left_after_the_stop(
    clean: Session,  # noqa: F811  (fixture)
    delivered_after_the_stop: bool,
    stall_stays: bool,
) -> None:
    """CodeRabbit and Greptile on #473: the webhook-recovery branch (REB-522) marks a
    queued row sent without calling Resend. A mail the webhook dates after the stop
    means the send moves again, so it clears the stall as an accepted send does, even
    when the next row only gets a `retry`. One it dates before the stop left before
    Resend started refusing, and says nothing about the refusal: the stall stays."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    run_tick(
        clean,
        RecordingCampaignSender([SendOutcome("stop", detail="Resend 401")]),
        SETTINGS,
        clock=clock,
        pause=NO_PAUSE,
    )
    stopped_at = clock.at
    first = (
        clean.query(CampaignRecipient)
        .filter_by(campaign_id=campaign.id)
        .order_by(CampaignRecipient.id)
        .first()
    )
    assert first is not None
    offset = timedelta(seconds=30)
    first.delivered_at = stopped_at + offset if delivered_after_the_stop else stopped_at - offset
    clean.commit()
    clock.at += timedelta(minutes=1)
    flaky = RecordingCampaignSender([SendOutcome("retry", detail="Resend 503")])
    run_tick(clean, flaky, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert len(flaky.sent) == 1  # only the second row reached Resend
    clean.expire_all()
    after = clean.get(Campaign, campaign.id)
    assert after is not None
    recovered = clean.get(CampaignRecipient, first.id)
    assert recovered is not None and recovered.status == "sent"
    expected = (stopped_at, STALLED_KEY) if stall_stays else (None, None)
    assert (after.status, after.stalled_at, after.stalled_reason) == ("sending", *expected)


SMALL_DAY = SETTINGS.model_copy(update={"resend_daily_share": 5, "campaign_daily_reserve": 2})


def test_to_list_past_the_daily_ceiling_sends_across_two_days_with_no_failed(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """REB-779: 5 a day less 2 reserved leaves 3. The fourth row stays `in_coda` with no
    attempt spent, the page says which day the rest leaves, and the next morning's
    pass sends it and clears the note."""
    clock = Clock(NOW)
    emails = [f"p{n}@studio.it" for n in range(5)]
    campaign = scheduled(clean, clock, *emails)
    sender = RecordingCampaignSender()
    first = run_tick(clean, sender, SMALL_DAY, clock=clock, pause=NO_PAUSE)
    assert (first.sent, first.failed, len(sender.sent)) == (3, 0, 3)
    left = [r for r in rows(clean, campaign).values() if r.status == "in_coda"]
    assert len(left) == 2 and {r.attempts for r in left} == {0}
    held = CampaignService(clean, SMALL_DAY, clock=clock).detail(campaign.id).campaign
    assert held.status == "sending"
    assert held.stalled_reason == (
        "limite di 3 mail al giorno raggiunto: il resto parte il 26/09 dalle 00:00 (ora di Roma)"
    )
    # Later the same day nothing leaves and nothing is spent.
    clock.at += timedelta(hours=10)
    again = run_tick(clean, sender, SMALL_DAY, clock=clock, pause=NO_PAUSE)
    assert (again.sent, again.failed, len(sender.sent)) == (0, 0, 3)
    # After midnight in Rome (22:00 UTC is 00:00 of the 26th).
    clock.at = datetime(2026, 9, 25, 22, 1, tzinfo=UTC)
    second = run_tick(clean, sender, SMALL_DAY, clock=clock, pause=NO_PAUSE)
    assert (second.sent, second.failed, len(sender.sent)) == (2, 0, 5)
    done = CampaignService(clean, SMALL_DAY, clock=clock).detail(campaign.id).campaign
    assert (done.status, done.stalled_reason, done.stalled_at) == ("sent", None, None)
    assert {r.status for r in rows(clean, campaign).values()} == {"sent"}


def test_the_ceiling_counts_every_campaign_by_the_day_in_rome(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """A second campaign shares the day's room with the first, and a mail sent at 23:30
    in Rome the evening before does not count against the next day."""
    clock = Clock(NOW)
    first = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    second = scheduled_filters(clean, clock, "c@studio.it", "d@studio.it")
    sender = RecordingCampaignSender()
    run_tick(clean, sender, SMALL_DAY, clock=clock, pause=NO_PAUSE)
    assert len(sender.sent) == 3
    states = [r.status for c in (first, second) for r in rows(clean, c).values()]
    assert states.count("sent") == 3 and "failed" not in states and "in_coda" in states
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.status == "sent")
        .values(sent_at=datetime(2026, 9, 24, 21, 30, tzinfo=UTC))  # 23:30 in Rome
    )
    clean.commit()
    run_tick(clean, sender, SMALL_DAY, clock=clock, pause=NO_PAUSE)
    assert len(sender.sent) > 3
    after = [r.status for c in (first, second) for r in rows(clean, c).values()]
    assert after.count("sent") == len(sender.sent) and "failed" not in after


def test_to_share_429_stops_the_campaign_without_spending_an_attempt(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    full = RecordingCampaignSender([SendOutcome("quota", detail="daily_quota_exceeded")])
    result = run_tick(clean, full, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert (result.sent, result.failed, len(full.sent)) == (0, 0, 1)
    assert {(r.status, r.attempts) for r in rows(clean, campaign).values()} == {("in_coda", 0)}
    stalled = CampaignService(clean, SETTINGS, clock=clock).detail(campaign.id).campaign
    assert (stalled.status, stalled.stalled_reason) == ("sending", STALLED_SHARE_DAY)
    clock.at += timedelta(minutes=1)
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    done = CampaignService(clean, SETTINGS, clock=clock).detail(campaign.id).campaign
    assert (done.status, done.stalled_reason) == ("sent", None)


def test_to_monthly_share_says_so(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    full = RecordingCampaignSender([SendOutcome("quota", detail="monthly_quota_exceeded")])
    run_tick(clean, full, SETTINGS, clock=clock, pause=NO_PAUSE)
    stalled = CampaignService(clean, SETTINGS, clock=clock).detail(campaign.id).campaign
    assert stalled.stalled_reason == STALLED_SHARE_MONTH


def test_to_reserve_that_takes_the_whole_share_is_refused_at_start_up() -> None:
    with pytest.raises(ValueError, match="REBASE_CAMPAIGN_DAILY_RESERVE"):
        SETTINGS.model_validate({**SETTINGS.model_dump(), "resend_daily_share": 10})
