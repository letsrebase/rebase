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
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import ScheduleRequest, TalentiFiltri
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.tick import (
    MAX_ATTEMPTS,
    ROW_PREPARE_ERROR,
    SEND_INTERVAL_SECONDS,
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


def scheduled_filtri(session: Session, clock: Clock, *emails: str) -> Campaign:
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    for email in emails:
        # A card *with* a CV: a filtri campaign (no completeness filter) reaches it
        # regardless, but it must not also match a `manca_cv` stato campaign scheduled
        # afterwards in the same test: the two would otherwise double-book the address.
        person(session, email)
    created = service.create(
        who.id,
        draft(fonte="filtri", stato_percorso=None, filtri=TalentiFiltri(lista="talenti")),
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
    assert (result.campagne, result.inviate) == (1, 2)
    sent = rows(clean, campaign)
    assert {r.stato for r in sent.values()} == {"inviata"}
    assert sorted(recording.keys) == sorted(str(r.id) for r in sent.values())
    assert all(m.tags["r"] in recording.keys for m in recording.sent)
    clean.refresh(campaign)
    assert campaign.stato == "inviata"


def test_a_campaign_scheduled_later_waits(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    campaign.programmata_per = clock.at + timedelta(hours=1)
    clean.commit()
    result = run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.campagne == 0


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
    assert (sent["done@studio.it"].stato, sent["done@studio.it"].motivo) == (
        "saltata",
        "ha già fatto l'azione",
    )
    assert (sent["gone@studio.it"].stato, sent["gone@studio.it"].motivo) == (
        "saltata",
        "non più in lista",
    )
    assert sent["ok@studio.it"].stato == "inviata"
    assert [m.mail.to for m in recording.sent] == ["ok@studio.it"]


def test_an_opt_out_after_freezing_is_skipped(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    clean.add(CampaignOptout(email="a@studio.it", fonte="link"))
    clean.commit()
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert rows(clean, campaign)["a@studio.it"].motivo == "si è disiscritto"


def test_a_retry_keeps_the_row_until_the_third_failure(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    flaky = RecordingCampaignSender([SendOutcome("riprova")] * MAX_ATTEMPTS)
    for _ in range(MAX_ATTEMPTS - 1):
        run_tick(clean, flaky, SETTINGS, clock=clock, pause=NO_PAUSE)
        assert rows(clean, campaign)["a@studio.it"].stato == "in_coda"
    run_tick(clean, flaky, SETTINGS, clock=clock, pause=NO_PAUSE)
    row = rows(clean, campaign)["a@studio.it"]
    assert (row.stato, row.tentativi) == ("fallita", MAX_ATTEMPTS)
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
    refused = RecordingCampaignSender([SendOutcome("fermati", dettaglio="Resend 401")] * 2)
    with caplog.at_level(logging.ERROR, logger="rebase_core.campaigns.tick"):
        result = run_tick(clean, refused, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert len(refused.sent) == 1
    assert (result.inviate, result.fallite) == (0, 0)
    assert {(r.stato, r.tentativi) for r in rows(clean, campaign).values()} == {("in_coda", 0)}
    clean.refresh(campaign)
    assert campaign.stato == "in_invio"
    assert "Resend 401" in caplog.text and "studio.it" not in caplog.text
    result = run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.inviate == 2


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
    """A tick that died after moving the campaign to `in_invio`: the next one takes it."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    campaign.stato = "in_invio"
    clean.commit()
    result = run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.inviate == 1


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
    motivo = rows(clean, second_campaign)["a@studio.it"].motivo  # type: ignore[arg-type]
    assert motivo.startswith("ha ricevuto un'altra campagna")


def test_a_cancelled_campaign_is_never_marked_sent(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    CampaignService(clean, SETTINGS, clock=clock).cancel(campaign.id)
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.refresh(campaign)
    assert campaign.stato == "annullata"


def test_a_campaign_moved_back_to_draft_before_claiming_is_left_alone(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Controller ruling R12: the unlocked scan that finds a due campaign never flips it
    itself. Between that scan and the claim, an admin can still take it back to `bozza`
    (deleting its frozen list); the claim re-reads the row under its own lock and, no
    longer seeing `programmata` and due, leaves it exactly where the admin put it,
    never `in_invio`. Calls the internal claim directly with the campaign's id, which is
    all a real tick pass carries between the scan and the claim."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    CampaignService(clean, SETTINGS, clock=clock).back_to_draft(campaign.id)
    claimed = _claim(clean, campaign.id, clock.at)
    assert claimed is None
    clean.refresh(campaign)
    assert campaign.stato == "bozza"
    assert campaign.programmata_per is None


def test_a_campaign_cancelled_between_two_rows_ends_annullata_not_inviata(
    clean: Session,  # noqa: F811  (fixture)
    hub_engine: Engine,
) -> None:
    """Controller ruling R13: claiming a campaign releases its lock for the rest of the
    send (only the claim itself, and the final transition, hold it), so a `cancel()`
    from a second session can land between two rows of the same send. The row already
    sent stays `inviata`; the one still queued when the cancel lands is turned
    `saltata`; the campaign itself ends `annullata`, never `inviata`. The `pause` hook
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
    assert campaign.stato == "annullata"
    sent = rows(clean, campaign)
    inviata = [r for r in sent.values() if r.stato == "inviata"]
    saltata = [r for r in sent.values() if r.stato == "saltata"]
    assert len(inviata) == 1
    assert [(r.stato, r.motivo) for r in saltata] == [("saltata", "campagna annullata")]
    assert len(recording.sent) == 1


def test_a_row_whose_checks_raise_is_marked_fallita_the_rest_still_sends(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Controller ruling R14. `render` itself only fails on a campaign-wide setting (a
    `pigro` button, refused for every row alike), so it can't isolate to a single row;
    `done_at` can, and is named right alongside `render` in the finding: it indexes
    `recipient.prima["t"]` unconditionally, and a broken snapshot on just one row (data
    this pass didn't choose, not a loop bug) must not be able to fail every row after
    it. One row's `prima` is wiped straight in the table, bypassing `schedule()`'s own
    snapshot; the other row's is untouched."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "broken@studio.it", "ok@studio.it")
    clean.execute(
        update(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.email == "broken@studio.it",
        )
        .values(prima={})
    )
    clean.commit()
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    sent = rows(clean, campaign)
    assert (sent["broken@studio.it"].stato, sent["broken@studio.it"].motivo) == (
        "fallita",
        ROW_PREPARE_ERROR,
    )
    assert sent["ok@studio.it"].stato == "inviata"
    assert (result.fallite, result.inviate) == (1, 1)
    assert [m.mail.to for m in recording.sent] == ["ok@studio.it"]
    clean.refresh(campaign)
    assert campaign.stato == "inviata"


def test_a_campaign_whose_candidates_raises_does_not_stop_a_second_due_campaign(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Controller ruling R14: corrupt stored `filtri` (data this pass didn't choose)
    makes `candidates()` raise while this campaign's own membership set is rebuilt for
    the send, before any row is touched. The tick rolls that one campaign's work back
    and leaves it `in_invio` for a later pass, without stopping the second due campaign
    in the same pass."""
    clock = Clock(NOW)
    broken = scheduled_filtri(clean, clock, "a@studio.it")
    clean.execute(update(Campaign).where(Campaign.id == broken.id).values(filtri={"lista": "boom"}))
    clean.commit()
    healthy = scheduled(clean, clock, "b@studio.it")

    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)

    assert [m.mail.to for m in recording.sent] == ["b@studio.it"]
    clean.refresh(broken)
    assert broken.stato == "in_invio"
    assert rows(clean, broken)["a@studio.it"].stato == "in_coda"
    clean.refresh(healthy)
    assert healthy.stato == "inviata"
