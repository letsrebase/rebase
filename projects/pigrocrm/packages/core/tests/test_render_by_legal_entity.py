"""Every PDF and every mail speaks for the legal entity of its record (REB-627, spec
2026-10-03 §1.8, §6): a document of legal entity X carries X's header and X's logo, the
invoice PDF of X likewise, a reminder for X's invoice signs as X and names X's IBAN, a
draft about X's customer leaves in X's name and under X's domain. The second legal entity is
written by row, since nothing creates one before milestone 5.
"""

import shutil
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.schemas import CustomerCreate, CustomerUpdate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents import templating as documents_templating_module
from pigrocrm.core.documents.schemas import DocumentFromTemplate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.gmail.drafts import EmailDraftService
from pigrocrm.core.gmail.models import EmailDraft, PaymentReminder
from pigrocrm.core.gmail.payment_reminders import PaymentRemindersService
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import EmailDraftCreate
from pigrocrm.core.invoices import pdf as invoice_pdf
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.assets import LegalEntityAssets
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.people.models import Person
from pigrocrm.core.render.pdf import BLANK_PNG
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.templates.schemas import TemplateCreate
from pigrocrm.core.templates.service import TemplateService

ADMIN = Actor(id=None, type="system", role="admin")
SETTINGS = Settings(jwt_secret="x" * 32)
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
    b'<rect width="8" height="8"/></svg>'
)


def _default(session: Session) -> LegalEntity:
    row = LegalEntityRepository(session).default()
    assert row is not None
    return row


def _second(session: Session) -> LegalEntity:
    row = LegalEntity(
        name="rebase",
        legal_name="Rebase S.r.l.",
        vat_number="09876543210",
        country="IT",
        website="https://rebase.example",
        email_signature="Rebase, con affetto",
        address="Via Po 1",
        cap="10100",
        city="Torino",
        province="TO",
    )
    session.add(row)
    session.flush()
    return row


def _fiscal(session: Session, legal_entity_id: UUID, iban: str) -> None:
    FiscalProfileService(session).upsert(
        FiscalProfileUpsert(regime_code="RF19", iban=iban), ADMIN, legal_entity_id=legal_entity_id
    )


def _customer_of(session: Session, legal_entity_id: UUID, name: str = "Overseas") -> UUID:
    return (
        CustomerService(session)
        .create(CustomerCreate(legal_name=f"{name} S.r.l.", legal_entity_id=legal_entity_id), ADMIN)
        .id
    )


# --- documents --------------------------------------------------------------------


def test_to_document_renders_from_its_own_legal_entity_with_its_logo(
    db_session: Session, local_storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = _second(db_session)
    LegalEntityAssets(db_session, local_storage).set_logo(SVG, ADMIN, second.id)
    customer_id = _customer_of(db_session, second.id)
    template = TemplateService(db_session).create(
        TemplateCreate(
            name="Brief", type="document", body_markdown="# {{cliente.ragione_sociale}}\n"
        ),
        ADMIN,
    )
    seen: dict[str, Any] = {}

    def fake_render(markdown: str, *, header_typst: str, settings: Any, media: Any = None) -> bytes:
        seen["header"] = header_typst
        seen["media"] = dict(media or {})
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(documents_templating_module, "render_pdf", fake_render)
    service = DocumentService(db_session, local_storage, SETTINGS)
    document = service.create_from_template(
        DocumentFromTemplate(
            template_id=template.id, customer_id=customer_id, title="Brief", variables={}
        ),
        ADMIN,
    )
    assert document.legal_entity_id == second.id
    # The header escapes the dots of «S.r.l.» for Typst, so the name is matched up to them.
    assert "Rebase S" in seen["header"]
    assert '#image("./media/logo.svg"' in seen["header"]
    assert seen["media"] == {"logo.svg": SVG}

    # The default azienda has no logo: its documents set the name in type.
    acme = _customer_of(db_session, _default(db_session).id, "Acme")
    service.create_from_template(
        DocumentFromTemplate(
            template_id=template.id, customer_id=acme, title="Brief", variables={}
        ),
        ADMIN,
    )
    assert "#image(" not in seen["header"] and seen["media"] == {}
    assert _default(db_session).legal_name in seen["header"]


# --- invoices ---------------------------------------------------------------------


def test_the_invoice_pdf_carries_the_issuing_legal_entity_s_logo(
    db_session: Session, local_storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = _second(db_session)
    _fiscal(db_session, _default(db_session).id, "IT60X0542811101000000123456")
    _fiscal(db_session, second.id, "IT60X0542811101000000654321")
    LegalEntityAssets(db_session, local_storage).set_logo(BLANK_PNG, ADMIN, second.id)
    customer_id = _customer_of(db_session, second.id)
    invoices = InvoiceService(db_session, local_storage, SETTINGS)
    proforma = invoices.create(
        InvoiceCreate(
            customer_id=customer_id,
            type="proforma",
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("100.00"))],
        ),
        ADMIN,
    )
    # A proforma renders once confirmed: a draft has neither a number nor a snapshot.
    invoices.confirm_proforma(proforma.id, ADMIN)
    if shutil.which("pandoc") and shutil.which("typst"):
        # The real thing once, so the header's `#image` branch is compiled by Typst and
        # not only matched as text: the artefact comes back a PDF.
        artifacts = invoices.produce_artifacts(proforma.id, ADMIN)
        assert artifacts and artifacts[0].content_type == "application/pdf"
    seen: dict[str, Any] = {}

    def fake_render(export: Any, *, reference: Any, settings: Any, media: Any = None) -> tuple:
        seen["media"] = dict(media or {})
        seen["header"] = invoice_pdf.build_invoice_header(
            invoice_pdf.build_scope(export, reference=reference),
            logo=next(iter(seen["media"]), None),
        )
        return "md", b"%PDF-1.7 fake"

    monkeypatch.setattr(invoice_pdf, "render_invoice_pdf", fake_render)
    invoices.produce_artifacts(proforma.id, ADMIN)
    assert seen["media"] == {"logo.png": BLANK_PNG}
    assert '#image("./media/logo.png"' in seen["header"]
    assert "Rebase S" in seen["header"]


def test_the_invoice_header_without_a_logo_sets_the_name_in_type() -> None:
    scope = {
        "emittente": {"ragione_sociale": "Studio Rossi", "indirizzo_display": ""},
        "fiscale": {"regime_display": "RF19 Regime forfettario"},
    }
    header = invoice_pdf.build_invoice_header(scope)
    assert "#image(" not in header and 'weight: "bold")[Studio Rossi]' in header
    assert '#image("./media/logo.png"' in invoice_pdf.build_invoice_header(scope, logo="logo.png")


# --- mail ---------------------------------------------------------------------------


def test_to_mail_speaks_for_the_legal_entity_of_its_record(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    default = _default(db_session)
    second = _second(db_session)
    customer_id = _customer_of(db_session, second.id)
    deal = DealService(db_session).create(
        DealCreate(
            name="Migration", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id
        ),
        ADMIN,
    )
    person = Person(first_name="Ada", last_name="Lovelace", customer_id=customer_id)
    loose = Person(first_name="Nessuno", last_name="Di Nessuno")
    db_session.add_all([person, loose])
    db_session.flush()
    repo = GmailRepository(db_session)
    assert repo.legal_entity_for("customer", customer_id).id == second.id  # type: ignore[union-attr]
    assert repo.legal_entity_for("deal", deal.id).id == second.id  # type: ignore[union-attr]
    assert repo.legal_entity_for("person", person.id).id == second.id  # type: ignore[union-attr]
    # A contact with no customer, and a record that does not exist: the default's name.
    assert repo.legal_entity_for("person", loose.id).id == default.id  # type: ignore[union-attr]
    assert repo.legal_entity_for("customer", uuid4()).id == default.id  # type: ignore[union-attr]

    drafts = EmailDraftService(db_session, settings=SETTINGS)
    assert drafts._domain("customer", customer_id) == "rebase.example"
    # The default azienda's site for a record with none; the admitted fallback without.
    default.website = None
    db_session.flush()
    assert drafts._domain("person", loose.id) == "localhost.invalid"


def test_to_reminder_signs_as_the_invoice_s_legal_entity_and_names_its_iban(
    db_session: Session,
) -> None:
    default = _default(db_session)
    second = _second(db_session)
    _fiscal(db_session, default.id, "IT60X0542811101000000123456")
    _fiscal(db_session, second.id, "IT60X0542811101000000654321")
    payment_reminders = PaymentRemindersService(db_session, settings=SETTINGS)
    scope = payment_reminders._legal_entity_scope(ADMIN, second.id)["emittente"]
    assert (scope["ragione_sociale"], scope["firma_email"]) == (
        "Rebase S.r.l.",
        "Rebase, con affetto",
    )
    assert payment_reminders._iban(second.id) == "IT60X0542811101000000654321"
    assert payment_reminders._iban(default.id) == "IT60X0542811101000000123456"


def test_to_reminder_s_draft_speaks_for_the_invoice_s_legal_entity_even_after_the_customer_moved(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The body and the IBAN of a reminder are the invoice's azienda's; so are the `From`
    name and the Message-ID domain of the draft that carries it, although the draft is
    filed under the customer, who may since have moved (Greptile, PR #510)."""
    default = _default(db_session)
    second = _second(db_session)
    _fiscal(db_session, default.id, "IT60X0542811101000000123456")
    customer_id = _customer_of(db_session, default.id)
    invoice = InvoiceService(db_session, local_storage, SETTINGS).create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1.00"))],
        ),
        ADMIN,
    )
    assert invoice.legal_entity_id == default.id
    CustomerService(db_session).update(
        customer_id, CustomerUpdate(legal_entity_id=second.id), ADMIN
    )

    reminder = PaymentReminder(invoice_id=invoice.id, sequence=1)
    db_session.add(reminder)
    db_session.flush()
    draft = EmailDraft(
        entity_type="customer",
        entity_id=customer_id,
        subject="Sollecito",
        body_markdown="…",
        message_id_header="<x@rebase.example>",
        send_state="draft",
        payment_reminder_id=reminder.id,
    )
    db_session.add(draft)
    db_session.flush()
    repo = GmailRepository(db_session)
    # The customer is azienda B's now; the reminder still signs as A, who issued.
    assert repo.legal_entity_for("customer", customer_id).id == second.id  # type: ignore[union-attr]
    assert repo.legal_entity_for_draft(draft).id == default.id  # type: ignore[union-attr]
    # And the Message-ID domain the reminder's draft is minted with is A's site.
    default.website = "https://studio.example"
    db_session.flush()
    drafts = EmailDraftService(db_session, settings=SETTINGS)
    assert drafts._domain("customer", customer_id, default.id) == "studio.example"
    assert drafts._domain("customer", customer_id) == "rebase.example"
    # And the draft a reminder creates, handed the invoice's azienda as the reminder
    # does, stores a Message-ID under that azienda's domain.
    created = drafts.create(
        EmailDraftCreate(
            entity_type="customer",
            entity_id=customer_id,
            to_addresses=["cliente@example.com"],
            subject="Sollecito",
            body_markdown="…",
        ),
        ADMIN,
        legal_entity_id=default.id,
    )
    assert created.message_id_header.endswith("@studio.example>")
