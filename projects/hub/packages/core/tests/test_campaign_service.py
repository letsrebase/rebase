"""CampaignService: the admin's verbs (spec § 4)."""

from datetime import timedelta

import pytest
from campaign_fixtures import (  # noqa: F401  (fixture)
    NOW,
    SETTINGS,
    Clock,
    admin,
    clean,
    draft,
    person,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import CampaignPatch, TalentiFiltri
from rebase_core.campaigns.service import NOT_A_DRAFT, CampaignService
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.models import Campaign


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
