"""The loop's one pass (spec § 5.3, § 5.4)."""

from datetime import UTC, datetime, timedelta

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
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.tick import MAX_ATTEMPTS, _claim, run_tick
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
    longer seeing `programmata` and due, leaves it exactly where the admin put it —
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
    — the same one that throttles real sends between rows — is where that second
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
