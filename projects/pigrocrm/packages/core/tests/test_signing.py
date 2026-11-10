"""Signing, through a Documenso fake (REB-1019, collaborators design § 2 and § 8): a framework
agreement then a letter are sent, only the freelancer signs, and the envelope is found again from
its `externalId`; a draft text is never sent, a cancelled document is cancelled on Documenso too,
a lost webhook is recovered, and without a Documenso a letter is signed by hand.
"""

import datetime as dt
from pathlib import Path
from uuid import UUID

import pytest
from fakes.fake_documenso import FakeDocumenso
from fakes.letter_world import (
    ADMIN,
    LEGAL_ENTITY_FIELDS,
    World,
    needs_binaries,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.collaborators.service import EngagementService
from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.config import Settings
from pigrocrm.core.documenso.client import (
    COMPLETED,
    Outcome,
    WebhookBody,
    outcome_from_webhook,
)
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    DocumensoFailed,
    SigningUnavailable,
    ValidationFailed,
)
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.schemas import TemplateUpdate
from pigrocrm.core.templates.service import TemplateService

TENANT = UUID("018f0000-0000-7000-8000-000000000001")
SIGNED_AT = dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.UTC)
ISSUER_MAIL = "contratti@rebase.test"
AGENT = Actor(id=None, type="mcp", role="admin", full_access=True)
PLAIN_AGENT = Actor(id=None, type="mcp", role="admin", full_access=False)


class Desk:
    """A world, a Documenso fake, a mail sender and the service built on them."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.session: Session = world.session
        self.fake = FakeDocumenso()
        self.mails = RecordingSender()
        LegalEntityService(self.session).upsert_default(
            LegalEntityUpsert(**LEGAL_ENTITY_FIELDS, email=ISSUER_MAIL), ADMIN
        )
        self.service = self.build()
        self.who = world.freelancer()
        self.engagement = world.engagement(self.who)
        world.letters.create_letter(self.engagement.id, {"attivita": "Sviluppo"}, ADMIN)

    def build(
        self, *, documenso: bool = True, sender: bool = True, tenant: UUID | None = TENANT
    ) -> SigningService:
        return SigningService(
            self.session,
            self.world.storage,
            Settings(),
            documenso=self.fake.client() if documenso else None,
            sender=self.mails if sender else None,
            tenant_id=tenant,
        )

    def document(self, kind: str) -> Document:
        return self.session.scalars(
            select(Document).where(Document.type == kind, Document.customer_id == self.who.id)
        ).one()

    def send(self) -> None:
        self.service.send_engagement(self.engagement.id, ADMIN)

    def webhook(self, envelope_id: str, event: str = "DOCUMENT_COMPLETED") -> Outcome:
        outcome = outcome_from_webhook(
            WebhookBody.model_validate(self.fake.webhook(envelope_id, event))
        )
        assert outcome is not None
        return outcome

    def sign(self, document: Document) -> None:
        """What happens when the freelancer signs and Documenso tells us: the signer's own date,
        the webhook applied, and what a signature leaves to do."""
        assert document.envelope_id is not None
        self.fake.sign(document.envelope_id, SIGNED_AT)
        signed = self.service.apply(self.webhook(document.envelope_id))
        assert signed == document.id
        self.service.recovery.finish(document.id)

    def state(self, kind: str) -> str | None:
        self.session.expire_all()
        return self.document(kind).signature_state

    def engagement_state(self) -> str:
        self.session.expire_all()
        engagement = self.session.get(Engagement, self.engagement.id)
        assert engagement is not None
        return engagement.state


@pytest.fixture
def desk(db_session: Session, tmp_path: Path) -> Desk:
    return Desk(World(db_session, tmp_path))


def _subjects(desk: Desk) -> list[str]:
    return [mail.subject for mail in desk.mails.sent]


# ---- the way through: a framework agreement, then a letter, and only the freelancer signs ------


@needs_binaries
def test_a_framework_agreement_then_a_letter_are_sent_and_only_the_freelancer_signs(
    desk: Desk,
) -> None:
    report = desk.service.send_engagement(desk.engagement.id, ADMIN)

    # The freelancer has no framework agreement: it is written and leaves first, the letter waits.
    assert (report.sent, report.mail_sent) == ("framework_agreement", True)
    assert report.engagement.state == "awaiting_signature"
    framework, letter = desk.document("framework_agreement"), desk.document("engagement_letter")
    assert (framework.signature_state, letter.signature_state) == ("sent", "pending")
    assert framework.engagement_id is None and letter.engagement_id == desk.engagement.id
    (payload,) = desk.fake.created()
    # One recipient, a signer, the freelancer: nothing asks the issuer for a signature.
    (recipient,) = payload["recipients"]
    assert (recipient["role"], recipient["email"], recipient["name"]) == (
        "SIGNER",
        "ada@example.com",
        "Ada Lovelace",
    )
    assert {field["type"] for field in recipient["fields"]} == {"SIGNATURE", "DATE"}
    assert payload["externalId"] == f"{TENANT}:{framework.id}"
    assert payload["meta"]["distributionMethod"] == "NONE"
    assert not any(payload["meta"]["emailSettings"].values())
    # The link goes out by this app's own mail, once.
    (mail,) = desk.mails.sent
    assert mail.to == "ada@example.com" and "Da firmare: contratto quadro" in mail.subject
    assert "https://firma.example.test/sign/token0001" in mail.text

    desk.sign(framework)

    # The framework agreement is signed with the signer's own date and its sealed copy is stored
    # as the next version; both mails went out and the waiting letter left.
    framework = desk.document("framework_agreement")
    assert framework.signature_state == "signed" and framework.signed_at == SIGNED_AT
    version = desk.session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == framework.id,
            DocumentVersion.number == framework.signed_version,
        )
    ).one()
    assert desk.world.storage.get(version.storage_key) == desk.fake.signed_pdf("envelope_0001")
    assert framework.signed_copy_to_freelancer_at and framework.signed_copy_to_issuer_at
    recipients = [mail.to for mail in desk.mails.sent]
    assert recipients == ["ada@example.com", "ada@example.com", ISSUER_MAIL, "ada@example.com"]
    signed_copies = [mail for mail in desk.mails.sent if mail.attachments]
    assert len(signed_copies) == 2
    assert signed_copies[0].attachments[0].content == desk.fake.signed_pdf("envelope_0001")
    assert desk.state("engagement_letter") == "sent"
    letter = desk.document("engagement_letter")
    assert letter.envelope_id == "envelope_0002"
    assert desk.fake.envelopes["envelope_0002"].payload["externalId"] == f"{TENANT}:{letter.id}"
    assert _subjects(desk)[-1].startswith("Da firmare: lettera di incarico n. ")
    # The letter stands on the agreement it names, and the issuer's blank says who issued it.
    first = desk.session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == letter.id)
        .order_by(DocumentVersion.number.desc())
    ).first()
    assert first is not None and first.markdown_source is not None
    assert "3 novembre 2026" in first.markdown_source
    assert "Documento emesso da rebase S\\.r\\.l\\." in first.markdown_source
    assert desk.engagement_state() == "awaiting_signature"

    desk.sign(letter)

    # The letter is signed: the engagement is active, and the signed copy is stored.
    assert desk.state("engagement_letter") == "signed"
    assert desk.engagement_state() == "active"
    assert desk.document("engagement_letter").signed_version is not None
    assert len(desk.fake.envelopes) == 2


# ---- the rules that did not change ------------------------------------------------------------


@needs_binaries
def test_a_signed_framework_agreement_lets_the_letter_leave_by_itself_on_a_later_send(
    desk: Desk,
) -> None:
    """The framework agreement of the letter's regime is signed: the letter leaves at once, and
    nothing is written or sent for the agreement a second time."""
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    desk.sign(desk.document("framework_agreement"))
    # The release already sent the letter; sending the engagement again has nothing to send.
    with pytest.raises(Conflict, match="già partita"):
        desk.service.send_engagement(desk.engagement.id, ADMIN)
    assert len(desk.fake.envelopes) == 2


@needs_binaries
def test_a_second_engagement_of_a_freelancer_with_an_active_agreement_sends_only_its_letter(
    desk: Desk,
) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    desk.sign(desk.document("framework_agreement"))
    desk.sign(desk.document("engagement_letter"))
    other = desk.world.engagement(desk.who, starts_on=dt.date(2027, 1, 4), ends_on=None)
    desk.world.letters.create_letter(other.id, {}, ADMIN)

    report = desk.service.send_engagement(other.id, ADMIN)

    assert report.sent == "engagement_letter"
    assert len(desk.fake.envelopes) == 3
    assert desk.service.read(report.documents[-1].document_id).state == "sent"


def _draft(session: Session, kind: str, regime: str = "ordinary") -> None:
    template = session.scalars(
        select(Template).where(Template.type == kind, Template.letter_type == regime)
    ).one()
    TemplateService(session).update(
        template.id,
        TemplateUpdate(
            body_markdown=template.body_markdown.replace("status: final", "status: draft")
        ),
        ADMIN,
    )


@needs_binaries
def test_a_text_still_in_draft_is_never_sent(desk: Desk) -> None:
    """Either text: the letter's, with a framework agreement already out, and the agreement's."""
    _draft(desk.session, "engagement_letter")
    with pytest.raises(ValidationFailed, match="bozza"):
        desk.service.send_engagement(desk.engagement.id, ADMIN)
    assert desk.fake.calls == [] and desk.mails.sent == []
    assert desk.engagement_state() == "draft"


@needs_binaries
def test_a_draft_agreement_text_is_never_written_or_sent(desk: Desk) -> None:
    _draft(desk.session, "framework_agreement")
    with pytest.raises(ValidationFailed, match="bozza"):
        desk.service.send_engagement(desk.engagement.id, ADMIN)
    assert desk.fake.calls == [] and desk.engagement_state() == "draft"


@needs_binaries
def test_without_a_documenso_or_a_mail_sender_nothing_is_sent(desk: Desk) -> None:
    with pytest.raises(SigningUnavailable, match="Documenso"):
        desk.build(documenso=False).send_engagement(desk.engagement.id, ADMIN)
    with pytest.raises(SigningUnavailable, match="email"):
        desk.build(sender=False).send_engagement(desk.engagement.id, ADMIN)
    assert desk.fake.calls == [] and desk.engagement_state() == "draft"
    assert desk.state("engagement_letter") == "generated"


@needs_binaries
def test_a_refusal_from_documenso_leaves_everything_as_it_was(desk: Desk) -> None:
    desk.fake.fail("create", 500, "boom")
    with pytest.raises(DocumensoFailed, match="boom"):
        desk.service.send_engagement(desk.engagement.id, ADMIN)
    assert desk.engagement_state() == "draft" and desk.mails.sent == []
    assert desk.state("engagement_letter") == "generated"
    # An envelope Documenso made and the app could not finish is removed again, not left behind.
    desk.fake.fail("distribute", 400, "no")
    with pytest.raises(DocumensoFailed):
        desk.service.send_engagement(desk.engagement.id, ADMIN)
    assert desk.fake.envelopes == {} and desk.fake.called("delete") == 1


@needs_binaries
def test_only_a_person_writes_the_agreement_an_agent_sends(desk: Desk) -> None:
    with pytest.raises(AgentForbidden):
        desk.service.send_engagement(desk.engagement.id, AGENT)
    assert desk.fake.calls == []
    # Once a person wrote it, an agent with full access sends it.
    desk.world.letters.create_framework_agreement(desk.who.id, None, ADMIN)
    report = desk.service.send_engagement(desk.engagement.id, AGENT)
    assert report.sent == "framework_agreement"
    with pytest.raises(AgentForbidden):
        desk.service.send_engagement(desk.engagement.id, PLAIN_AGENT)


@needs_binaries
def test_a_letter_must_exist_and_an_engagement_must_be_in_the_right_state(desk: Desk) -> None:
    other = desk.world.engagement(desk.who, starts_on=dt.date(2027, 3, 1), ends_on=None)
    with pytest.raises(Conflict, match="lettera"):
        desk.service.send_engagement(other.id, ADMIN)
    desk.service.engagement_service().cancel(other.id, ADMIN)
    with pytest.raises(Conflict, match="bozza o in firma"):
        desk.service.send_engagement(other.id, ADMIN)


# ---- a cancelled document is cancelled on Documenso too ---------------------------------------


@needs_binaries
def test_cancelling_an_engagement_cancels_its_envelope_and_tells_the_freelancer(
    desk: Desk,
) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    desk.sign(desk.document("framework_agreement"))
    letter = desk.document("engagement_letter")
    envelope = desk.fake.envelopes["envelope_0002"]
    assert envelope.status == "PENDING"

    desk.service.engagement_service().cancel(desk.engagement.id, ADMIN)

    assert envelope.status == "CANCELLED"
    assert desk.state("engagement_letter") == "cancelled"
    assert desk.document("engagement_letter").cancel_reason == "Annullato con il suo incarico."
    assert desk.engagement_state() == "cancelled"
    assert desk.mails.sent[-1].subject == "Lettera di incarico n. 2026-001 annullata".replace(
        "2026", str(dt.date.today().year)
    )
    assert desk.state("framework_agreement") == "signed"
    assert letter.id == desk.document("engagement_letter").id


@needs_binaries
def test_an_engagement_whose_letter_waits_is_cancelled_without_calling_documenso(
    desk: Desk,
) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    calls = len(desk.fake.calls)
    desk.service.engagement_service().cancel(desk.engagement.id, ADMIN)
    assert desk.state("engagement_letter") == "cancelled"
    assert len(desk.fake.calls) == calls
    # The agreement is the freelancer's, not the engagement's: it stays out for signature.
    assert desk.state("framework_agreement") == "sent"


@needs_binaries
def test_a_cancellation_documenso_refuses_changes_nothing(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    desk.sign(desk.document("framework_agreement"))
    desk.fake.fail("cancel", 400, "Only pending documents can be cancelled")
    with pytest.raises(DocumensoFailed, match="Aggiorna lo stato"):
        desk.service.engagement_service().cancel(desk.engagement.id, ADMIN)
    assert desk.state("engagement_letter") == "sent"
    assert desk.engagement_state() == "awaiting_signature"
    # Documenso not answering at all keeps its own sentence: it says nothing about the document.
    desk.fake.down("cancel")
    with pytest.raises(DocumensoFailed, match="non risponde"):
        desk.service.engagement_service().cancel(desk.engagement.id, ADMIN)


@needs_binaries
def test_an_engagement_service_with_no_way_to_documenso_refuses_a_letter_out_for_signature(
    desk: Desk,
) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    desk.sign(desk.document("framework_agreement"))
    with pytest.raises(SigningUnavailable):
        EngagementService(desk.session).cancel(desk.engagement.id, ADMIN)
    assert desk.state("engagement_letter") == "sent"


@needs_binaries
def test_an_unsigned_framework_agreement_is_cancelled_on_documenso_and_a_new_one_is_written(
    desk: Desk,
) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    with pytest.raises(AgentForbidden):
        desk.service.cancel_framework_agreement(framework.id, PLAIN_AGENT)

    read = desk.service.cancel_framework_agreement(framework.id, AGENT)

    assert read.state == "cancelled"
    assert desk.fake.envelopes["envelope_0001"].status == "CANCELLED"
    assert desk.mails.sent[-1].subject.endswith("annullato")
    # Cancelled, it no longer stands in the way: the next send writes and sends a new one.
    letter = desk.document("engagement_letter")
    assert letter.signature_state == "pending"
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    assert len(desk.fake.envelopes) == 2
    agreements = desk.session.scalars(
        select(Document).where(Document.type == "framework_agreement")
    ).all()
    assert sorted(a.signature_state or "" for a in agreements) == ["cancelled", "sent"]


@needs_binaries
def test_a_letter_is_cancelled_with_its_engagement_not_on_its_own(desk: Desk) -> None:
    letter = desk.document("engagement_letter")
    with pytest.raises(Conflict, match="con il suo incarico"):
        desk.service.cancel_framework_agreement(letter.id, ADMIN)


# ---- the webhook, a refusal and a forged completion -------------------------------------------


@needs_binaries
def test_a_rejection_cancels_the_document_with_the_signers_reason(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    desk.fake.reject("envelope_0001", "Non sono d'accordo")

    assert desk.service.apply(desk.webhook("envelope_0001", "DOCUMENT_REJECTED")) is None

    assert desk.state("framework_agreement") == "cancelled"
    assert desk.document("framework_agreement").cancel_reason == (
        "Rifiutato dal freelance: Non sono d'accordo"
    )
    # Delivered twice, it changes nothing the second time.
    assert desk.service.apply(desk.webhook("envelope_0001", "DOCUMENT_REJECTED")) is None
    assert framework.id == desk.document("framework_agreement").id


@needs_binaries
def test_a_forged_completion_does_not_sign_a_document(desk: Desk) -> None:
    """The webhook's secret travels in clear: the completion is confirmed with Documenso itself."""
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    forged = Outcome("envelope_0001", COMPLETED, signed_at=SIGNED_AT)

    assert desk.service.apply(forged) == framework.id
    assert desk.service.recovery.finish(framework.id) is False

    assert desk.state("framework_agreement") == "sent"
    assert desk.document("framework_agreement").signed_at is None


@needs_binaries
def test_an_unknown_envelope_and_a_document_already_signed_are_acknowledged_and_left_alone(
    desk: Desk,
) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    assert desk.service.apply(Outcome("envelope_9999", COMPLETED)) is None
    desk.sign(desk.document("framework_agreement"))
    assert desk.service.apply(desk.webhook("envelope_0001")) is None


@needs_binaries
def test_finishing_twice_does_everything_once(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    desk.sign(framework)
    mails = len(desk.mails.sent)
    downloads = desk.fake.called("download")

    assert desk.service.recovery.finish(framework.id) is False

    assert len(desk.mails.sent) == mails and desk.fake.called("download") == downloads
    assert len(desk.fake.envelopes) == 2


@needs_binaries
def test_a_signed_copy_whose_mail_is_refused_is_mailed_by_the_next_finish(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    desk.fake.sign("envelope_0001", SIGNED_AT)
    desk.mails.accepts = False
    desk.service.apply(desk.webhook("envelope_0001"))
    desk.service.recovery.finish(framework.id)
    framework = desk.document("framework_agreement")
    assert framework.signature_state == "signed" and framework.signed_version is not None
    assert framework.signed_copy_to_freelancer_at is None
    # Released with no mail accepted: the letter left (Documenso took it), its mail is for resend.
    desk.mails.accepts = True
    assert desk.service.recovery.finish(framework.id) is True
    framework = desk.document("framework_agreement")
    assert framework.signed_copy_to_freelancer_at and framework.signed_copy_to_issuer_at


# ---- the recovery sweep -----------------------------------------------------------------------


@needs_binaries
def test_the_sweep_recovers_a_signature_the_webhook_never_delivered(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    desk.fake.sign("envelope_0001", SIGNED_AT)

    result = desk.service.recovery.sweep()

    assert (result.touched, result.unconfirmed) == (1, 0)
    assert desk.state("framework_agreement") == "signed"
    assert desk.state("engagement_letter") == "sent"
    # Nothing left to do: the next sweep touches nothing but still waits on the letter.
    again = desk.service.recovery.sweep()
    assert again.touched == 0


@needs_binaries
def test_the_sweep_always_recovers_signatures_even_without_a_second_service(desk: Desk) -> None:
    """The hub's round returned early without its engagement service; this one has no such
    condition: with Documenso reachable it recovers, with it absent it reports and moves nothing."""
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    desk.fake.sign("envelope_0001", SIGNED_AT)
    nothing = desk.build(documenso=False).recovery.sweep()
    assert (nothing.touched, nothing.unconfirmed) == (0, 0)
    assert desk.state("framework_agreement") == "sent"
    assert desk.service.recovery.sweep().touched == 1


@needs_binaries
def test_the_sweep_counts_a_document_documenso_could_not_be_asked_about(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    desk.fake.down("get")
    result = desk.service.recovery.sweep()
    assert (result.touched, result.unconfirmed) == (0, 1)
    desk.fake.fail("get", 404, "Envelope not found")
    assert desk.service.recovery.sweep().unconfirmed == 1
    assert desk.state("framework_agreement") == "sent"


@needs_binaries
def test_a_refresh_applies_what_documenso_says_and_leaves_the_rest_to_finish(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    desk.fake.sign("envelope_0001", SIGNED_AT)

    # An agent without full access may refresh: it only reads Documenso's state.
    read = desk.service.refresh(framework.id, PLAIN_AGENT)

    assert read.state == "signed" and read.has_signed_copy and read.active
    assert read.renewal is not None and read.last_notice_day is not None
    assert desk.state("engagement_letter") == "sent"
    with pytest.raises(Conflict, match="mai partito"):
        desk.service.refresh(_unsent(desk).id, ADMIN)


def _unsent(desk: Desk) -> Document:
    other = desk.world.engagement(desk.who, starts_on=dt.date(2027, 6, 7), ends_on=None)
    desk.world.letters.create_letter(other.id, {}, ADMIN)
    return desk.session.scalars(select(Document).where(Document.engagement_id == other.id)).one()


@needs_binaries
def test_the_mail_with_the_link_is_sent_again_only_while_the_document_waits(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    before = len(desk.mails.sent)
    desk.service.resend_mail(framework.id, AGENT)
    assert len(desk.mails.sent) == before + 1
    assert desk.mails.sent[-1].text == desk.mails.sent[0].text
    with pytest.raises(AgentForbidden):
        desk.service.resend_mail(framework.id, PLAIN_AGENT)
    desk.mails.accepts = False
    with pytest.raises(SigningUnavailable, match="provider"):
        desk.service.resend_mail(framework.id, ADMIN)
    desk.mails.accepts = True
    with pytest.raises(Conflict, match="aspetta la firma"):
        desk.service.resend_mail(desk.document("engagement_letter").id, ADMIN)


# ---- a notice of termination ------------------------------------------------------------------


@needs_binaries
def test_a_notice_ends_an_active_agreement_and_the_next_engagement_writes_a_new_one(
    desk: Desk,
) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    with pytest.raises(Conflict, match="attivo"):
        desk.service.record_framework_notice(framework.id, ADMIN)
    desk.sign(framework)
    with pytest.raises(AgentForbidden):
        desk.service.record_framework_notice(framework.id, PLAIN_AGENT)

    read = desk.service.record_framework_notice(framework.id, AGENT)

    assert read.state == "notice_served" and read.notice_at is not None and not read.active
    other = desk.world.engagement(desk.who, starts_on=dt.date(2027, 2, 1), ends_on=None)
    desk.world.letters.create_letter(other.id, {}, ADMIN)
    report = desk.service.send_engagement(other.id, ADMIN)
    assert report.sent == "framework_agreement"
    assert len(desk.fake.envelopes) == 3


# ---- no Documenso: signed by hand -------------------------------------------------------------

HAND_SIGNED_PDF = b"%PDF-1.7\nsigned by hand\n%%EOF\n"


@needs_binaries
def test_without_a_documenso_a_letter_is_signed_by_hand_and_its_pdf_uploaded(desk: Desk) -> None:
    manual = desk.build(documenso=False, sender=False)
    framework = desk.world.letters.create_framework_agreement(desk.who.id, None, ADMIN)
    letter = desk.document("engagement_letter")
    on = dt.date(2026, 11, 4)

    with pytest.raises(AgentForbidden):
        manual.upload_signed_copy(letter.id, HAND_SIGNED_PDF, "application/pdf", on, AGENT)
    with pytest.raises(ValidationFailed, match="PDF"):
        manual.upload_signed_copy(letter.id, b"x", "text/plain", on, ADMIN)
    read = manual.upload_signed_copy(
        framework.document.id, HAND_SIGNED_PDF, "application/pdf", on, ADMIN
    )
    assert (read.state, read.has_signed_copy, read.signed_by_hand, read.active) == (
        "signed",
        True,
        True,
        True,
    )

    done = manual.upload_signed_copy(letter.id, HAND_SIGNED_PDF, "application/pdf", on, ADMIN)

    assert done.state == "signed" and done.signed_at is not None
    assert done.signed_at.date() == on
    assert desk.engagement_state() == "active"
    version = desk.session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == letter.id,
            DocumentVersion.number == desk.document("engagement_letter").signed_version,
        )
    ).one()
    assert desk.world.storage.get(version.storage_key) == HAND_SIGNED_PDF
    assert desk.fake.calls == [] and desk.mails.sent == []
    with pytest.raises(Conflict, match="non ancora partito"):
        manual.upload_signed_copy(letter.id, HAND_SIGNED_PDF, "application/pdf", on, ADMIN)


@needs_binaries
def test_a_document_that_went_out_through_documenso_is_not_recorded_signed_by_hand(
    desk: Desk,
) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)
    framework = desk.document("framework_agreement")
    with pytest.raises(Conflict, match="non ancora partito"):
        desk.service.upload_signed_copy(
            framework.id, HAND_SIGNED_PDF, "application/pdf", None, ADMIN
        )


@needs_binaries
def test_an_engagement_that_was_never_sent_becomes_active_with_a_hand_signed_letter(
    desk: Desk,
) -> None:
    manual = desk.build(documenso=False)
    desk.world.letters.create_framework_agreement(desk.who.id, None, ADMIN)
    letter = desk.document("engagement_letter")
    assert desk.engagement_state() == "draft"
    manual.upload_signed_copy(letter.id, HAND_SIGNED_PDF, "application/pdf", None, ADMIN)
    assert desk.engagement_state() == "active"
