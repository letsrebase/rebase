"""«Riscrivi a chi non ha fatto niente» (spec § 4.3): a `lista` draft that follows a sent
campaign, and its list."""

from datetime import timedelta

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
from sqlalchemy import update
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import CampaignPatch, ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.campaigns.service import (
    LIST_IS_FIXED,
    NOTHING_TO_FOLLOW,
    ONLY_SENT,
    CampaignService,
)
from rebase_core.campaigns.tick import run_tick
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.models import Campaign, CampaignRecipient, Login, User

NO_PAUSE = lambda _seconds: None  # noqa: E731
GAP = timedelta(days=SETTINGS.campaign_gap_days, hours=1)


def sent_to(session: Session, clock: Clock, *emails: str, azione: str = "entrato") -> Campaign:
    """A `completo` campaign sent by one tick to complete cards at `emails`."""
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    for email in emails:
        person(session, email)
    created = service.create(who.id, draft(stato_percorso="completo", azione=azione))
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    run_tick(session, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    return session.get(Campaign, created.id)  # type: ignore[return-value]


def test_a_follow_up_is_a_lista_draft_with_the_same_action_and_mail(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    parent = sent_to(clean, clock, "ada@studio.it")
    service = CampaignService(clean, SETTINGS, clock=clock)
    follow = service.follow_up(parent.id, admin(clean).id)
    assert (follow.fonte, follow.segue_id, follow.stato) == ("lista", parent.id, "bozza")
    assert (follow.azione, follow.oggetto, follow.testo) == (
        parent.azione,
        parent.oggetto,
        parent.testo,
    )
    assert follow.nome == f"{parent.nome} · riscrivi" and follow.slug != parent.slug


def test_only_a_sent_campaign_with_someone_waiting_is_followed_up(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    draft_only = service.create(admin(clean).id, draft())
    with pytest.raises(InvalidState, match=ONLY_SENT):
        service.follow_up(draft_only.id, admin(clean).id)
    parent = sent_to(clean, clock, "ada@studio.it")
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == parent.id)
        .values(azione_at=clock.at)
    )
    clean.commit()
    with pytest.raises(InvalidState, match=NOTHING_TO_FOLLOW):
        service.follow_up(parent.id, admin(clean).id)


def test_the_list_is_who_did_nothing_read_live_and_the_gap_shows_as_a_reason(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Review Focus 2 and 3."""
    clock = Clock(NOW)
    parent = sent_to(clean, clock, "ada@studio.it", "bob@studio.it", "cleo@studio.it")
    stamped = clock.at + timedelta(hours=1)
    rows = {r.email: r for r in clean.query(CampaignRecipient).filter_by(campaign_id=parent.id)}
    rows["ada@studio.it"].azione_at = stamped  # stamped by the tick
    bob = clean.query(User).filter(User.email == "bob@studio.it").one()
    clean.add(Login(user_id=bob.id, logged_at=stamped))  # did it, not stamped yet
    clean.commit()
    service = CampaignService(clean, SETTINGS, clock=clock)
    follow = service.follow_up(parent.id, admin(clean).id)
    preview = service.audience(follow.id)
    assert [(r.email, r.escluso) for r in preview.righe] == [
        ("cleo@studio.it", f"ha ricevuto un'altra campagna il {clock.at:%d/%m}")
    ]
    clock.at += GAP
    preview = CampaignService(clean, SETTINGS, clock=clock).audience(follow.id)
    assert [(r.email, r.escluso) for r in preview.righe] == [("cleo@studio.it", None)]


def test_a_follow_up_is_scheduled_and_sent_like_any_campaign(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    parent = sent_to(clean, clock, "ada@studio.it")
    clock.at += GAP
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    follow = service.follow_up(parent.id, who.id)
    clock.at += timedelta(minutes=1)
    service.send_test(follow.id, as_admin(who), RecordingCampaignSender())
    service.schedule(follow.id, ScheduleRequest())
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.inviate == 1 and recording.sent[0].mail.to == "ada@studio.it"


def test_a_follow_up_keeps_its_list_and_action_but_its_mail_is_rewritten(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    parent = sent_to(clean, clock, "ada@studio.it")
    service = CampaignService(clean, SETTINGS, clock=clock)
    follow = service.follow_up(parent.id, admin(clean).id)
    edited = service.update(follow.id, CampaignPatch(oggetto="Ti scrivo di nuovo"))
    assert edited.oggetto == "Ti scrivo di nuovo" and edited.fonte == "lista"
    with pytest.raises(ValidationFailed, match=LIST_IS_FIXED):
        service.update(follow.id, CampaignPatch(azione="cv"))
    with pytest.raises(ValidationFailed, match=LIST_IS_FIXED):
        service.update(follow.id, CampaignPatch(fonte="stato", stato_percorso="completo"))
