"""The register of a contract's days for a period: the document a client asks for next to
an invoice (REB-752), ported from mastro's `buildRegister` and its two renderers.

One set of figures, two formats, as the timesheet is (`timetracking/report.py`): the PDF
is the archived artefact attached to the invoice, the CSV is what the client's own
tracking sheet takes in. Both read `ContractRegisterService.variables_for`, so the two
cannot disagree about a row or a total.

**Which days.** Those on a line of an invoice, whatever state they have moved to since: a day
billed and then disputed still names the approval that authorised it, and one a person moved
back to `worked` is still on the invoice. Mastro decides by state (`invoiced`, `disputed`,
`paid`); here the day graph lets a day enter `disputed` with no invoice and leave it with
its line still set, so the state alone would add an unbilled day and drop a billed one. The
link is what an invoice's total is checked against. A day on no line is not on it.

**A day with no approval stays on it.** Mastro leaves such a day out (an INNER JOIN on
the approval), because its register promises every row carries its approval reference. That
holds there because a contract there always has approvals; here `requires_prior_approval`
defaults to false, so a contract that never asked for one would print an empty register
beside an invoice that bills its days. The day is listed with an empty approval cell, and
`without_approval` counts them, so the total matches the invoice and the gap is stated
rather than hidden.
"""

import csv
import datetime as dt
import io
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.clock import today_local
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

REGISTER_TEMPLATE_NAME = "Registro giornate"

REGISTER_TEMPLATE_VARIABLES: tuple[dict[str, Any], ...] = (
    {"name": "periodo", "label": "Periodo", "type": "text", "required": True},
    {
        "name": "totale_quantita",
        "label": "Totale quantità",
        "type": "number",
        "required": True,
    },
    {"name": "numero_voci", "label": "Numero giornate", "type": "number", "required": True},
)

CSV_HEADER: tuple[str, ...] = ("Data", "Quantità", "Descrizione", "Fattura", "Approvazione")
CSV_TOTAL_LABEL = "Totale"
CSV_CONTENT_TYPE = "text/csv; charset=utf-8"


class RegisterApproval(BaseModel):
    channel: str
    sender: str
    received_at: datetime
    message_id: str | None


class RegisterEntry(BaseModel):
    work_unit_id: UUID
    date: dt.date
    quantity: Decimal
    description: str
    status: str
    invoice_number: str | None = None
    approval: RegisterApproval | None = None


class ContractRegister(BaseModel):
    contract_id: UUID
    title: str
    customer: str
    date_from: dt.date
    date_to: dt.date
    entries: list[RegisterEntry]
    quantity_total: Decimal
    without_approval: int


def approval_reference(approval: RegisterApproval | None) -> str:
    """The text of the approval cell, in both formats: the channel verbatim, the sender,
    the day it arrived and the message id, joined by ` · `. Not a sentence and not
    translated: the client is entitled to look the message up in their own mailbox, and a
    contractual figure stays unambiguous. Empty for a day with no approval."""
    if approval is None:
        return ""
    parts = [approval.channel, approval.sender, approval.received_at.date().isoformat()]
    if approval.message_id:
        parts.append(approval.message_id)
    return " · ".join(parts)


def _invoice_label(year: int | None, number: int | None) -> str | None:
    return None if year is None or number is None else f"{year}/{number}"


# A spreadsheet runs a cell that opens with one of these as a formula. The description and
# the sender are text somebody typed, and the file goes to a client who opens it in Excel.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value: str) -> str:
    return f"'{value}" if value.startswith(_FORMULA_LEADERS) else value


def _italian_date(day: dt.date) -> str:
    return f"{day.day:02d}/{day.month:02d}/{day.year}"


class ContractRegisterService:
    def __init__(self, session: Session, storage: DocumentStorage | None = None) -> None:
        self.session = session
        self.storage = storage
        self.templates = TemplateRepository(session)

    def build(
        self,
        contract_id: UUID,
        date_from: dt.date,
        date_to: dt.date,
        actor: Actor,
        *,
        invoice_id: UUID | None = None,
    ) -> ContractRegister:
        """`invoice_id` narrows it to the days on that one invoice, for the register that is
        attached to it: a period can hold days billed on another, and the total has to be
        the invoice's."""
        if date_to < date_from:
            raise ValidationFailed(
                ENTITY, "to", "il periodo finisce prima di iniziare", expected="to >= from"
            )
        contract = self.session.get(Contract, contract_id)
        if contract is None or contract.deleted_at is not None:
            raise NotFound("contract", contract_id)
        customer = self.session.get(Customer, contract.customer_id)

        query = (
            select(WorkUnit, Approval, Invoice.year, Invoice.number)
            .outerjoin(Approval, Approval.id == WorkUnit.approval_id)
            # LEFT, unlike mastro's own approval join and for the reason in the module
            # docstring; and a day belongs on the register because it was billed, never
            # because its invoice has been numbered yet.
            .outerjoin(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
            .outerjoin(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(
                WorkUnit.contract_id == contract_id,
                WorkUnit.date >= date_from,
                WorkUnit.date <= date_to,
                WorkUnit.invoice_line_id.is_not(None),
            )
            .order_by(WorkUnit.date, WorkUnit.id)
        )
        if invoice_id is not None:
            query = query.where(Invoice.id == invoice_id)
        rows = self.session.execute(query).all()

        entries = [
            RegisterEntry(
                work_unit_id=day.id,
                date=day.date,
                quantity=day.quantity,
                description=day.description,
                status=day.status,
                invoice_number=_invoice_label(year, number),
                approval=(
                    None
                    if approval is None
                    else RegisterApproval(
                        channel=approval.channel,
                        sender=approval.sender,
                        received_at=approval.received_at,
                        message_id=approval.message_id,
                    )
                ),
            )
            for day, approval, year, number in rows
        ]
        return ContractRegister(
            contract_id=contract.id,
            title=contract.title,
            customer=customer.legal_name if customer is not None else "",
            date_from=date_from,
            date_to=date_to,
            entries=entries,
            quantity_total=sum((v.quantity for v in entries), start=Decimal("0")),
            without_approval=sum(1 for v in entries if v.approval is None),
        )

    def variables_for(self, register: ContractRegister) -> dict[str, Any]:
        """Everything the PDF needs, finished: no cell is computed by the template."""
        return {
            "periodo": f"{_italian_date(register.date_from)} - {_italian_date(register.date_to)}",
            "oggi": _italian_date(today_local()),
            "contratto": {"titolo": register.title},
            "voci": [
                {
                    "data": _italian_date(entry.date),
                    "quantita": str(entry.quantity),
                    "descrizione": entry.description,
                    "fattura": entry.invoice_number or "",
                    "approvazione": approval_reference(entry.approval),
                }
                for entry in register.entries
            ],
            "totale_quantita": str(register.quantity_total),
            "numero_voci": str(len(register.entries)),
        }

    def csv_filename(self, register: ContractRegister) -> str:
        return (
            f"registro-giornate-{register.date_from.isoformat()}-{register.date_to.isoformat()}.csv"
        )

    def build_csv(self, register: ContractRegister) -> bytes:
        """RFC 4180: CRLF row endings, a field quoted only when it needs to be, a header
        row, one row per day and a totals row. ISO dates and plain decimals, never a
        locale's own: a separator or a day-month order is exactly the ambiguity a document
        the client can dispute must not carry. UTF-8 with a BOM, which is what makes Excel
        read the accents instead of guessing."""
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        for entry in register.entries:
            writer.writerow(
                (
                    entry.date.isoformat(),
                    str(entry.quantity),
                    _csv_text(entry.description),
                    entry.invoice_number or "",
                    _csv_text(approval_reference(entry.approval)),
                )
            )
        writer.writerow(("", str(register.quantity_total), CSV_TOTAL_LABEL, "", ""))
        return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")

    def _template(self) -> Template:
        """The editable copy of the layout. A space born before this template existed has
        no row, and the PDF must not depend on somebody running `seed-templates` first, so
        the first render creates it, exactly as the seed would."""
        template = self.templates.get_by_name(REGISTER_TEMPLATE_NAME)
        if template is not None:
            return template
        try:
            # A savepoint of its own: two first requests can both find no row, and the loser's
            # insert is refused by the unique name. It rolls back to here and reads the winner's.
            with self.session.begin_nested():
                template = self.templates.add(
                    Template(
                        name=REGISTER_TEMPLATE_NAME,
                        type="hours_report",
                        body_markdown=(ASSETS_DIR / "template-day-register.md").read_text(
                            encoding="utf-8"
                        ),
                        declared_variables=list(REGISTER_TEMPLATE_VARIABLES),
                        active=True,
                    )
                )
            # Committed on purpose: `create_from_template` commits the session before it
            # renders, so the seed is filed here, deliberately, as the seed command would.
            self.session.commit()
            return template
        except IntegrityError:
            existing = self.templates.get_by_name(REGISTER_TEMPLATE_NAME)
            if existing is None:
                raise
            return existing

    def render_pdf(
        self,
        contract_id: UUID,
        date_from: dt.date,
        date_to: dt.date,
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
        register = self.build(contract_id, date_from, date_to, actor, invoice_id=invoice_id)
        contract = self.session.get(Contract, contract_id)
        if contract is None:
            raise NotFound("contract", contract_id)
        template = self._template()
        return DocumentService(self.session, self.storage).create_from_template(
            DocumentFromTemplate(
                template_id=template.id,
                title=(
                    f"Registro giornate {register.title} "
                    f"{date_from.isoformat()} {date_to.isoformat()}"
                ),
                customer_id=contract.customer_id,
                deal_id=None,
                variables=self.variables_for(register),
                # The contract's own legal entity, not the customer's current one: a customer
                # moved to another legal entity keeps its contracts where they were.
                legal_entity_id=contract.legal_entity_id,
            ),
            actor,
        )


__all__ = [
    "ContractRegister",
    "ContractRegisterService",
    "RegisterEntry",
    "approval_reference",
]
