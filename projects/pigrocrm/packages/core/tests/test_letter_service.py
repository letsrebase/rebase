"""Engagement letters from the space's templates (REB-1018, collaborators design § 1 and § 8): the
texts seeded through the key map, a letter rendered with no client price in it and refused past the
legal payment term, framework before letter's own rules, drafts refused for a stored letter, and
templates of these types an administrator's alone.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from fakes.letter_world import (
    ADMIN,
    CLIENT_PRICE,
    COLLABORATOR,
    LEGAL_ENTITY_FIELDS,
    SYSTEM,
    World,
    needs_binaries,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.letter_templates import SEEDS, seed_collaborator_templates
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.db import today_local
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.errors import Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.schemas import TemplateCreate, TemplateUpdate
from pigrocrm.core.templates.service import TemplateService


@pytest.fixture
def world(db_session: Session, tmp_path: Path) -> World:
    return World(db_session, tmp_path)


# ---- the texts as templates -------------------------------------------------------------------


def test_the_seven_texts_are_seeded_once_with_their_regime_and_the_key_map(
    db_session: Session, world: World
) -> None:
    rows = list(db_session.scalars(select(Template).where(Template.type != "offer")))
    by_name = {row.name: row for row in rows if row.name in {seed.name for seed in SEEDS}}
    assert len(by_name) == len(SEEDS) == 7
    letter = by_name["Lettera di incarico"]
    assert (letter.type, letter.letter_type, letter.active) == (
        "engagement_letter",
        "ordinary",
        True,
    )
    assert by_name["Lettera di incarico occasionale"].letter_type == "occasional"
    assert by_name["Contratto quadro"].type == "framework_agreement"
    assert by_name["Accordo di riservatezza"].type == "contract"
    assert by_name["Accordo di riservatezza"].letter_type is None
    assert "{{professionista.nome}}" in letter.body_markdown
    assert "{{#if incarico.giorni_incasso}}" in letter.body_markdown
    names = {variable["name"] for variable in letter.declared_variables}
    assert {"attivita", "risultati", "periodo_verifica"} <= names
    assert not names & {"emittente", "professionista", "incarico", "firma", "cliente"}
    # Seeding again writes nothing: a text the space has is left alone.
    assert seed_collaborator_templates(db_session, SYSTEM) == []


def test_a_deleted_or_edited_text_is_not_written_back_over(
    db_session: Session, world: World
) -> None:
    service = TemplateService(db_session)
    template = world.final_template("engagement_letter", "ordinary")
    service.update(template.id, TemplateUpdate(name="La nostra lettera"), ADMIN)
    # Same kind and regime, still active: not seeded again under its original name either.
    assert seed_collaborator_templates(db_session, SYSTEM) == []
    service.deactivate(template.id, ADMIN)
    again = seed_collaborator_templates(db_session, SYSTEM)
    assert [row.name for row in again] == ["Lettera di incarico"]


def test_these_templates_are_an_admins_and_say_their_regime_and_version(
    db_session: Session, world: World
) -> None:
    service = TemplateService(db_session)
    body = '---\ntitle: T\nversion: "1"\nstatus: final\n---\n\nTesto.'

    def create(**overrides: object) -> object:
        values: dict[str, object] = {
            "name": f"Testo {uuid4()}",
            "type": "framework_agreement",
            "body_markdown": body,
            "letter_type": "ordinary",
            "active": False,
        }
        values.update(overrides)
        return service.create(TemplateCreate(**values), ADMIN)  # type: ignore[arg-type]

    create()
    with pytest.raises(PermissionDenied):
        service.create(
            TemplateCreate(
                name="Mio", type="engagement_letter", body_markdown=body, letter_type="ordinary"
            ),
            COLLABORATOR,
        )
    with pytest.raises(PermissionDenied):
        service.update(
            world.final_template("engagement_letter", "ordinary").id,
            TemplateUpdate(body_markdown=body),
            COLLABORATOR,
        )
    with pytest.raises(ValidationFailed, match="regime"):
        create(letter_type=None)
    with pytest.raises(ValidationFailed, match="regime"):
        create(type="offer")
    with pytest.raises(ValidationFailed, match="intestazione"):
        create(body_markdown="Senza intestazione.")
    with pytest.raises(ValidationFailed, match="stato del testo"):
        create(body_markdown=body.replace("final", "maybe"))
    # One active text per kind and regime.
    with pytest.raises(Conflict, match="già un template attivo"):
        create(type="engagement_letter", active=True)


# ---- a letter rendered ------------------------------------------------------------------------


@needs_binaries
def test_an_engagements_letter_renders_with_its_facts_and_no_client_price(world: World) -> None:
    who = world.freelancer()
    engagement = world.engagement(who)
    assert engagement.letter_number == f"{today_local().year}-001"

    preview = world.letters.preview_letter(
        engagement.id, {"attivita": "Sviluppo del backend", "luogo": "Da remoto"}, ADMIN
    )
    text = preview.markdown
    assert (preview.kind, preview.letter_type, preview.text_version) == (
        "engagement_letter",
        "ordinary",
        "1.2",
    )
    assert preview.draft is False and preview.number == engagement.letter_number
    assert preview.pdf.startswith(b"%PDF-")
    for printed in (
        "450,00 €",
        "al giorno",
        "30 giorni data fattura fine mese",
        "Ada Lovelace",
        who.vat_number or "?",
        "Acme S.r.l.",
        "09876543210",
        "Via Milano 3, 10100 Torino (TO)",
        "rebase S.r.l.",
        "Mario Rossi",
        "2 novembre 2026",
        "29 novembre 2026",
        "3 giorni a settimana",
        "Sviluppo del backend",
        "Da remoto",
        engagement.letter_number or "?",
    ):
        assert printed in text.replace("\\", ""), printed
    # What rebase agrees with the client never reaches a freelancer's document.
    assert CLIENT_PRICE not in text and "7.331" not in text and "73.310" not in text
    assert "firma professionista" in preview.blank and "data firma" in preview.blank
    assert "Pagamento a incasso" not in text


@needs_binaries
def test_a_letter_that_pays_after_the_client_prints_its_clause_and_the_backstop(
    world: World,
) -> None:
    who = world.freelancer()
    engagement = world.engagement(
        who,
        payment_rule="on_collection",
        payment_days=None,
        payment_end_of_month=False,
        collection_days=15,
    )
    text = world.letters.preview_letter(engagement.id, {}, ADMIN).markdown
    assert "15 giorni dopo che il Cliente ha saldato rebase per lo stesso lavoro" in text
    assert "ricevimento della fattura" in text
    assert "**Pagamento a incasso.**" in text


def test_a_payment_term_over_the_legal_limit_is_refused_before_any_letter(world: World) -> None:
    who = world.freelancer()
    for overrides in (
        {"payment_days": 61, "payment_end_of_month": False},
        {"payment_days": 45, "payment_end_of_month": True},
        {
            "payment_rule": "on_collection",
            "payment_days": None,
            "payment_end_of_month": False,
            "collection_days": 61,
        },
    ):
        with pytest.raises(ValidationFailed, match="81/2017"):
            world.engagement(who, **overrides)
    assert (
        world.session.scalars(select(Engagement).where(Engagement.freelancer_id == who.id)).all()
        == []
    )


@needs_binaries
def test_an_occasional_freelancer_gets_the_occasional_text_paid_on_a_receipt(world: World) -> None:
    who = world.freelancer(supplier_kind="occasional", vat_number=None)
    engagement = world.engagement(who)
    assert engagement.letter_type == "occasional"
    preview = world.letters.preview_letter(engagement.id, {}, ADMIN)
    assert preview.letter_type == "occasional"
    assert "30 giorni data ricevuta fine mese" in preview.markdown
    assert "LVLDAA15B10Z114X" in preview.markdown


@needs_binaries
def test_a_stored_letter_is_a_document_of_the_freelancer_written_once(
    db_session: Session, world: World
) -> None:
    who = world.freelancer()
    engagement = world.engagement(who)
    stored = world.letters.create_letter(engagement.id, {"attivita": "Sviluppo"}, ADMIN)

    document = db_session.get(Document, stored.document.id)
    assert document is not None
    assert (document.type, document.customer_id, document.engagement_id) == (
        "engagement_letter",
        who.id,
        engagement.id,
    )
    assert (document.letter_type, document.text_version, document.current_version) == (
        "ordinary",
        "1.2",
        1,
    )
    assert stored.number == engagement.letter_number and engagement.letter_number in document.title
    version = db_session.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    ).one()
    assert version.content_type == "application/pdf" and version.template_id is not None
    assert version.variables is not None and version.variables["attivita"] == "Sviluppo"
    assert "emittente" not in version.variables
    assert world.storage.get(version.storage_key).startswith(b"%PDF-")

    with pytest.raises(Conflict, match="già la sua lettera"):
        world.letters.create_letter(engagement.id, {}, ADMIN)


def test_a_stored_letter_needs_the_freelancers_tax_data_and_a_draft_engagement(
    db_session: Session, world: World
) -> None:
    bare = world.freelancer(tax_code=None)
    engagement = world.engagement(bare)
    with pytest.raises(ValidationFailed, match="tax_code"):
        world.letters.create_letter(engagement.id, {}, ADMIN)
    no_vat = world.freelancer(vat_number=None)
    with pytest.raises(ValidationFailed, match="vat_number"):
        world.letters.create_letter(world.engagement(no_vat).id, {}, ADMIN)

    sent = world.freelancer()
    out = world.engagement(sent)
    world.engagements.transition(out.id, "awaiting_signature", ADMIN)
    with pytest.raises(Conflict, match="in bozza"):
        world.letters.create_letter(out.id, {}, ADMIN)


def test_the_administrators_fields_cannot_shadow_what_the_records_say(world: World) -> None:
    engagement = world.engagement(world.freelancer())
    for name in ("emittente", "professionista", "incarico", "firma", "cliente"):
        with pytest.raises(ValidationFailed, match="dall'incarico"):
            world.letters.preview_letter(engagement.id, {name: "falso"}, ADMIN)
    with pytest.raises(ValidationFailed, match="minuscole"):
        world.letters.preview_letter(engagement.id, {"Periodo-Verifica": "x"}, ADMIN)
    with pytest.raises(ValidationFailed, match="caratteri"):
        world.letters.preview_letter(engagement.id, {"luogo": "x" * 4001}, ADMIN)


# ---- drafts and framework agreements --------------------------------------------------------


@needs_binaries
def test_a_text_still_in_draft_previews_with_a_watermark_and_is_never_stored(
    db_session: Session, world: World
) -> None:
    who = world.freelancer()
    engagement = world.engagement(who)
    template = world.final_template("engagement_letter", "ordinary")
    body = template.body_markdown.replace("status: final", "status: draft", 1)
    TemplateService(db_session).update(template.id, TemplateUpdate(body_markdown=body), ADMIN)

    preview = world.letters.preview_letter(engagement.id, {}, ADMIN)
    assert preview.draft is True
    with pytest.raises(ValidationFailed, match="bozza"):
        world.letters.create_letter(engagement.id, {}, ADMIN)
    assert (
        db_session.scalars(select(Document).where(Document.engagement_id == engagement.id)).all()
        == []
    )


@needs_binaries
def test_a_framework_agreement_hangs_on_the_freelancer_and_a_regime_has_one(
    db_session: Session, world: World
) -> None:
    who = world.freelancer()
    preview = world.letters.preview_framework_agreement(who.id, None, ADMIN)
    assert (preview.kind, preview.letter_type, preview.text_version) == (
        "framework_agreement",
        "ordinary",
        "1.1",
    )
    assert "Ada Lovelace" in preview.markdown and "Mario Rossi" in preview.markdown

    stored = world.letters.create_framework_agreement(who.id, None, ADMIN)
    document = db_session.get(Document, stored.document.id)
    assert document is not None
    assert (document.type, document.customer_id, document.engagement_id) == (
        "framework_agreement",
        who.id,
        None,
    )
    assert stored.number is None
    with pytest.raises(Conflict, match="contratto quadro"):
        world.letters.create_framework_agreement(who.id, None, ADMIN)
    # The other regime is a different agreement, but only the freelancer's own kind is allowed.
    with pytest.raises(ValidationFailed, match="professional"):
        world.letters.create_framework_agreement(who.id, "occasional", ADMIN)


def test_numbers_follow_the_year_and_a_cancelled_engagement_keeps_its_own(world: World) -> None:
    who = world.freelancer()
    first, second = world.engagement(who), world.engagement(who)
    year = today_local().year
    assert (first.letter_number, second.letter_number) == (f"{year}-001", f"{year}-002")
    world.engagements.cancel(first.id, ADMIN)
    assert world.engagement(who).letter_number == f"{year}-003"


def test_the_legal_entity_keeps_its_representative_when_a_save_leaves_it_out(
    db_session: Session, world: World
) -> None:
    service = LegalEntityService(db_session)
    fields = {**LEGAL_ENTITY_FIELDS}
    del fields["legal_representative"]
    saved = service.upsert_default(LegalEntityUpsert(**fields), ADMIN)
    assert saved.legal_representative == "Mario Rossi"
    values = service.as_template_values(ADMIN)["emittente"]
    assert values["sede"] == "Via Roma 1, 20100 Milano (MI)"
    assert values["rappresentante"] == "Mario Rossi"
    cleared = service.upsert_default(LegalEntityUpsert(**fields, legal_representative=""), ADMIN)
    assert cleared.legal_representative == ""
