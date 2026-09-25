"""CampaignService: the admin's verbs (spec § 4)."""

from datetime import UTC, date, datetime, time, timedelta

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
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import (
    CampaignPatch,
    CampaignRead,
    ScheduleRequest,
    TalentiFiltri,
)
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.campaigns.service import NOT_A_DRAFT, CampaignService
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.models import Campaign, CampaignRecipient, User


def test_a_draft_gets_a_dated_unique_slug(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    first = service.create(who.id, draft())
    second = service.create(who.id, draft())
    assert first.slug == "c-2026-09-25-manca-il-cv"
    assert second.slug == "c-2026-09-25-manca-il-cv-2"
    assert first.stato == "bozza" and first.pronta is False


def test_phase_one_refuses_the_pigro_parts(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    bad_drafts = (
        draft(stato_percorso="pigro_vuoto"),
        draft(bottone_meta="pigro"),
        draft(azione="pigro_cliente"),
    )
    for bad in bad_drafts:
        with pytest.raises(ValidationFailed, match="fase Pigro"):
            service.create(who.id, bad)


def test_the_audience_preview_counts_included_and_excluded(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    person(clean, "nocv@studio.it", cv=False)
    person(clean, "boss@rebase.it", cv=False, role="admin")
    created = service.create(who.id, draft())
    preview = service.audience(created.id)
    assert (preview.incluse, preview.escluse) == (1, 1)


def test_an_edit_moves_contenuto_at_and_a_non_draft_cannot_be_edited(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    created = service.create(admin(clean).id, draft())
    clock.at = NOW + timedelta(minutes=5)
    edited = service.update(created.id, CampaignPatch(oggetto="Nuovo oggetto"))
    assert edited.contenuto_at == NOW + timedelta(minutes=5)

    row = clean.get(Campaign, created.id)
    assert row is not None
    row.stato = "programmata"
    clean.commit()
    with pytest.raises(InvalidState, match=NOT_A_DRAFT):
        service.update(created.id, CampaignPatch(oggetto="Un altro oggetto"))


def test_switching_fonte_clears_the_other_sources_column(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)

    with_filters = service.create(
        who.id,
        draft(fonte="filtri", stato_percorso=None, filtri=TalentiFiltri(lista="talenti")),
    )
    service.update(with_filters.id, CampaignPatch(fonte="stato", stato_percorso="manca_cv"))
    filtri_is_null = clean.execute(
        text("select 1 from campaigns where id = :id and filtri is null"),
        {"id": with_filters.id},
    ).scalar()
    assert filtri_is_null == 1

    with_state = service.create(who.id, draft())
    service.update(
        with_state.id, CampaignPatch(fonte="filtri", filtri=TalentiFiltri(lista="talenti"))
    )
    stato_percorso_is_null = clean.execute(
        text("select 1 from campaigns where id = :id and stato_percorso is null"),
        {"id": with_state.id},
    ).scalar()
    assert stato_percorso_is_null == 1


def ready(service: CampaignService, session: Session, clock: Clock) -> tuple[CampaignRead, User]:
    who = admin(session)
    person(session, "nocv@studio.it", cv=False)
    person(session, "other@studio.it", cv=False)
    created = service.create(who.id, draft())
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    return service.detail(created.id).campagna, who


def test_the_test_goes_to_the_admin_and_enables_sending(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    created = service.create(who.id, draft())
    recording = RecordingCampaignSender()
    tested = service.send_test(created.id, as_admin(who), recording)
    assert tested.pronta is True
    rendered = recording.sent[0]
    assert rendered.mail.to == "ivan@rebase.it"
    assert rendered.mail.subject.startswith("[prova] ")
    assert rendered.tags["kind"] == "test" and "r" not in rendered.tags


def test_an_edit_after_the_test_blocks_scheduling_until_a_new_test(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Review Focus 1: `updated_at` moves on the test's own write; `contenuto_at` does
    not, so it is what the rule compares."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, who = ready(service, clean, clock)
    clock.at += timedelta(minutes=1)
    service.update(campaign.id, CampaignPatch(testo="Ciao {nome},\n\naltro testo."))
    with pytest.raises(InvalidState, match="Manda una prova"):
        service.schedule(campaign.id, ScheduleRequest())
    clock.at += timedelta(minutes=1)
    service.send_test(campaign.id, as_admin(who), RecordingCampaignSender())
    assert service.schedule(campaign.id, ScheduleRequest()).stato == "programmata"


def test_a_refused_test_does_not_enable_sending(clean: Session) -> None:  # noqa: F811  (fixture)
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    created = service.create(who.id, draft())
    with pytest.raises(InvalidState, match="Resend"):
        service.send_test(
            created.id,
            as_admin(who),
            RecordingCampaignSender([SendOutcome("rifiutata", dettaglio="Resend 422")]),
        )
    assert service.detail(created.id).campagna.pronta is False


def test_scheduling_freezes_the_list_minus_the_unticked_and_the_excluded(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    person(clean, "boss@rebase.it", cv=False, role="admin")
    scheduled = service.schedule(campaign.id, ScheduleRequest(esclusi=["Other@Studio.it"]))
    assert scheduled.stato == "programmata" and scheduled.programmata_per == clock.at
    rows = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).all()
    assert [r.email for r in rows] == ["nocv@studio.it"]
    assert rows[0].stato == "in_coda" and len(rows[0].codice) == 8
    assert rows[0].prima["ha_cv"] is False and len(rows[0].disiscrizione_token) >= 40


@pytest.mark.parametrize(
    ("giorno", "ora", "utc"),
    [
        (date(2026, 10, 24), time(9, 30), datetime(2026, 10, 24, 7, 30, tzinfo=UTC)),  # CEST
        (date(2026, 10, 25), time(9, 30), datetime(2026, 10, 25, 8, 30, tzinfo=UTC)),  # CET
        (
            date(2026, 10, 25),
            time(2, 30),
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        ),  # twice: the first
    ],
)
def test_a_scheduled_time_is_rome_wall_clock_across_dst(
    clean: Session,  # noqa: F811  (fixture)
    giorno: date,
    ora: time,
    utc: datetime,
) -> None:
    """Review Focus 2."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    assert (
        service.schedule(campaign.id, ScheduleRequest(giorno=giorno, ora=ora)).programmata_per
        == utc
    )


def test_a_time_that_does_not_exist_or_has_passed_is_refused(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    with pytest.raises(ValidationFailed, match="cambio d'ora"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2027, 3, 28), ora=time(2, 30)))
    with pytest.raises(ValidationFailed, match="già passato"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 24), ora=time(9, 30)))
    with pytest.raises(ValidationFailed, match="giorno e ora"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 30)))


def test_back_to_draft_drops_the_frozen_list_and_cancel_skips_what_is_left(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 30), ora=time(9, 30)))
    assert service.back_to_draft(campaign.id).stato == "bozza"
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == 0
    service.schedule(campaign.id, ScheduleRequest())
    cancelled = service.cancel(campaign.id)
    assert cancelled.stato == "annullata"
    assert {
        r.motivo for r in clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id)
    } == {"campagna annullata"}
    with pytest.raises(InvalidState):
        service.cancel(campaign.id)
