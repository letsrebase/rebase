"""Saved email templates per contract, and the step that turns one into a draft with the
invoice and the register attached (REB-753).

Nothing here sends. `prepare` writes an `EmailDraft` on the customer, through the same
`EmailDraftService` the composer uses, and the person sends it with `POST
/api/email-drafts/{id}/send`: the one endpoint in the product that sends an email, which
has no MCP counterpart and never will (`routers/email_drafts.py` says why). So this
module has none either.
"""

from contextlib import AbstractContextManager
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.contract_emails.models import ContractEmailTemplate
from pigrocrm.core.contract_emails.schemas import (
    ContractEmailAttachment,
    ContractEmailPreview,
    ContractEmailTemplateCreate,
    ContractEmailTemplateRead,
    ContractEmailTemplateUpdate,
    ContractEmailVariable,
)
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.drafts import EmailDraftService
from pigrocrm.core.gmail.schemas import EmailDraftCreate, EmailDraftRead
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.naming import number_complete
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.people.models import Person
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.templates.renderer import DeclaredVariable, render_template
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.register import ContractRegisterService

ENTITY = "contract_email_template"
CONTRACT_ENTITY = "contract"

# One declaration per variable: its name, label, whether a template must be given it, and the
# sample the save check renders it with. The editor lists them, the save check renders against
# the samples, and `_values` supplies the real ones. `due_date` is optional because an invoice
# need not have one.
_FIELDS: tuple[tuple[str, str, bool, str], ...] = (
    ("customer", "Cliente", True, "Acme S.r.l."),
    ("contract", "Contratto", True, "Consulenza"),
    ("period", "Periodo del registro", True, "01/10/2026 - 31/10/2026"),
    ("invoice_number", "Numero della fattura", True, "2026/12"),
    ("invoice_date", "Data della fattura", True, "31/10/2026"),
    ("amount", "Totale della fattura", True, "1.250,00 €"),
    ("work_units", "Giornate in fattura", True, "2.50"),
    ("due_date", "Scadenza", False, "30/11/2026"),
)
TEMPLATE_VARIABLES: tuple[DeclaredVariable, ...] = tuple(
    DeclaredVariable(name, label, "text", required) for name, label, required, _ in _FIELDS
)
# `emittente.*` is the azienda's own identity, the same scope every template reads.
_LEGAL_ENTITY_SAMPLE = {"legal_name": "Studio Rossi", "phone": "", "website": ""}
LEGAL_ENTITY_VARIABLES = tuple(f"emittente.{name}" for name in _LEGAL_ENTITY_SAMPLE)

_SAMPLE: dict[str, Any] = {
    **{name: sample for name, _, _, sample in _FIELDS},
    "emittente": _LEGAL_ENTITY_SAMPLE,
}


def variables() -> list[ContractEmailVariable]:
    named = [ContractEmailVariable(name=v.name, label=v.label) for v in TEMPLATE_VARIABLES]
    named.extend(ContractEmailVariable(name=n, label=n) for n in LEGAL_ENTITY_VARIABLES)
    return named


def _italian_date(day: date | None) -> str:
    return "" if day is None else f"{day.day:02d}/{day.month:02d}/{day.year}"


def _money(value: Decimal, currency: str) -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} €" if currency == "EUR" else f"{text} {currency}"


def _render(source: str, values: dict[str, Any]) -> str:
    # "plain", never the default "markdown": that one escapes the whole ASCII punctuation
    # class, so `2026/12` would reach the client as `2026\/12`.
    return render_template(source, values, TEMPLATE_VARIABLES, context="plain")


class ContractEmailService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.activities = ActivityService(session)

    def _contract(self, contract_id: UUID) -> Contract:
        contract = self.session.get(Contract, contract_id)
        if contract is None or contract.deleted_at is not None:
            raise NotFound("contract", contract_id)
        return contract

    def _template(self, contract_id: UUID, template_id: UUID) -> ContractEmailTemplate:
        template = self.session.get(ContractEmailTemplate, template_id)
        if template is None or template.contract_id != contract_id:
            raise NotFound(ENTITY, template_id)
        return template

    @staticmethod
    def _check_renders(subject: str, body: str) -> None:
        """An unknown `{{name}}` fails here, at save, and not on the day a client's invoice
        is being sent: the same promise mastro's own placeholder check makes."""
        _render(subject, _SAMPLE)
        _render(body, _SAMPLE)

    # ---- the saved templates ---------------------------------------------------------

    def list_for_contract(self, contract_id: UUID, actor: Actor) -> list[ContractEmailTemplateRead]:
        self._contract(contract_id)
        rows = self.session.scalars(
            select(ContractEmailTemplate)
            .where(ContractEmailTemplate.contract_id == contract_id)
            .order_by(func.lower(ContractEmailTemplate.name))
        )
        return [ContractEmailTemplateRead.model_validate(r) for r in rows]

    def create(
        self, contract_id: UUID, data: ContractEmailTemplateCreate, actor: Actor
    ) -> ContractEmailTemplateRead:
        actor.require_write("create_contract_email_template")
        self._contract(contract_id)
        self._check_renders(data.subject, data.body)
        row = ContractEmailTemplate(
            contract_id=contract_id,
            name=data.name,
            subject=data.subject,
            body=data.body,
            attachments=list(dict.fromkeys(data.attachments)),
        )
        with self._unique_name(data.name):
            self.session.add(row)
        self.activities.record(
            CONTRACT_ENTITY, contract_id, "email_template_saved", actor, {"name": data.name}
        )
        self.session.commit()
        return ContractEmailTemplateRead.model_validate(row)

    def update(
        self,
        contract_id: UUID,
        template_id: UUID,
        data: ContractEmailTemplateUpdate,
        actor: Actor,
    ) -> ContractEmailTemplateRead:
        actor.require_write("update_contract_email_template")
        row = self._template(contract_id, template_id)
        changes = data.model_dump(exclude_unset=True)
        subject = changes["subject"] if changes.get("subject") is not None else row.subject
        body = changes["body"] if changes.get("body") is not None else row.body
        self._check_renders(subject, body)
        name = changes["name"] if changes.get("name") is not None else row.name
        with self._unique_name(name):
            for field in ("name", "subject", "body", "active"):
                if changes.get(field) is not None:
                    setattr(row, field, changes[field])
            if changes.get("attachments") is not None:
                row.attachments = list(dict.fromkeys(changes["attachments"]))
        self.activities.record(
            CONTRACT_ENTITY, contract_id, "email_template_saved", actor, {"name": row.name}
        )
        self.session.commit()
        return ContractEmailTemplateRead.model_validate(row)

    def _unique_name(self, name: str) -> AbstractContextManager[None]:
        """The writes of a save, in a SAVEPOINT: two saves of one name at once are refused by
        the unique index, and only that write is rolled back."""
        return savepoint(
            self.session,
            lambda exc: Conflict(
                ENTITY, "questo contratto ha già un modello con questo nome", name=name
            ),
        )

    # ---- from a template and an invoice to a draft -----------------------------------

    def _invoice_of(self, contract: Contract, invoice_id: UUID) -> Invoice:
        invoice = self.session.get(Invoice, invoice_id)
        if invoice is None or invoice.deleted_at is not None:
            raise NotFound("invoice", invoice_id)
        if invoice.customer_id != contract.customer_id:
            raise ValidationFailed(
                ENTITY, "invoice_id", "la fattura non è di questo cliente", expected="una fattura"
            )
        if invoice.type != "invoice" or invoice.status != "issued" or invoice.number is None:
            raise ValidationFailed(
                ENTITY,
                "invoice_id",
                "si invia una fattura emessa: una bozza non ha ancora un numero",
                expected="una fattura emessa",
            )
        return invoice

    def _days_of(self, contract_id: UUID, invoice: Invoice) -> tuple[date, date, Decimal]:
        row = self.session.execute(
            select(func.min(WorkUnit.date), func.max(WorkUnit.date), func.sum(WorkUnit.quantity))
            .join(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
            .where(InvoiceLine.invoice_id == invoice.id, WorkUnit.contract_id == contract_id)
        ).one()
        if row[0] is None:
            raise ValidationFailed(
                ENTITY,
                "invoice_id",
                "questa fattura non porta giornate di questo contratto",
                expected="una fattura fatta con «Crea la bozza di fattura»",
            )
        return row[0], row[1], Decimal(row[2])

    def _values(
        self,
        contract: Contract,
        invoice: Invoice,
        da: date,
        a: date,
        quantity: Decimal,
        actor: Actor,
    ) -> dict[str, Any]:
        customer = self.session.get(Customer, contract.customer_id)
        if invoice.year is None or invoice.number is None:
            raise ValidationFailed(
                ENTITY,
                "invoice_id",
                "si invia una fattura emessa: una bozza non ha ancora un numero",
                expected="una fattura emessa",
            )
        legal_entity = LegalEntityService(self.session).as_template_values(
            actor, invoice.legal_entity_id
        )
        return {
            "cliente": customer.legal_name if customer is not None else "",
            "contratto": contract.title,
            "periodo": f"{_italian_date(da)} - {_italian_date(a)}",
            "numero_fattura": number_complete(invoice.year, invoice.number),
            "data_fattura": _italian_date(invoice.issue_date),
            "importo": _money(invoice.total, invoice.currency),
            "giornate": str(quantity),
            "scadenza": _italian_date(invoice.due_date) or None,
            **legal_entity,
        }

    def _suggested(self, contract: Contract) -> list[str]:
        people = self.session.scalars(
            select(Person.email).where(
                Person.customer_id == contract.customer_id,
                Person.deleted_at.is_(None),
                Person.email.is_not(None),
            )
        )
        customer = self.session.get(Customer, contract.customer_id)
        found = [e for e in people if e]
        if customer is not None and customer.email:
            found.append(customer.email)
        return list(dict.fromkeys(found))

    def preview(
        self, contract_id: UUID, template_id: UUID, invoice_id: UUID, actor: Actor
    ) -> ContractEmailPreview:
        contract = self._contract(contract_id)
        template = self._template(contract_id, template_id)
        invoice = self._invoice_of(contract, invoice_id)
        da, a, quantity = self._days_of(contract_id, invoice)
        values = self._values(contract, invoice, da, a, quantity, actor)
        attachments = [
            ContractEmailAttachment(type="invoice_pdf", name=f"Fattura {values['invoice_number']}")
            for kind in template.attachments
            if kind == "invoice_pdf"
        ] + [
            ContractEmailAttachment(
                type="register_pdf", name=f"Registro giornate {values['period']}"
            )
            for kind in template.attachments
            if kind == "register_pdf"
        ]
        return ContractEmailPreview(
            subject=_render(template.subject, values),
            body=_render(template.body, values),
            to_suggested=self._suggested(contract),
            attachments=attachments,
            da=da,
            a=a,
        )

    def prepare(
        self,
        contract_id: UUID,
        template_id: UUID,
        invoice_id: UUID,
        to_addresses: list[str],
        actor: Actor,
        *,
        settings: Settings,
        storage: DocumentStorage,
    ) -> EmailDraftRead:
        """The draft, with the files attached: the invoice's own archived PDF and the
        register's, archived now for the period the invoice's days span. A draft is what
        leaves the person one press from sending, and writes nothing to Gmail."""
        actor.require_write("prepare_contract_email")
        contract = self._contract(contract_id)
        template = self._template(contract_id, template_id)
        if not template.active:
            raise Conflict(ENTITY, "questo modello è archiviato", template_id=str(template_id))
        invoice = self._invoice_of(contract, invoice_id)
        da, a, quantity = self._days_of(contract_id, invoice)
        values = self._values(contract, invoice, da, a, quantity, actor)

        documents = DocumentRepository(self.session)
        version_ids: list[UUID] = []
        if "invoice_pdf" in template.attachments:
            if invoice.pdf_document_id is None:
                raise ValidationFailed(
                    ENTITY,
                    "invoice_id",
                    "la fattura non ha ancora un PDF: generalo dalla sua pagina",
                    expected="un PDF della fattura",
                )
            document = documents.get(invoice.pdf_document_id)
            version = (
                None
                if document is None
                else documents.version(document.id, document.current_version)
            )
            if version is None:
                raise NotFound("document_version", invoice.pdf_document_id)
            version_ids.append(version.id)
        if "register_pdf" in template.attachments:
            # Everything the render reads is read: end the transaction first, so the pooled
            # connection is not held while the render waits for one of its slots.
            self.session.commit()
            archived = ContractRegisterService(self.session, storage).render_pdf(
                contract_id, da, a, actor, invoice_id=invoice.id
            )
            version = documents.version(archived.id, archived.current_version)
            if version is None:
                raise NotFound("document_version", archived.id)
            version_ids.append(version.id)

        return EmailDraftService(self.session, settings=settings).create(
            EmailDraftCreate(
                entity_type="customer",
                entity_id=contract.customer_id,
                to_addresses=to_addresses,
                subject=_render(template.subject, values),
                body_markdown=_render(template.body, values),
                attachment_version_ids=version_ids,
            ),
            actor,
            legal_entity_id=invoice.legal_entity_id,
        )


__all__ = ["ContractEmailService", "TEMPLATE_VARIABLES", "variables"]
