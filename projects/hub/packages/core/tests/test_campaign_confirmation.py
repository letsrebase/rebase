"""REB-911: an address nobody proved stays out of campaigns, gets one confirmation mail
before its first campaign, and joins the campaigns after the link or a login."""

from datetime import timedelta

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
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.campaigns.audience import REASON_UNCONFIRMED, build_audience
from rebase_core.campaigns.confirmation import confirm, request, send_pending
from rebase_core.campaigns.schemas import ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.tick import run_tick
from rebase_core.mail import RecordingSender
from rebase_core.models import Campaign, CampaignRecipient, Login, User

ADDRESS = "ada@acme.it"


def prepared(session: Session, clock: Clock) -> tuple[CampaignService, Campaign]:
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    created = service.create(who.id, draft(journey_status="complete"))
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    return service, session.get(Campaign, created.id)  # type: ignore[return-value]


def user_of(session: Session, email: str) -> User:
    session.expire_all()
    return session.scalars(select(User).where(User.email == email)).one()


def link_in(mailer: RecordingSender) -> str:
    text = mailer.sent[-1].text
    return next(line for line in text.splitlines() if "/confirm?t=" in line).split("t=")[1]


def test_the_preview_lists_an_unconfirmed_card_with_its_reason(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, ADDRESS, confirmed=False)
    person(clean, "bruno@acme.it")
    company(clean, "carla@acme.it", confirmed=False)
    service, campaign = prepared(clean, Clock(NOW))
    preview = service.audience(campaign.id)
    reasons = {line.email: line.excluded for line in preview.lines}
    assert reasons[ADDRESS] == REASON_UNCONFIRMED == "indirizzo non confermato"
    assert reasons["bruno@acme.it"] is None
    assert preview.included == 1


def test_a_login_counts_as_a_confirmation(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, ADDRESS, confirmed=False, logins=1)
    campaign = prepared(clean, Clock(NOW))[1]
    rows = build_audience(clean, campaign, now=NOW, gap_days=3)
    assert [r.excluded for r in rows] == [None]


def test_a_signup_with_no_card_is_not_held_back(clean: Session) -> None:  # noqa: F811  (fixture)
    lead(clean, ADDRESS)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    created = service.create(admin(clean).id, draft(journey_status="lead", action="signed_in"))
    rows = build_audience(clean, clean.get(Campaign, created.id), now=NOW, gap_days=3)  # type: ignore[arg-type]
    assert [r.excluded for r in rows] == [None]


def test_scheduling_leaves_the_card_out_and_the_tick_sends_one_confirmation(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    person(clean, ADDRESS, confirmed=False)
    person(clean, "bruno@acme.it")
    clock = Clock(NOW)
    service, campaign = prepared(clean, clock)
    service.schedule(campaign.id, ScheduleRequest())
    assert clean.scalars(select(CampaignRecipient.email)).all() == ["bruno@acme.it"]
    assert user_of(clean, ADDRESS).confirmation_requested_at is not None
    assert user_of(clean, ADDRESS).confirmation_sent_at is None

    mailer, sender = RecordingSender(), RecordingCampaignSender()
    clock.at += timedelta(minutes=1)
    first = run_tick(clean, sender, SETTINGS, clock=clock, pause=lambda _s: None, mailer=mailer)
    assert first.confirmations == 1
    assert [m.to for m in mailer.sent] == [ADDRESS]
    mail = mailer.sent[0]
    assert mail.subject == "Conferma il tuo indirizzo su rebase"
    assert "Conferma l'indirizzo" in (mail.html or "")
    assert [m.mail.to for m in sender.sent] == ["bruno@acme.it"]  # the campaign itself, once

    # A second campaign that leaves the same card out asks again: still one mail ever.
    clock.at += timedelta(days=5)
    second, second_campaign = prepared(clean, clock)
    second.schedule(second_campaign.id, ScheduleRequest())
    run_tick(clean, sender, SETTINGS, clock=clock, pause=lambda _s: None, mailer=mailer)
    assert len(mailer.sent) == 1


def test_a_confirmed_card_is_in_the_next_campaign(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, ADDRESS, confirmed=False)
    clock = Clock(NOW)
    service, campaign = prepared(clean, clock)
    mailer = RecordingSender()
    request(clean, [ADDRESS], now=clock())
    clean.commit()
    assert send_pending(clean, mailer, SETTINGS, clock=clock, pause=lambda _s: None) == 1
    assert confirm(clean, link_in(mailer), now=clock()) is True
    rows = build_audience(clean, campaign, now=clock(), gap_days=3)
    assert [r.excluded for r in rows] == [None]


def test_the_link_twice_is_the_same_as_once(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, ADDRESS, confirmed=False)
    clock = Clock(NOW)
    mailer = RecordingSender()
    request(clean, [ADDRESS], now=clock())
    clean.commit()
    send_pending(clean, mailer, SETTINGS, clock=clock, pause=lambda _s: None)
    raw = link_in(mailer)
    assert confirm(clean, raw, now=clock()) is True
    first = user_of(clean, ADDRESS).email_confirmed_at
    clock.at += timedelta(hours=1)
    assert confirm(clean, raw, now=clock()) is True
    assert user_of(clean, ADDRESS).email_confirmed_at == first


def test_an_expired_or_forged_link_confirms_nothing(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, ADDRESS, confirmed=False)
    clock = Clock(NOW)
    mailer = RecordingSender()
    request(clean, [ADDRESS], now=clock())
    clean.commit()
    send_pending(clean, mailer, SETTINGS, clock=clock, pause=lambda _s: None)
    raw = link_in(mailer)
    late = clock() + timedelta(days=SETTINGS.confirmation_link_days, seconds=1)
    assert confirm(clean, raw, now=late) is False
    assert confirm(clean, raw + "x", now=clock()) is False
    assert confirm(clean, "", now=clock()) is False
    assert confirm(clean, "a" * 65, now=clock()) is False
    assert user_of(clean, ADDRESS).email_confirmed_at is None


def test_a_refused_mail_is_tried_again_and_a_login_cancels_it(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, ADDRESS, confirmed=False)
    person(clean, "bruno@acme.it", confirmed=False)
    clock = Clock(NOW)

    class Refusing:
        def send(self, mail: object) -> bool:
            return False

    request(clean, [ADDRESS, "bruno@acme.it"], now=clock())
    clean.commit()
    assert send_pending(clean, Refusing(), SETTINGS, clock=clock, pause=lambda _s: None) == 0  # type: ignore[arg-type]
    assert user_of(clean, ADDRESS).confirmation_sent_at is None
    # Bruno logs in before the retry: nobody asks him to confirm what a login proved.
    clean.add(Login(user_id=user_of(clean, "bruno@acme.it").id))
    clean.commit()
    mailer = RecordingSender()
    assert send_pending(clean, mailer, SETTINGS, clock=clock, pause=lambda _s: None) == 1
    assert [m.to for m in mailer.sent] == [ADDRESS]


def test_the_day_budget_holds_the_rest_until_tomorrow(clean: Session) -> None:  # noqa: F811  (fixture)
    for n in range(3):
        person(clean, f"p{n}@acme.it", confirmed=False)
    clock = Clock(NOW)
    small = SETTINGS.model_copy(update={"confirmation_mail_daily_budget": 2})
    request(clean, [f"p{n}@acme.it" for n in range(3)], now=clock())
    clean.commit()
    mailer = RecordingSender()
    assert send_pending(clean, mailer, small, clock=clock, pause=lambda _s: None) == 2
    assert send_pending(clean, mailer, small, clock=clock, pause=lambda _s: None) == 0
    clock.at += timedelta(days=1)
    assert send_pending(clean, mailer, small, clock=clock, pause=lambda _s: None) == 1
