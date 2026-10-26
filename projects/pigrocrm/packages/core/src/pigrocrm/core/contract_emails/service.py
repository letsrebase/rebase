"""Saved email templates per contract, and the step that turns one into a draft with the
invoice and the register attached (REB-753).

Nothing here sends. `prepare` writes an `EmailDraft` on the customer, through the same
`EmailDraftService` the composer uses, and the person sends it with `POST
/api/email-drafts/{id}/send`: the one endpoint in the product that sends an email, which
has no MCP counterpart and never will (`routers/email_drafts.py` says why). So this
module has none either.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.drafts import EmailDraftService
from pigrocrm.core.gmail.schemas import EmailDraftCreate, EmailDraftRead
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.people.models import Person
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.templates.renderer import DeclaredVariable, render_template
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.register import ContractRegisterService

ENTITY = "contract_email_template"
CONTRACT_ENTITY = "contract"

# What a template may name, declared once: the editor lists these, the save check renders
# against sample values for them, and `values_for` supplies them. `scadenza` is optional
# because an invoice need not have one.
TEMPLATE_VARIABLES: tuple[DeclaredVariable, ...] = (
    DeclaredVariable("cliente", "Cliente", "text", True),
    DeclaredVariable("contratto", "Contratto", "text", True),
    DeclaredVariable("periodo", "Periodo del registro", "text", True),
    DeclaredVariable("numero_fattura", "Numero della fattura", "text", True),
    DeclaredVariable("data_fattura", "Data della fattura", "text", True),
    DeclaredVariable("importo", "Totale della fattura", "text", True),
    DeclaredVariable("giornate", "Giornate in fattura", "text", True),
    DeclaredVariable("scadenza", "Scadenza", "text", False),
)
# `emittente.*` is the azienda's own identity, the same scope every template reads.
EMITTER_VARIABLES = ("emittente.ragione_sociale", "emittente.telefono", "emittente.sito_web")

_SAMPLE: dict[str, Any] = {
    "cliente": "Acme S.r.l.",
    "contratto": "Consulenza",
    "periodo": "01/10/2026 - 31/10/2026",
    "numero_fattura": "2026/12",
    "data_fattura": "31/10/2026",
    "importo": "1.250,00 €",
    "giornate": "2.50",
    "scadenza": "30/11/2026",
    "emittente": {"ragione_sociale": "Studio Rossi", "telefono": "", "sito_web": ""},
}


def variables() -> list[ContractEmailVariable]:
    named = [ContractEmailVariable(nome=v.nome, etichetta=v.etichetta) for v in TEMPLATE_VARIABLES]
    named.extend(ContractEmailVariable(nome=n, etichetta=n) for n in EMITTER_VARIABLES)
    return named


def _italian_date(giorno: date | None) -> str:
    return "" if giorno is None else f"{giorno.day:02d}/{giorno.month:02d}/{giorno.year}"


def _money(value: Decimal, divisa: str) -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} €" if divisa == "EUR" else f"{text} {divisa}"


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
    def _check_renders(oggetto: str, corpo: str) -> None:
        """An unknown `{{name}}` fails here, at save, and not on the day a client's invoice
        is being sent: the same promise mastro's own placeholder check makes."""
        _render(oggetto, _SAMPLE)
        _render(corpo, _SAMPLE)

    # ---- the saved templates ---------------------------------------------------------

    def list_for_contract(self, contract_id: UUID, actor: Actor) -> list[ContractEmailTemplateRead]:
        self._contract(contract_id)
        rows = self.session.scalars(
            select(ContractEmailTemplate)
            .where(ContractEmailTemplate.contract_id == contract_id)
            .order_by(func.lower(ContractEmailTemplate.nome))
        )
        return [ContractEmailTemplateRead.model_validate(r) for r in rows]

    def create(
        self, contract_id: UUID, data: ContractEmailTemplateCreate, actor: Actor
    ) -> ContractEmailTemplateRead:
        actor.require_write("create_contract_email_template")
        self._contract(contract_id)
        self._check_renders(data.oggetto, data.corpo)
        row = ContractEmailTemplate(
            contract_id=contract_id,
            nome=data.nome,
            oggetto=data.oggetto,
            corpo=data.corpo,
            allegati=list(dict.fromkeys(data.allegati)),
        )
        self.session.add(row)
        self._flush_unique(row)
        self.activities.record(
            CONTRACT_ENTITY, contract_id, "email_template_saved", actor, {"nome": data.nome}
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
        for field in ("nome", "oggetto", "corpo", "attivo"):
            if changes.get(field) is not None:
                setattr(row, field, changes[field])
        if changes.get("allegati") is not None:
            row.allegati = list(dict.fromkeys(changes["allegati"]))
        self._check_renders(row.oggetto, row.corpo)
        self._flush_unique(row)
        self.activities.record(
            CONTRACT_ENTITY, contract_id, "email_template_saved", actor, {"nome": row.nome}
        )
        self.session.commit()
        return ContractEmailTemplateRead.model_validate(row)

    def _flush_unique(self, row: ContractEmailTemplate) -> None:
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                ENTITY, "questo contratto ha già un modello con questo nome", nome=row.nome
            ) from exc

    # ---- from a template and an invoice to a draft -----------------------------------

    def _invoice_of(self, contract: Contract, invoice_id: UUID) -> Invoice:
        invoice = self.session.get(Invoice, invoice_id)
        if invoice is None or invoice.deleted_at is not None:
            raise NotFound("invoice", invoice_id)
        if invoice.customer_id != contract.customer_id:
            raise ValidationFailed(
                ENTITY, "invoice_id", "la fattura non è di questo cliente", expected="una fattura"
            )
        if invoice.tipo != "fattura" or invoice.stato != "emessa" or invoice.numero is None:
            raise ValidationFailed(
                ENTITY,
                "invoice_id",
                "si invia una fattura emessa: una bozza non ha ancora un numero",
                expected="una fattura emessa",
            )
        return invoice

    def _days_of(self, contract_id: UUID, invoice: Invoice) -> tuple[date, date, Decimal]:
        row = self.session.execute(
            select(func.min(WorkUnit.data), func.max(WorkUnit.data), func.sum(WorkUnit.quantita))
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
        quantita: Decimal,
        actor: Actor,
    ) -> dict[str, Any]:
        customer = self.session.get(Customer, contract.customer_id)
        assert invoice.anno is not None and invoice.numero is not None
        emitter = LegalEntityService(self.session).as_template_values(actor, invoice.azienda_id)
        return {
            "cliente": customer.ragione_sociale if customer is not None else "",
            "contratto": contract.titolo,
            "periodo": f"{_italian_date(da)} - {_italian_date(a)}",
            "numero_fattura": numero_completo(invoice.anno, invoice.numero),
            "data_fattura": _italian_date(invoice.data_emissione),
            "importo": _money(invoice.totale, invoice.divisa),
            "giornate": str(quantita),
            "scadenza": _italian_date(invoice.data_scadenza) or None,
            **emitter,
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
        da, a, quantita = self._days_of(contract_id, invoice)
        values = self._values(contract, invoice, da, a, quantita, actor)
        allegati = [
            ContractEmailAttachment(tipo="fattura_pdf", nome=f"Fattura {values['numero_fattura']}")
            for kind in template.allegati
            if kind == "fattura_pdf"
        ] + [
            ContractEmailAttachment(
                tipo="registro_pdf", nome=f"Registro giornate {values['periodo']}"
            )
            for kind in template.allegati
            if kind == "registro_pdf"
        ]
        return ContractEmailPreview(
            subject=_render(template.oggetto, values),
            body=_render(template.corpo, values),
            to_suggeriti=self._suggested(contract),
            allegati=allegati,
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
        if not template.attivo:
            raise Conflict(ENTITY, "questo modello è archiviato", template_id=str(template_id))
        invoice = self._invoice_of(contract, invoice_id)
        da, a, quantita = self._days_of(contract_id, invoice)
        values = self._values(contract, invoice, da, a, quantita, actor)

        documents = DocumentRepository(self.session)
        version_ids: list[UUID] = []
        if "fattura_pdf" in template.allegati:
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
                else documents.version(document.id, document.versione_corrente)
            )
            if version is None:
                raise NotFound("document_version", invoice.pdf_document_id)
            version_ids.append(version.id)
        if "registro_pdf" in template.allegati:
            archived = ContractRegisterService(self.session, storage).render_pdf(
                contract_id, da, a, actor
            )
            version = documents.version(archived.id, archived.versione_corrente)
            assert version is not None
            version_ids.append(version.id)

        return EmailDraftService(self.session, settings=settings).create(
            EmailDraftCreate(
                entity_type="customer",
                entity_id=contract.customer_id,
                to_addresses=to_addresses,
                subject=_render(template.oggetto, values),
                body_markdown=_render(template.corpo, values),
                attachment_version_ids=version_ids,
            ),
            actor,
            azienda_id=invoice.azienda_id,
        )


__all__ = ["ContractEmailService", "TEMPLATE_VARIABLES", "variables"]
