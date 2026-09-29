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
    email="ada@studio.it", nome="Ada", codice="ab12cd34", token="tok", recipient_id="r-1"
)


def link_draft(**fields: object) -> CampaignDraft:
    return draft(**{"bottone_meta": "link", "bottone_url": LUMA, "azione": "clic", **fields})


def link_campaign(url: str) -> Campaign:
    return Campaign(
        nome="Casa",
        slug="c-2026-09-28-casa",
        fonte="stato",
        stato_percorso="completo",
        oggetto="Vieni",
        testo="Ciao {nome},\n\nti aspettiamo.",
        bottone_testo="Iscriviti",
        bottone_meta="link",
        bottone_url=url,
        azione="clic",
        contenuto_at=NOW,
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
        "https://letsrebase.com/eventi/casa?utm_source=email&utm_medium=campagna"
        "&utm_campaign=c-2026-09-28-casa&utm_content=clic&utm_term=ab12cd34"
    ) in mail.text


def test_tracking_joins_a_query_already_there_and_stays_before_the_fragment() -> None:
    url = "https://preview.letsrebase.com/casa?giorno=3#programma"
    mail = render(link_campaign(url), TARGET, SETTINGS).mail
    assert (
        "https://preview.letsrebase.com/casa?giorno=3&utm_source=email&utm_medium=campagna"
        "&utm_campaign=c-2026-09-28-casa&utm_content=clic&utm_term=ab12cd34#programma"
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
    campaign.bottone_meta, campaign.bottone_url, campaign.azione = "area", None, "entrato"
    assert (
        "https://letsrebase.com/hub/login?utm_source=email&utm_medium=campagna"
        "&utm_campaign=c-2026-09-28-casa&utm_content=entrato&utm_term=ab12cd34"
    ) in render(campaign, TARGET, SETTINGS).mail.text


# ---- what the service accepts -------------------------------------------------------


def test_a_link_campaign_is_stored_with_its_trimmed_address(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    created = service.create(admin(clean).id, link_draft(bottone_url=f"  {LUMA}  "))
    assert (created.bottone_meta, created.bottone_url, created.azione) == ("link", LUMA, "clic")


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
    assert service.create(admin(clean).id, link_draft(bottone_url=url)).bottone_url == url


@pytest.mark.parametrize(
    ("fields", "field", "sentence"),
    [
        ({"bottone_url": None}, "bottone_url", LINK_URL_MISSING),
        ({"bottone_url": "   "}, "bottone_url", LINK_URL_MISSING),
        ({"bottone_url": "http://lu.ma/casa"}, "bottone_url", LINK_URL_NOT_HTTPS),
        ({"bottone_url": "lu.ma/casa"}, "bottone_url", LINK_URL_NOT_HTTPS),
        ({"bottone_url": "https://"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://lu.ma/una casa"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://ivan:pw@lu.ma/casa"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://evil.io\\@lu.ma/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://lu.ma/" + "a" * 490}, "bottone_url", LINK_URL_TOO_LONG),
        # Greptile P1s on #476: what `urlsplit` raises on, or lets through while a
        # browser (and the editor's `new URL`) refuses it, is the same 422.
        ({"bottone_url": "https://[invalid"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://a]b.com/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://[zzz]/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://lu.ma:99999/rebase-house"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://lu.ma:65536/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://lu.ma:abc/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://lu.ma:-1/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https:lu.ma"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https:///lu.ma"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://@lu.ma/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://a%2eb.com/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://a<b.com/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://a|b.com/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://999.1.1.1/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://127.1/"}, "bottone_url", LINK_URL_INVALID),
        ({"bottone_url": "https://xn--a.com/"}, "bottone_url", LINK_URL_INVALID),
        ({"azione": "entrato"}, "azione", LINK_MEASURES_CLICK),
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
        service.create(who, draft(bottone_url=LUMA))
    assert unused.value.details == {
        "entity": "campagna",
        "field": "bottone_url",
        "reason": LINK_URL_UNUSED,
    }
    with pytest.raises(ValidationFailed) as click:
        service.create(who, draft(azione="clic"))
    assert click.value.details["field"] == "azione"
    assert click.value.details["reason"] == CLICK_NEEDS_LINK


def test_a_patch_is_checked_on_the_campaign_it_leaves(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    created = service.create(admin(clean).id, draft())
    with pytest.raises(ValidationFailed) as missing:
        service.update(created.id, CampaignPatch(bottone_meta="link", azione="clic"))
    assert missing.value.details["reason"] == LINK_URL_MISSING
    clean.rollback()  # as the API's request session does after a refusal
    with pytest.raises(ValidationFailed) as not_https:
        service.update(
            created.id,
            CampaignPatch(bottone_meta="link", azione="clic", bottone_url="ftp://lu.ma/x"),
        )
    assert not_https.value.details["reason"] == LINK_URL_NOT_HTTPS
    clean.rollback()
    linked = service.update(
        created.id, CampaignPatch(bottone_meta="link", azione="clic", bottone_url=LUMA)
    )
    assert (linked.bottone_meta, linked.bottone_url) == ("link", LUMA)
    # Leaving «Un link» takes the address with it, the request need not say so.
    back = service.update(created.id, CampaignPatch(bottone_meta="area", azione="cv"))
    assert (back.bottone_meta, back.bottone_url, back.azione) == ("area", None, "cv")


def test_a_new_address_makes_the_last_test_stale(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    created = service.create(who.id, link_draft())
    clock.at += timedelta(minutes=1)
    recording = RecordingCampaignSender()
    tested = service.send_test(created.id, as_admin(who), recording)
    assert tested.pronta and LUMA in recording.sent[0].mail.text
    clock.at += timedelta(minutes=1)
    moved = service.update(created.id, CampaignPatch(bottone_url="https://chat.whatsapp.com/x"))
    assert moved.pronta is False


# ---- what it measures ---------------------------------------------------------------


def sent_link_campaign(session: Session, clock: Clock) -> Campaign:
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    created = service.create(who.id, link_draft(stato_percorso="completo"))
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
    assert row.stato == "inviata" and row.motivo is None
    assert stamp_outcomes(clean, now=clock.at + timedelta(minutes=1)) == 0
    clicked = clock.at + timedelta(hours=3)
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.id == row.id)
        .values(primo_clic_at=clicked)
    )
    clean.commit()
    assert stamp_outcomes(clean, now=clicked + timedelta(minutes=1)) == 1
    clean.expire_all()
    row = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).one()
    assert (row.azione_at, row.entrato_at) == (clicked, None)


def test_a_follow_up_keeps_the_link(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    person(clean, "ada@studio.it")
    campaign = sent_link_campaign(clean, clock)
    service = CampaignService(clean, SETTINGS, clock=clock)
    follow = service.follow_up(campaign.id, admin(clean).id)
    assert (follow.bottone_meta, follow.bottone_url, follow.azione) == ("link", LUMA, "clic")
    with pytest.raises(ValidationFailed) as refused:
        service.update(follow.id, CampaignPatch(bottone_meta="area"))
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
        .values(primo_clic_at=at)
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
    assert (result.inviate, result.saltate, recording.sent) == (0, 1, [])
    clean.expire_all()
    row = clean.query(CampaignRecipient).filter_by(campaign_id=follow.id).one()
    assert (row.stato, row.motivo) == ("saltata", REASON_DONE)


def test_a_follow_up_is_credited_only_with_a_click_after_it_left(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    parent, follow = scheduled_follow_up(clean, clock)
    # Its own Resend id: a second recorder would answer `rec-1` again, which the parent's
    # row already holds (`uq_campaign_recipients_resend_id`).
    follow_sender = RecordingCampaignSender([SendOutcome("accettata", "rec-follow")])
    run_tick(clean, follow_sender, SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.expire_all()
    row = clean.query(CampaignRecipient).filter_by(campaign_id=follow.id).one()
    assert row.stato == "inviata" and row.inviata_at is not None
    # A click on the first mail from before this one left does not count for it...
    assert earlier_click(clean, row, since=row.inviata_at) is None
    click_on(clean, parent, row.inviata_at - timedelta(minutes=1))
    assert earlier_click(clean, row, since=row.inviata_at) is None
    # ...one after it does, and the tick stamps it.
    later = row.inviata_at + timedelta(hours=2)
    click_on(clean, parent, later)
    assert stamp_outcomes(clean, now=later + timedelta(minutes=1)) >= 1
    clean.expire_all()
    row = clean.query(CampaignRecipient).filter_by(campaign_id=follow.id).one()
    assert row.azione_at == later


# ---- the database -------------------------------------------------------------------


def test_the_link_constraints_are_installed(hub_engine: Engine) -> None:
    """The address goes with «Un link» and the click with the address, proven with raw
    SQL that bypasses the service, rolled back."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, "
                "updated_at) VALUES (gen_random_uuid(), 'l@rebase.it', 'L', '', 'admin', "
                "true, now(), now()) RETURNING id"
            )
        ).scalar_one()
        insert = text(
            "INSERT INTO campaigns (id, created_by, nome, slug, fonte, stato_percorso, "
            "oggetto, testo, bottone_testo, bottone_meta, bottone_url, azione, stato, "
            "contenuto_at, created_at, updated_at) VALUES (gen_random_uuid(), :u, 'n', "
            ":slug, 'stato', 'lead', '', '', '', :meta, :url, :azione, 'bozza', now(), "
            "now(), now())"
        )
        good = {"meta": "link", "url": LUMA, "azione": "clic"}
        for slug, bad in (
            ("a", {**good, "url": None}),  # ck_campaigns_bottone_url
            ("b", {**good, "meta": "area"}),  # ck_campaigns_bottone_url
            ("c", {**good, "azione": "entrato"}),  # ck_campaigns_link_clic
            ("d", {"meta": "area", "url": None, "azione": "clic"}),  # ck_campaigns_link_clic
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
    row = campaign_row(clean, bottone_meta="link", bottone_url=url, azione="clic")
    clean.expire_all()
    assert clean.get(Campaign, row.id).bottone_url == url  # type: ignore[union-attr]


def test_migration_0027_can_run_again_and_roll_back() -> None:
    """A retried deploy runs 0027 again over what it already added, and the downgrade
    leaves 0026's schema; with a link campaign in the table the downgrade refuses
    rather than delete it, and the table stays as it was."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0026'"))
        command.upgrade(config, "head")
        command.downgrade(config, "0026")
        command.upgrade(config, "head")
        with engine.begin() as connection:
            user_id = connection.execute(
                text(
                    "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, "
                    "updated_at) VALUES (gen_random_uuid(), 'l@rebase.it', 'L', '', 'admin', "
                    "true, now(), now()) RETURNING id"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO campaigns (id, created_by, nome, slug, fonte, stato_percorso, "
                    "oggetto, testo, bottone_testo, bottone_meta, bottone_url, azione, stato, "
                    "contenuto_at) VALUES (gen_random_uuid(), :u, 'n', 'casa', 'stato', 'lead', "
                    "'', '', '', 'link', :url, 'clic', 'bozza', now())"
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
            assert connection.execute(text("SELECT bottone_url FROM campaigns")).scalar() == LUMA
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
