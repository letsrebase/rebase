"""A campaign's list: templates, candidates, exclusions, the action already done."""

from datetime import UTC, datetime, timedelta

from campaign_fixtures import (  # noqa: F401  (fixture)
    T0,
    campaign_row,
    clean,
    company,
    lead,
    person,
)
from sqlalchemy.orm import Session

from rebase_core.campaigns.actions import done_at, snapshot
from rebase_core.campaigns.audience import (
    REASON_ADMIN,
    REASON_BOUNCED,
    REASON_NEVER,
    build_audience,
    candidates,
    exclusions,
)
from rebase_core.campaigns.states import JOURNEY_STATES, PHASE_ONE_STATES, candidates_for_state
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.comments import CommentService
from rebase_core.models import (
    CAMPAIGN_ACTIONS,
    CAMPAIGN_DESTINATIONS,
    CampaignOptout,
    CampaignRecipient,
    Login,
)


def test_every_phase_one_state_has_a_template_that_fits_the_columns() -> None:
    assert set(STATE_TEMPLATES) == set(PHASE_ONE_STATES)
    for key, template in STATE_TEMPLATES.items():
        assert template.etichetta == JOURNEY_STATES[key]
        assert template.azione in CAMPAIGN_ACTIONS and template.azione != "pigro_cliente"
        assert template.bottone_meta in CAMPAIGN_DESTINATIONS and template.bottone_meta != "pigro"
        assert template.testo.startswith("Ciao {nome},")
        assert len(template.oggetto) <= 200 and len(template.bottone_testo) <= 60


def recipient_for(session: Session, candidate, prima: dict) -> CampaignRecipient:  # type: ignore[no-untyped-def]
    return CampaignRecipient(
        email=candidate.email,
        tipo=candidate.tipo,
        user_id=candidate.user_id,
        freelancer_id=candidate.freelancer_id,
        signup_id=candidate.signup_id,
        codice="00000000",
        prima=prima,
        disiscrizione_token="t",
    )


def test_a_cv_uploaded_after_the_snapshot_counts_and_one_from_before_does_not(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    card = person(clean, "nocv@studio.it", cv=False)
    candidate = candidates_for_state(clean, "manca_cv")[0]
    prima = snapshot(clean, candidate, T0)
    assert prima["ha_cv"] is False and prima["ha_scheda"] is True
    row = recipient_for(clean, candidate, prima)
    assert done_at(clean, row, "cv") is None
    card.cv_size, card.cv_filename, card.cv_bytes = 4, "cv.pdf", b"%PDF"
    clean.commit()
    CommentService(clean).add("freelancer", card.id, "CV caricato dalla persona", "Ada Lovelace")
    assert done_at(clean, row, "cv") is not None


def test_a_login_after_the_snapshot_is_entered(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "done@studio.it")
    candidate = candidates_for_state(clean, "completo")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "entrato") is None
    clean.add(Login(user_id=candidate.user_id, logged_at=T0 + timedelta(minutes=5)))
    clean.commit()
    assert done_at(clean, row, "entrato") == T0 + timedelta(minutes=5)


def test_a_lead_who_makes_a_card_has_created_a_profile(clean: Session) -> None:  # noqa: F811  (fixture)
    lead(clean, "giulia@studio.it")
    candidate = candidates_for_state(clean, "lead")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0 - timedelta(days=1)))
    assert done_at(clean, row, "profilo_creato") is None
    person(clean, "giulia@studio.it", nome="Giulia")
    assert done_at(clean, row, "profilo_creato") is not None


def test_any_of_a_referentes_open_requests_updated_counts(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    first = company(clean, "info@block-buy.it")
    company(clean, "info@block-buy.it", stato="contattato")
    candidate = candidates_for_state(clean, "azienda_aperta")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, datetime.now(UTC)))
    assert done_at(clean, row, "richiesta_aggiornata") is None
    first.durata = "18 mesi"
    clean.commit()
    assert done_at(clean, row, "richiesta_aggiornata") is not None


def test_the_pigro_action_is_never_done_in_phase_one(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "done@studio.it")
    candidate = candidates_for_state(clean, "completo")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "pigro_cliente") is None


def test_an_address_with_no_card_never_has_done_cv_or_scheda_completa(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    lead(clean, "nocard@studio.it")
    candidate = candidates_for_state(clean, "lead")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "cv") is None
    assert done_at(clean, row, "scheda_completa") is None


def test_a_card_soft_deleted_after_completing_counts_as_not_done(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    card = person(clean, "gone@studio.it", cv=False)
    candidate = candidates_for_state(clean, "manca_cv")[0]
    prima = snapshot(clean, candidate, T0)
    assert prima["ha_cv"] is False and prima["completa"] is False
    row = recipient_for(clean, candidate, prima)
    card.cv_size, card.cv_filename, card.cv_bytes = 4, "cv.pdf", b"%PDF"
    card.deleted_at = datetime.now(UTC)
    clean.commit()
    assert done_at(clean, row, "cv") is None
    assert done_at(clean, row, "scheda_completa") is None


def test_filters_reuse_talenti_and_merge_one_person_across_cases(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    person(clean, "ada@studio.it", tariffa=False)
    lead(clean, "giulia@studio.it")
    campaign = campaign_row(
        clean, fonte="filtri", stato_percorso=None, filtri={"lista": "talenti", "has_cv": True}
    )
    assert [c.email for c in candidates(clean, campaign)] == ["ada@studio.it"]
    campaign.filtri = {"lista": "talenti", "stato": "lead"}
    clean.commit()
    assert [c.email for c in candidates(clean, campaign)] == ["giulia@studio.it"]


def test_every_exclusion_names_its_reason(clean: Session) -> None:  # noqa: F811  (fixture)
    person(clean, "boss@rebase.it", role="admin", cv=False)
    person(clean, "gone@studio.it", cv=False)
    person(clean, "never@studio.it", cv=False)
    person(clean, "bounce@studio.it", cv=False)
    person(clean, "recent@studio.it", cv=False)
    person(clean, "ok@studio.it", cv=False)
    clean.add(CampaignOptout(email="gone@studio.it", fonte="link"))
    clean.add(CampaignOptout(email="never@studio.it", fonte="admin"))
    earlier = campaign_row(clean)
    clean.add(
        CampaignRecipient(
            campaign_id=earlier.id,
            email="bounce@studio.it",
            tipo="freelancer",
            codice="1",
            prima={},
            disiscrizione_token="b",
            stato="inviata",
            inviata_at=T0 - timedelta(days=30),
            rimbalzata_at=T0 - timedelta(days=30),
        )
    )
    clean.add(
        CampaignRecipient(
            campaign_id=earlier.id,
            email="recent@studio.it",
            tipo="freelancer",
            codice="2",
            prima={},
            disiscrizione_token="r",
            stato="inviata",
            inviata_at=T0 - timedelta(days=1),
        )
    )
    clean.commit()
    current = campaign_row(clean)
    reasons = exclusions(
        clean,
        [
            "boss@rebase.it",
            "gone@studio.it",
            "never@studio.it",
            "bounce@studio.it",
            "recent@studio.it",
            "ok@studio.it",
        ],
        campaign_id=current.id,
        now=T0,
        gap_days=3,
    )
    assert reasons["boss@rebase.it"] == REASON_ADMIN
    assert reasons["gone@studio.it"] == "si è disiscritto"
    assert reasons["never@studio.it"] == REASON_NEVER
    assert reasons["bounce@studio.it"] == REASON_BOUNCED
    assert reasons["recent@studio.it"].startswith("ha ricevuto un'altra campagna il ")
    assert "ok@studio.it" not in reasons


def test_the_audience_lists_everyone_and_greys_out_the_excluded(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    person(clean, "boss@rebase.it", role="admin", cv=False)
    person(clean, "ok@studio.it", cv=False)
    campaign = campaign_row(clean)
    rows = build_audience(clean, campaign, now=T0, gap_days=3)
    assert [(r.candidate.email, r.escluso) for r in rows] == [
        ("boss@rebase.it", REASON_ADMIN),
        ("ok@studio.it", None),
    ]
