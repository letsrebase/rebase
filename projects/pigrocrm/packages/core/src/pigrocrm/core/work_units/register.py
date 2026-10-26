"""The register of a contract's days for a period: the document a client asks for next to
an invoice (REB-752), ported from mastro's `buildRegister` and its two renderers.

One set of figures, two formats, as the timesheet is (`timetracking/report.py`): the PDF
is the archived artefact attached to the invoice, the CSV is what the client's own
tracking sheet takes in. Both read `ContractRegisterService.variables_for`, so the two
cannot disagree about a row or a total.

**Which days.** Those on a line of an invoice, whatever state they have moved to since: a day
billed and then disputed still names the approval that authorised it, and one a person moved
back to `lavorato` is still on the invoice. Mastro decides by state (`invoiced`, `disputed`,
`paid`); here the day graph lets a day enter `contestato` with no invoice and leave it with
its line still set, so the state alone would add an unbilled day and drop a billed one. The
link is what an invoice's total is checked against. A day on no line is not on it.

**A day with no approval stays on it.** Mastro leaves such a day out (an INNER JOIN on
the approval), because its register promises every row carries its approval reference. That
holds there because a contract there always has approvals; here `requires_prior_approval`
defaults to false, so a contract that never asked for one would print an empty register
beside an invoice that bills its days. The day is listed with an empty approval cell, and
`senza_approvazione` counts them, so the total matches the invoice and the gap is stated
rather than hidden.
"""

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.schemas import DocumentFromTemplate, DocumentRead
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.render import ASSETS_DIR
from pigrocrm.core.storage import DocumentStorage
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.repository import TemplateRepository
from pigrocrm.core.work_units.models import Approval, WorkUnit

ENTITY = "contract_register"

REGISTER_TEMPLATE_NOME = "Registro giornate"

REGISTER_TEMPLATE_VARIABLES: tuple[dict[str, Any], ...] = (
    {"nome": "periodo", "etichetta": "Periodo", "tipo": "text", "obbligatoria": True},
    {
        "nome": "totale_quantita",
        "etichetta": "Totale quantità",
        "tipo": "number",
        "obbligatoria": True,
    },
    {"nome": "numero_voci", "etichetta": "Numero giornate", "tipo": "number", "obbligatoria": True},
)

CSV_HEADER: tuple[str, ...] = ("Data", "Quantità", "Descrizione", "Fattura", "Approvazione")
CSV_TOTAL_LABEL = "Totale"
CSV_CONTENT_TYPE = "text/csv; charset=utf-8"


class RegisterApproval(BaseModel):
    canale: str
    mittente: str
    ricevuto_il: datetime
    message_id: str | None


class RegisterEntry(BaseModel):
    work_unit_id: UUID
    data: date
    quantita: Decimal
    descrizione: str
    stato: str
    numero_fattura: str | None = None
    approvazione: RegisterApproval | None = None


class ContractRegister(BaseModel):
    contract_id: UUID
    titolo: str
    cliente: str
    da: date
    a: date
    voci: list[RegisterEntry]
    totale_quantita: Decimal
    senza_approvazione: int


def approval_reference(approval: RegisterApproval | None) -> str:
    """The text of the approval cell, in both formats: the channel verbatim, the sender,
    the day it arrived and the message id, joined by ` · `. Not a sentence and not
    translated: the client is entitled to look the message up in their own mailbox, and a
    contractual figure stays unambiguous. Empty for a day with no approval."""
    if approval is None:
        return ""
    parts = [approval.canale, approval.mittente, approval.ricevuto_il.date().isoformat()]
    if approval.message_id:
        parts.append(approval.message_id)
    return " · ".join(parts)


def _invoice_label(anno: int | None, numero: int | None) -> str | None:
    return None if anno is None or numero is None else f"{anno}/{numero}"


# A spreadsheet runs a cell that opens with one of these as a formula. The description and
# the sender are text somebody typed, and the file goes to a client who opens it in Excel.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value: str) -> str:
    return f"'{value}" if value.startswith(_FORMULA_LEADERS) else value


def _italian_date(giorno: date) -> str:
    return f"{giorno.day:02d}/{giorno.month:02d}/{giorno.year}"


class ContractRegisterService:
    def __init__(self, session: Session, storage: DocumentStorage | None = None) -> None:
        self.session = session
        self.storage = storage
        self.templates = TemplateRepository(session)

    def build(
        self,
        contract_id: UUID,
        da: date,
        a: date,
        actor: Actor,
        *,
        invoice_id: UUID | None = None,
    ) -> ContractRegister:
        """`invoice_id` narrows it to the days on that one invoice, for the register that is
        attached to it: a period can hold days billed on another, and the total has to be
        the invoice's."""
        if a < da:
            raise ValidationFailed(
                ENTITY, "a", "il periodo finisce prima di iniziare", expected="a >= da"
            )
        contract = self.session.get(Contract, contract_id)
        if contract is None or contract.deleted_at is not None:
            raise NotFound("contract", contract_id)
        customer = self.session.get(Customer, contract.customer_id)

        query = (
            select(WorkUnit, Approval, Invoice.anno, Invoice.numero)
            .outerjoin(Approval, Approval.id == WorkUnit.approval_id)
            # LEFT, unlike mastro's own approval join and for the reason in the module
            # docstring; and a day belongs on the register because it was billed, never
            # because its invoice has been numbered yet.
            .outerjoin(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
            .outerjoin(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(
                WorkUnit.contract_id == contract_id,
                WorkUnit.data >= da,
                WorkUnit.data <= a,
                WorkUnit.invoice_line_id.is_not(None),
            )
            .order_by(WorkUnit.data, WorkUnit.id)
        )
        if invoice_id is not None:
            query = query.where(Invoice.id == invoice_id)
        rows = self.session.execute(query).all()

        voci = [
            RegisterEntry(
                work_unit_id=day.id,
                data=day.data,
                quantita=day.quantita,
                descrizione=day.descrizione,
                stato=day.stato,
                numero_fattura=_invoice_label(anno, numero),
                approvazione=(
                    None
                    if approval is None
                    else RegisterApproval(
                        canale=approval.canale,
                        mittente=approval.mittente,
                        ricevuto_il=approval.ricevuto_il,
                        message_id=approval.message_id,
                    )
                ),
            )
            for day, approval, anno, numero in rows
        ]
        return ContractRegister(
            contract_id=contract.id,
            titolo=contract.titolo,
            cliente=customer.ragione_sociale if customer is not None else "",
            da=da,
            a=a,
            voci=voci,
            totale_quantita=sum((v.quantita for v in voci), start=Decimal("0")),
            senza_approvazione=sum(1 for v in voci if v.approvazione is None),
        )

    def variables_for(self, register: ContractRegister) -> dict[str, Any]:
        """Everything the PDF needs, finished: no cell is computed by the template."""
        return {
            "periodo": f"{_italian_date(register.da)} - {_italian_date(register.a)}",
            "oggi": _italian_date(oggi_in_italia()),
            "contratto": {"titolo": register.titolo},
            "voci": [
                {
                    "data": _italian_date(voce.data),
                    "quantita": str(voce.quantita),
                    "descrizione": voce.descrizione,
                    "fattura": voce.numero_fattura or "",
                    "approvazione": approval_reference(voce.approvazione),
                }
                for voce in register.voci
            ],
            "totale_quantita": str(register.totale_quantita),
            "numero_voci": str(len(register.voci)),
        }

    def csv_filename(self, register: ContractRegister) -> str:
        return f"registro-giornate-{register.da.isoformat()}-{register.a.isoformat()}.csv"

    def build_csv(self, register: ContractRegister) -> bytes:
        """RFC 4180: CRLF row endings, a field quoted only when it needs to be, a header
        row, one row per day and a totals row. ISO dates and plain decimals, never a
        locale's own: a separator or a day-month order is exactly the ambiguity a document
        the client can dispute must not carry. UTF-8 with a BOM, which is what makes Excel
        read the accents instead of guessing."""
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        for voce in register.voci:
            writer.writerow(
                (
                    voce.data.isoformat(),
                    str(voce.quantita),
                    _csv_text(voce.descrizione),
                    voce.numero_fattura or "",
                    _csv_text(approval_reference(voce.approvazione)),
                )
            )
        writer.writerow(("", str(register.totale_quantita), CSV_TOTAL_LABEL, "", ""))
        return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")

    def _template(self) -> Template:
        """The editable copy of the layout. A space born before this template existed has
        no row, and the PDF must not depend on somebody running `seed-templates` first, so
        the first render creates it, exactly as the seed would."""
        template = self.templates.get_by_nome(REGISTER_TEMPLATE_NOME)
        if template is not None:
            return template
        try:
            # A savepoint of its own: two first requests can both find no row, and the loser's
            # insert is refused by the unique name. It rolls back to here and reads the winner's.
            with self.session.begin_nested():
                return self.templates.add(
                    Template(
                        nome=REGISTER_TEMPLATE_NOME,
                        tipo="rapporto_ore",
                        corpo_markdown=(ASSETS_DIR / "template-day-register.md").read_text(
                            encoding="utf-8"
                        ),
                        variabili_dichiarate=list(REGISTER_TEMPLATE_VARIABLES),
                        attivo=True,
                    )
                )
        except IntegrityError:
            existing = self.templates.get_by_nome(REGISTER_TEMPLATE_NOME)
            assert existing is not None
            return existing

    def render_pdf(
        self,
        contract_id: UUID,
        da: date,
        a: date,
        actor: Actor,
        *,
        invoice_id: UUID | None = None,
    ) -> DocumentRead:
        """Archived as a `document` on the contract's customer through the slice 2 pipeline
        unchanged, so it gets the hash, the pluggable storage and the timeline. Each call
        archives a new document, dated and never overwriting an earlier one: the file
        attached to an invoice stays reproducible a year later. (The pipeline has no way to
        render a fresh version of an existing document from new variables, so the register
        does not claim one.)"""
        actor.require_write("render_contract_register")
        if self.storage is None:
            raise ValidationFailed(
                ENTITY, "storage", "nessun backend di storage configurato", expected="uno storage"
            )
        register = self.build(contract_id, da, a, actor, invoice_id=invoice_id)
        contract = self.session.get(Contract, contract_id)
        assert contract is not None  # `build` raised NotFound otherwise
        template = self._template()
        return DocumentService(self.session, self.storage).create_from_template(
            DocumentFromTemplate(
                template_id=template.id,
                titolo=f"Registro giornate {register.titolo} {da.isoformat()} {a.isoformat()}",
                customer_id=contract.customer_id,
                deal_id=None,
                variabili=self.variables_for(register),
                # The contract's own azienda, not the customer's current one: a customer
                # moved to another azienda keeps its contracts where they were.
                azienda_id=contract.azienda_id,
            ),
            actor,
        )


__all__ = [
    "ContractRegister",
    "ContractRegisterService",
    "RegisterEntry",
    "approval_reference",
]
