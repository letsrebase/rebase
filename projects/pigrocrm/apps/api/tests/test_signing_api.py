"""Signing over HTTP (REB-1019, collaborators design § 2 and § 6): send an engagement, ask
Documenso where a document stands, mail the link again, cancel a framework agreement, record a
notice, upload a document signed by hand, and cancel an engagement whose letter is out for
signature. The service behind the routes is the core suite's (`test_signing.py`): this file is the
routes, their roles and the 503 of an installation without Documenso or mail.

Every call reaches a Documenso fake and a recording sender through the one seam the API has,
`get_signing_factory`; no test calls a real Documenso.
"""

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fakes.fake_documenso import FakeDocumenso
from fakes.letter_world import ADMIN, LEGAL_ENTITY_FIELDS, World, needs_binaries
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.documenso.client import DocumensoClient
from pigrocrm.core.documents.models import Document
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.mail import EmailSender, RecordingSender
from pigrocrm.core.storage import DocumentStorage
from pigrocrm_api.signing import get_signing_factory

pytestmark = needs_binaries

ISSUER_MAIL = "contratti@rebase.test"
SIGNED_AT = dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.UTC)
SIGNED_PDF = b"%PDF-1.7\n1 0 obj\n<< >>\nendobj\n%%EOF\n"


class Desk:
    """A world, a Documenso fake and a mail recorder behind the API's signing seam. Set
    `documenso` or `sender` to `None` for an installation without one."""

    def __init__(self, client: TestClient, session: Session, tmp_path: Path) -> None:
        self.client = client
        self.session = session
        self.fake = FakeDocumenso()
        self.documenso: DocumensoClient | None = self.fake.client()
        self.mails = RecordingSender()
        self.sender: EmailSender | None = self.mails
        settings = Settings(_env_file=None, module_collaborators=True)  # type: ignore[call-arg]

        def factory(
            session: Session, storage: DocumentStorage, settings: Settings, tenant_id: UUID | None
        ) -> SigningService:
            return SigningService(
                session,
                storage,
                settings,
                documenso=self.documenso,
                sender=self.sender,
                tenant_id=tenant_id,
            )

        client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
        client.app.dependency_overrides[get_signing_factory] = lambda: factory  # type: ignore[attr-defined]
        self.world = World(session, tmp_path)
        LegalEntityService(session).upsert_default(
            LegalEntityUpsert(**LEGAL_ENTITY_FIELDS, email=ISSUER_MAIL), ADMIN
        )
        self.who = self.world.freelancer()
        self.engagement = self.world.engagement(self.who)
        self.world.letters.create_letter(self.engagement.id, {"attivita": "Sviluppo"}, ADMIN)

    def document(self, kind: str) -> Document:
        return self.session.scalars(
            select(Document).where(Document.type == kind, Document.customer_id == self.who.id)
        ).one()

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.client.post(path, **kwargs)

    def send(self) -> Any:
        return self.post(f"/api/engagements/{self.engagement.id}/send")

    def sign(self, envelope_id: str) -> None:
        """The freelancer signs on the signing site; Documenso's webhook is lost, so the admin
        refreshes the document."""
        self.fake.sign(envelope_id, SIGNED_AT)

    def state_of(self, document: Document) -> str:
        read = self.client.get(f"/api/documents/{document.id}/signature")
        assert read.status_code == 200, read.text
        return str(read.json()["state"])


@pytest.fixture
def desk(client: TestClient, api_session: Session, tmp_path: Path) -> Iterator[Desk]:
    yield Desk(client, api_session, tmp_path)


def test_sending_an_engagement_sends_its_framework_agreement_first(
    logged_in: TestClient, desk: Desk
) -> None:
    sent = desk.send()
    assert sent.status_code == 200, sent.text
    report = sent.json()
    assert (report["sent"], report["mail_sent"]) == ("framework_agreement", True)
    assert report["engagement"]["state"] == "awaiting_signature"
    assert {d["kind"]: d["state"] for d in report["documents"]} == {
        "framework_agreement": "sent",
        "engagement_letter": "pending",
    }
    framework = desk.document("framework_agreement")
    # The root installation writes `root`, never a tenant id, so the webhook finds it again.
    (payload,) = desk.fake.created()
    assert payload["externalId"] == f"root:{framework.id}"
    assert [mail.to for mail in desk.mails.sent] == ["ada@example.com"]

    read = logged_in.get(f"/api/documents/{framework.id}/signature")
    assert read.status_code == 200, read.text
    assert read.json()["state"] == "sent"
    # The link is a capability: never in a read.
    assert "signing_url" not in read.text and "firma.example.test" not in read.text


def test_refreshing_a_signed_document_stores_the_copy_and_releases_the_letter(
    logged_in: TestClient, desk: Desk
) -> None:
    assert desk.send().status_code == 200
    framework = desk.document("framework_agreement")
    assert framework.envelope_id is not None
    desk.sign(framework.envelope_id)

    refreshed = desk.post(f"/api/documents/{framework.id}/signature/refresh")
    assert refreshed.status_code == 200, refreshed.text
    body = refreshed.json()
    assert (body["state"], body["has_signed_copy"], body["active"]) == ("signed", True, True)
    assert body["signed_by_hand"] is False
    # The letter that waited for the agreement has left.
    assert desk.state_of(desk.document("engagement_letter")) == "sent"
    assert len(desk.fake.envelopes) == 2


def test_the_mail_with_the_link_goes_again_only_for_a_document_that_waits(
    logged_in: TestClient, desk: Desk
) -> None:
    assert desk.send().status_code == 200
    framework, letter = desk.document("framework_agreement"), desk.document("engagement_letter")

    again = desk.post(f"/api/documents/{framework.id}/signature/resend-mail")
    assert again.status_code == 200, again.text
    assert [mail.to for mail in desk.mails.sent] == ["ada@example.com", "ada@example.com"]
    # A letter that waits for its agreement has no link to send.
    waiting = desk.post(f"/api/documents/{letter.id}/signature/resend-mail")
    assert waiting.status_code == 409, waiting.text


def test_a_framework_agreement_out_for_signature_is_cancelled_on_documenso_too(
    logged_in: TestClient, desk: Desk
) -> None:
    assert desk.send().status_code == 200
    framework, letter = desk.document("framework_agreement"), desk.document("engagement_letter")

    # A letter is cancelled with its engagement, never on its own.
    refused = desk.post(f"/api/documents/{letter.id}/signature/cancel")
    assert refused.status_code == 409, refused.text

    cancelled = desk.post(f"/api/documents/{framework.id}/signature/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert desk.fake.envelopes["envelope_0001"].status == "CANCELLED"
    # The freelancer, who had the link, is told it is dead.
    assert len(desk.mails.sent) == 2


def test_a_notice_is_recorded_on_an_active_framework_agreement_only(
    logged_in: TestClient, desk: Desk
) -> None:
    assert desk.send().status_code == 200
    framework = desk.document("framework_agreement")
    assert framework.envelope_id is not None

    early = desk.post(f"/api/documents/{framework.id}/signature/notice")
    assert early.status_code == 409, early.text

    desk.sign(framework.envelope_id)
    assert desk.post(f"/api/documents/{framework.id}/signature/refresh").status_code == 200
    served = desk.post(f"/api/documents/{framework.id}/signature/notice")
    assert served.status_code == 200, served.text
    assert (served.json()["state"], served.json()["active"]) == ("notice_served", False)
    assert served.json()["notice_at"] is not None


def test_cancelling_the_engagement_cancels_the_letters_envelope(
    logged_in: TestClient, desk: Desk
) -> None:
    assert desk.send().status_code == 200
    framework = desk.document("framework_agreement")
    assert framework.envelope_id is not None
    desk.sign(framework.envelope_id)
    assert desk.post(f"/api/documents/{framework.id}/signature/refresh").status_code == 200
    letter = desk.document("engagement_letter")
    assert letter.envelope_id == "envelope_0002"
    assert desk.fake.envelopes["envelope_0002"].status == "PENDING"

    moved = desk.post(f"/api/engagements/{desk.engagement.id}/state", json={"state": "cancelled"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["state"] == "cancelled"
    # The link the freelancer got stops working, and the document says why.
    assert desk.fake.envelopes["envelope_0002"].status == "CANCELLED"
    assert desk.state_of(desk.document("engagement_letter")) == "cancelled"


def test_a_signed_pdf_uploaded_by_hand_signs_the_letter_and_activates_the_engagement(
    logged_in: TestClient, desk: Desk
) -> None:
    letter = desk.document("engagement_letter")

    uploaded = desk.post(
        f"/api/documents/{letter.id}/signed-copy",
        files={"file": ("firmata.pdf", SIGNED_PDF, "application/pdf")},
        data={"signed_on": "2026-11-03"},
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert (body["state"], body["signed_by_hand"], body["has_signed_copy"]) == (
        "signed",
        True,
        True,
    )
    assert body["signed_at"].startswith("2026-11-03")
    engagement = logged_in.get(f"/api/engagements/{desk.engagement.id}")
    assert engagement.json()["state"] == "active"
    # Nothing went out: no Documenso was called.
    assert desk.fake.calls == []

    # The same document cannot be recorded twice, and the file is checked like any upload.
    again = desk.post(
        f"/api/documents/{letter.id}/signed-copy",
        files={"file": ("firmata.pdf", SIGNED_PDF, "application/pdf")},
    )
    assert again.status_code == 409, again.text


def test_the_upload_refuses_a_bad_day_a_missing_file_and_a_document_that_went_out(
    logged_in: TestClient, desk: Desk
) -> None:
    letter = desk.document("engagement_letter")
    path = f"/api/documents/{letter.id}/signed-copy"
    pdf = {"file": ("firmata.pdf", SIGNED_PDF, "application/pdf")}

    bad_day = desk.post(path, files=pdf, data={"signed_on": "ieri"})
    assert bad_day.status_code == 422, bad_day.text
    no_file = desk.post(path, data={"signed_on": "2026-11-03"}, files={"other": ("x", b"x")})
    assert no_file.status_code == 422, no_file.text
    not_a_pdf = desk.post(path, files={"file": ("a.txt", b"testo", "text/plain")})
    assert not_a_pdf.status_code == 422, not_a_pdf.text
    assert desk.state_of(desk.document("engagement_letter")) == "generated"

    # A framework agreement whose link is live is signed there or cancelled first.
    assert desk.send().status_code == 200
    framework = desk.document("framework_agreement")
    out = desk.post(f"/api/documents/{framework.id}/signed-copy", files=pdf)
    assert out.status_code == 409, out.text


@pytest.mark.parametrize("missing", ["documenso", "mail"])
def test_without_documenso_or_without_mail_sending_is_unavailable(
    logged_in: TestClient, desk: Desk, missing: str
) -> None:
    if missing == "documenso":
        desk.documenso = None
    else:
        desk.sender = None

    unavailable = desk.send()
    assert unavailable.status_code == 503, unavailable.text
    assert unavailable.json()["code"] == "signing_unavailable"
    assert desk.fake.envelopes == {}
    assert desk.state_of(desk.document("engagement_letter")) == "generated"


def test_a_documenso_that_refuses_is_a_bad_gateway_and_nothing_is_marked_sent(
    logged_in: TestClient, desk: Desk
) -> None:
    desk.fake.fail("create", 400, "Il PDF non va bene")

    refused = desk.send()
    assert refused.status_code == 502, refused.text
    assert refused.json()["code"] == "documenso_failed"
    assert desk.state_of(desk.document("engagement_letter")) == "generated"


def test_a_readonly_person_signs_nothing(readonly_client: TestClient, desk: Desk) -> None:
    letter = desk.document("engagement_letter")

    assert desk.send().status_code == 403
    assert desk.post(f"/api/documents/{letter.id}/signature/refresh").status_code == 403
    assert desk.post(f"/api/documents/{letter.id}/signature/cancel").status_code == 403
    assert desk.post(f"/api/documents/{letter.id}/signature/notice").status_code == 403
    assert desk.post(f"/api/documents/{letter.id}/signature/resend-mail").status_code == 403
    assert readonly_client.get(f"/api/documents/{letter.id}/signature").status_code == 403
    uploaded = desk.post(
        f"/api/documents/{letter.id}/signed-copy",
        files={"file": ("firmata.pdf", SIGNED_PDF, "application/pdf")},
    )
    assert uploaded.status_code == 403, uploaded.text
    assert desk.fake.calls == []
