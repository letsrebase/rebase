"""A campaign button that leads out of the hub (REB-530): «Un link», its address, the
tracking it gets only on our own domain, and the click it measures."""

from datetime import datetime, timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from campaign_fixtures import (  # noqa: F401  (fixture)
    NOW,
    SETTINGS,
    Clock,
    admin,
    as_admin,
    campaign_row,
    clean,
    draft,
    person,
)
from sqlalchemy import Engine, create_engine, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from rebase_core.campaigns.actions import earlier_click
from rebase_core.campaigns.audience import REASON_DONE
from rebase_core.campaigns.links import (
    CLICK_NEEDS_LINK,
    LINK_MEASURES_CLICK,
    LINK_URL_INVALID,
    LINK_URL_MISSING,
    LINK_URL_NOT_HTTPS,
    LINK_URL_TOO_LONG,
    LINK_URL_UNUSED,
    is_ours,
)
from rebase_core.campaigns.outcome import stamp_outcomes
from rebase_core.campaigns.render import RenderTarget, destination, render
from rebase_core.campaigns.schemas import CampaignDraft, CampaignPatch, ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.tick import run_tick
from rebase_core.db import Base
from rebase_core.errors import ValidationFailed
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head
from rebase_core.models import Campaign, CampaignRecipient

LUMA = "https://lu.ma/rebase-house"
NO_PAUSE = lambda _seconds: None  # noqa: E731
TARGET = RenderTarget(
    email="ada@studio.it", name="Ada", code="ab12cd34", token="tok", recipient_id="r-1"
)


def link_draft(**fields: object) -> CampaignDraft:
    return draft(**{"button_meta": "link", "button_url": LUMA, "action": "click", **fields})


def link_campaign(url: str) -> Campaign:
    return Campaign(
        name="Casa",
        slug="c-2026-09-28-casa",
        source="status",
        journey_status="complete",
        subject="Vieni",
        text="Ciao {nome},\n\nti aspettiamo.",
        button_text="Iscriviti",
        button_meta="link",
        button_url=url,
        action="click",
        content_at=NOW,
    )


# ---- where the button leads ---------------------------------------------------------


def test_an_external_link_is_the_button_as_it_was_written() -> None:
    campaign = link_campaign(LUMA)
    assert destination(campaign, SETTINGS) == LUMA
    mail = render(campaign, TARGET, SETTINGS).mail
    assert f"Iscriviti: {LUMA}\n" in mail.text
    assert f'href="{LUMA}"' in (mail.html or "")
    assert "utm_" not in mail.text and "utm_" not in (mail.html or "")


def test_a_link_on_our_own_domain_is_tracked_like_the_hub() -> None:
    mail = render(link_campaign("https://letsrebase.com/eventi/casa"), TARGET, SETTINGS).mail
    assert (
        "https://letsrebase.com/eventi/casa?utm_source=email&utm_medium=campaign"
        "&utm_campaign=c-2026-09-28-casa&utm_content=click&utm_term=ab12cd34"
    ) in mail.text


def test_tracking_joins_a_query_already_there_and_stays_before_the_fragment() -> None:
    url = "https://preview.letsrebase.com/casa?giorno=3#programma"
    mail = render(link_campaign(url), TARGET, SETTINGS).mail
    assert (
        "https://preview.letsrebase.com/casa?giorno=3&utm_source=email&utm_medium=campaign"
        "&utm_campaign=c-2026-09-28-casa&utm_content=click&utm_term=ab12cd34#programma"
    ) in mail.text


def test_only_letsrebase_com_and_its_subdomains_are_ours() -> None:
    assert is_ours("https://letsrebase.com/x")
    assert is_ours("https://LetsRebase.com")
    assert is_ours("https://firma.letsrebase.com/x")
    assert not is_ours("https://letsrebase.com.evil.io/")
    assert not is_ours("https://notletsrebase.com/")
    assert not is_ours("https://evil.io/letsrebase.com")
    assert not is_ours(LUMA)


def test_the_hub_destinations_keep_their_exact_tracked_url() -> None:
    """A retry must send Resend the same bytes: the join changed nothing for them."""
    campaign = link_campaign(LUMA)
    campaign.button_meta, campaign.button_url, campaign.action = "area", None, "signed_in"
    assert (
        "https://letsrebase.com/hub/login?utm_source=email&utm_medium=campaign"
        "&utm_campaign=c-2026-09-28-casa&utm_content=signed_in&utm_term=ab12cd34"
    ) in render(campaign, TARGET, SETTINGS).mail.text


# ---- what the service accepts -------------------------------------------------------


def test_a_link_campaign_is_stored_with_its_trimmed_address(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    created = service.create(admin(clean).id, link_draft(button_url=f"  {LUMA}  "))
    assert (created.button_meta, created.button_url, created.action) == ("link", LUMA, "click")


@pytest.mark.parametrize(
    "url",
    [
        "https://lu.ma:443/rebase-house",
        "https://lu.ma:8443/x",
        "https://lu.ma:/x",
        "https://[::1]:8443/x",
        "https://1.2.3.4/",
        "https://xn--bcher-kva.example/",
        "https://bücher.example/",
        "HTTPS://LU.MA/Casa?giorno=3#programma",
    ],
)
def test_an_address_a_browser_opens_is_taken(
    clean: Session,  # noqa: F811  (fixture)
    url: str,
) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    assert service.create(admin(clean).id, link_draft(button_url=url)).button_url == url


@pytest.mark.parametrize(
    ("fields", "field", "sentence"),
    [
        ({"button_url": None}, "button_url", LINK_URL_MISSING),
        ({"button_url": "   "}, "button_url", LINK_URL_MISSING),
        ({"button_url": "http://lu.ma/casa"}, "button_url", LINK_URL_NOT_HTTPS),
        ({"button_url": "lu.ma/casa"}, "button_url", LINK_URL_NOT_HTTPS),
        ({"button_url": "https://"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://lu.ma/una casa"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://ivan:pw@lu.ma/casa"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://evil.io\\@lu.ma/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://lu.ma/" + "a" * 490}, "button_url", LINK_URL_TOO_LONG),
        # Greptile P1s on #476: what `urlsplit` raises on, or lets through while a
        # browser (and the editor's `new URL`) refuses it, is the same 422.
        ({"button_url": "https://[invalid"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://a]b.com/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://[zzz]/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://lu.ma:99999/rebase-house"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://lu.ma:65536/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://lu.ma:abc/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://lu.ma:-1/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https:lu.ma"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https:///lu.ma"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://@lu.ma/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://a%2eb.com/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://a<b.com/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://a|b.com/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://999.1.1.1/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://127.1/"}, "button_url", LINK_URL_INVALID),
        ({"button_url": "https://xn--a.com/"}, "button_url", LINK_URL_INVALID),
        ({"action": "signed_in"}, "action", LINK_MEASURES_CLICK),
    ],
)
def test_a_link_campaign_refuses_a_bad_address_or_another_action(
    clean: Session,  # noqa: F811  (fixture)
    fields: dict[str, object],
    field: str,
    sentence: str,
) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    with pytest.raises(ValidationFailed) as refused:
        service.create(admin(clean).id, link_draft(**fields))
    assert refused.value.details["field"] == field
    assert refused.value.details["reason"] == sentence


def test_an_address_or_the_click_without_a_link_is_refused(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean).id
    with pytest.raises(ValidationFailed) as unused:
        service.create(who, draft(button_url=LUMA))
    assert unused.value.details == {
        "entity": "campaign",
        "field": "button_url",
        "reason": LINK_URL_UNUSED,
    }
    with pytest.raises(ValidationFailed) as click:
        service.create(who, draft(action="click"))
    assert click.value.details["field"] == "action"
    assert click.value.details["reason"] == CLICK_NEEDS_LINK


def test_a_patch_is_checked_on_the_campaign_it_leaves(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    created = service.create(admin(clean).id, draft())
    with pytest.raises(ValidationFailed) as missing:
        service.update(created.id, CampaignPatch(button_meta="link", action="click"))
    assert missing.value.details["reason"] == LINK_URL_MISSING
    clean.rollback()  # as the API's request session does after a refusal
    with pytest.raises(ValidationFailed) as not_https:
        service.update(
            created.id,
            CampaignPatch(button_meta="link", action="click", button_url="ftp://lu.ma/x"),
        )
    assert not_https.value.details["reason"] == LINK_URL_NOT_HTTPS
    clean.rollback()
    linked = service.update(
        created.id, CampaignPatch(button_meta="link", action="click", button_url=LUMA)
    )
    assert (linked.button_meta, linked.button_url) == ("link", LUMA)
    # Leaving «Un link» takes the address with it, the request need not say so.
    back = service.update(created.id, CampaignPatch(button_meta="area", action="cv"))
    assert (back.button_meta, back.button_url, back.action) == ("area", None, "cv")


def test_a_new_address_makes_the_last_test_stale(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    created = service.create(who.id, link_draft())
    clock.at += timedelta(minutes=1)
    recording = RecordingCampaignSender()
    tested = service.send_test(created.id, as_admin(who), recording)
    assert tested.ready and LUMA in recording.sent[0].mail.text
    clock.at += timedelta(minutes=1)
    moved = service.update(created.id, CampaignPatch(button_url="https://chat.whatsapp.com/x"))
    assert moved.ready is False


# ---- what it measures ---------------------------------------------------------------


def sent_link_campaign(session: Session, clock: Clock) -> Campaign:
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    created = service.create(who.id, link_draft(journey_status="complete"))
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    run_tick(session, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    return session.get(Campaign, created.id)  # type: ignore[return-value]


def test_the_first_click_is_a_link_campaigns_action(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    person(clean, "ada@studio.it")
    campaign = sent_link_campaign(clean, clock)
    row = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).one()
    assert row.status == "sent" and row.reason is None
    assert stamp_outcomes(clean, now=clock.at + timedelta(minutes=1)) == 0
    clicked = clock.at + timedelta(hours=3)
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.id == row.id)
        .values(first_click_at=clicked)
    )
    clean.commit()
    assert stamp_outcomes(clean, now=clicked + timedelta(minutes=1)) == 1
    clean.expire_all()
    row = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).one()
    assert (row.action_at, row.signed_in_at) == (clicked, None)


def test_a_follow_up_keeps_the_link(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    person(clean, "ada@studio.it")
    campaign = sent_link_campaign(clean, clock)
    service = CampaignService(clean, SETTINGS, clock=clock)
    follow = service.follow_up(campaign.id, admin(clean).id)
    assert (follow.button_meta, follow.button_url, follow.action) == ("link", LUMA, "click")
    with pytest.raises(ValidationFailed) as refused:
        service.update(follow.id, CampaignPatch(button_meta="area"))
    assert refused.value.details["reason"] == CLICK_NEEDS_LINK


GAP = timedelta(days=SETTINGS.campaign_gap_days, hours=1)


def scheduled_follow_up(session: Session, clock: Clock) -> tuple[Campaign, Campaign]:
    """A link campaign sent to Ada, and its «Riscrivi», tested and scheduled for now."""
    person(session, "ada@studio.it")
    parent = sent_link_campaign(session, clock)
    clock.at += GAP
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    follow = service.follow_up(parent.id, who.id)
    clock.at += timedelta(minutes=1)
    service.send_test(follow.id, as_admin(who), RecordingCampaignSender())
    service.schedule(follow.id, ScheduleRequest())
    return parent, session.get(Campaign, follow.id)  # type: ignore[return-value]


def click_on(session: Session, campaign: Campaign, at: datetime) -> None:
    session.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .values(first_click_at=at)
    )
    session.commit()


def test_a_click_on_the_first_mail_after_scheduling_stops_the_follow_up(clean: Session) -> None:  # noqa: F811  (fixture)
    """Greptile P1 on #476: the follow-up's own row has no click before its mail leaves,
    so the send-time check reads the click on the mail it follows."""
    clock = Clock(NOW)
    parent, follow = scheduled_follow_up(clean, clock)
    click_on(clean, parent, clock.at + timedelta(seconds=30))
    clock.at += timedelta(minutes=1)
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert (result.sent, result.skipped, recording.sent) == (0, 1, [])
    clean.expire_all()
    row = clean.query(CampaignRecipient).filter_by(campaign_id=follow.id).one()
    assert (row.status, row.reason) == ("skipped", REASON_DONE)


def test_a_follow_up_is_credited_only_with_a_click_after_it_left(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    parent, follow = scheduled_follow_up(clean, clock)
    # Its own Resend id: a second recorder would answer `rec-1` again, which the parent's
    # row already holds (`uq_campaign_recipients_resend_id`).
    follow_sender = RecordingCampaignSender([SendOutcome("accepted", "rec-follow")])
    run_tick(clean, follow_sender, SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.expire_all()
    row = clean.query(CampaignRecipient).filter_by(campaign_id=follow.id).one()
    assert row.status == "sent" and row.sent_at is not None
    # A click on the first mail from before this one left does not count for it...
    assert earlier_click(clean, row, since=row.sent_at) is None
    click_on(clean, parent, row.sent_at - timedelta(minutes=1))
    assert earlier_click(clean, row, since=row.sent_at) is None
    # ...one after it does, and the tick stamps it.
    later = row.sent_at + timedelta(hours=2)
    click_on(clean, parent, later)
    assert stamp_outcomes(clean, now=later + timedelta(minutes=1)) >= 1
    clean.expire_all()
    row = clean.query(CampaignRecipient).filter_by(campaign_id=follow.id).one()
    assert row.action_at == later


# ---- the database -------------------------------------------------------------------


def test_the_link_constraints_are_installed(hub_engine: Engine) -> None:
    """The address goes with «Un link» and the click with the address, proven with raw
    SQL that bypasses the service, rolled back."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name, role, active, created_at, "
                "updated_at) VALUES (gen_random_uuid(), 'l@rebase.it', 'L', '', 'admin', "
                "true, now(), now()) RETURNING id"
            )
        ).scalar_one()
        insert = text(
            "INSERT INTO campaigns (id, created_by, name, slug, source, journey_status, "
            "subject, text, button_text, button_meta, button_url, action, status, "
            "content_at, created_at, updated_at) VALUES (gen_random_uuid(), :u, 'n', "
            ":slug, 'status', 'lead', '', '', '', :meta, :url, :action, 'draft', now(), "
            "now(), now())"
        )
        good = {"meta": "link", "url": LUMA, "action": "click"}
        for slug, bad in (
            ("a", {**good, "url": None}),  # ck_campaigns_button_url
            ("b", {**good, "meta": "area"}),  # ck_campaigns_button_url
            ("c", {**good, "action": "signed_in"}),  # ck_campaigns_link_click
            ("d", {"meta": "area", "url": None, "action": "click"}),  # ck_campaigns_link_click
        ):
            savepoint = connection.begin_nested()
            with pytest.raises(IntegrityError):
                connection.execute(insert, {"u": user_id, "slug": slug, **bad})
            savepoint.rollback()
        connection.execute(insert, {"u": user_id, "slug": "ok", **good})
        outer.rollback()


def test_the_address_column_holds_five_hundred_characters(clean: Session) -> None:  # noqa: F811  (fixture)
    url = "https://lu.ma/" + "a" * 486
    assert len(url) == 500
    row = campaign_row(clean, button_meta="link", button_url=url, action="click")
    clean.expire_all()
    assert clean.get(Campaign, row.id).button_url == url  # type: ignore[union-attr]


def test_migration_0027_can_run_again_and_roll_back() -> None:
    """A retried deploy runs 0027 again over what it already added, and the downgrade
    leaves 0026's schema; with a link campaign in the table the downgrade refuses
    rather than delete it, and the table stays as it was."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "0027")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0026'"))
        command.upgrade(config, "0027")
        command.downgrade(config, "0026")
        upgrade_to_head(url)
        with engine.begin() as connection:
            user_id = connection.execute(
                text(
                    "INSERT INTO users (id, email, first_name, last_name, role, active, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'l@rebase.it', 'L', "
                    "'', 'admin', true, now(), now()) RETURNING id"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO campaigns (id, created_by, name, slug, source, journey_status, "
                    "subject, text, button_text, button_meta, button_url, action, status, "
                    "content_at) VALUES (gen_random_uuid(), :u, 'n', 'casa', 'status', 'lead', "
                    "'', '', '', 'link', :url, 'click', 'draft', now())"
                ),
                {"u": user_id, "url": LUMA},
            )
        with pytest.raises(IntegrityError):
            command.downgrade(config, "0026")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            assert connection.execute(text("SELECT button_url FROM campaigns")).scalar() == LUMA
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
