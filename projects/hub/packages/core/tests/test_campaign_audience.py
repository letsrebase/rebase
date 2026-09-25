"""A campaign's list: templates, candidates, exclusions, the action already done."""

from datetime import UTC, datetime, timedelta

from campaign_fixtures import T0, clean, company, lead, person  # noqa: F401  (fixture)
from sqlalchemy.orm import Session

from rebase_core.campaigns.actions import done_at, snapshot
from rebase_core.campaigns.states import JOURNEY_STATES, PHASE_ONE_STATES, candidates_for_state
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.comments import CommentService
from rebase_core.models import CAMPAIGN_ACTIONS, CAMPAIGN_DESTINATIONS, CampaignRecipient, Login


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
