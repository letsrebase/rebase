"""Spec §7's own Done-when, the second half: "a reimbursable expense becomes an
ordinary `InvoiceLine` through the same `replace_lines` path" the day-lifecycle
issue already wired (`work_units/invoicing.py`, spec §9's own call site). This
module mirrors that one's shape exactly, substituting one line per unbilled
reimbursable expense for one line per `(rate_card, month)` group of unbilled days
-- an expense has no rate card to group by, and each is its own commercial fact, so
grouping would only hide which receipt a line covers.

No schema change to `Invoice`/`InvoiceLine`, and no cap enforcement: `importo_tetto`
(`contract_expenses.models`'s own docstring) constrains how much of an expense a
human rebills at review time, not what this assembly step invents on its own --
inventing a capped figure here would be exactly the commercial decision
`unbilled_work_unit_lines` already refuses to make for an unpriced day (spec §9's
own "raises `ValidationFailed`... inventing a price here would be a commercial
decision this step does not own").

`assemble_unbilled_contract_expenses_into_new_invoice` is the convenience path for
the common case, mirroring `assemble_unbilled_work_units_into_new_invoice`
exactly. A caller assembling a contract's days *and* its expenses onto the same
invoice should use `unbilled_contract_expense_lines`/`bind_contract_expenses`
directly around its own single `replace_lines` call instead, the same warning that
module's own docstring gives.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contract_expenses.repository import ContractExpenseRepository
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn, InvoiceRead
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.base import DocumentStorage

ENTITY = "contract_expense"


def unbilled_contract_expense_lines(
    session: Session, contract_id: UUID
) -> tuple[list[InvoiceLineIn], list[UUID]]:
    """`(righe, expense_ids)`: one `InvoiceLineIn` per unbilled, reimbursable
    expense, and the id each line prices, in the same order -- positionally
    zippable with the line ids `replace_lines` produces (`bind_contract_expenses`),
    the same mechanism `bind_work_units` uses for a `work_unit`."""
    expenses = ContractExpenseRepository(session).unbilled_for_contract(contract_id)
    righe = [
        InvoiceLineIn(
            descrizione=expense.descrizione,
            quantita=Decimal(1),
            prezzo_unitario=expense.importo,
        )
        for expense in expenses
    ]
    return righe, [expense.id for expense in expenses]


def bind_contract_expenses(session: Session, expense_ids: list[UUID], line_ids: list[UUID]) -> None:
    """Sets `invoice_line_id` on every expense to its assembled line's id,
    positionally -- called only after `InvoiceService.replace_lines` has returned,
    so the ids being bound are real, persisted lines."""
    for expense_id, line_id in zip(expense_ids, line_ids, strict=True):
        expense = session.get(ContractExpense, expense_id)
        assert expense is not None, f"contract_expense {expense_id} vanished mid-assembly"
        expense.invoice_line_id = line_id


def assemble_unbilled_contract_expenses_into_new_invoice(
    session: Session, storage: DocumentStorage, contract_id: UUID, actor: Actor
) -> InvoiceRead:
    """The end-to-end common case: every unbilled, reimbursable expense on
    `contract_id` becomes one line each on a fresh draft invoice for the
    contract's own customer, through `InvoiceService.create` then `replace_lines`
    -- mirroring `assemble_unbilled_work_units_into_new_invoice`'s own shape --
    and each expense is bound to the line it produced."""
    contract = session.get(Contract, contract_id)
    if contract is None or contract.deleted_at is not None:
        raise NotFound("contract", contract_id)

    righe, expense_ids = unbilled_contract_expense_lines(session, contract_id)
    if not righe:
        raise ValidationFailed(
            ENTITY,
            "contract_id",
            "nessuna spesa rimborsabile e non ancora fatturata su questo contratto",
            expected=(
                "almeno un contract_expense rimborsabile senza invoice_line_id "
                f"sul contratto {contract_id}"
            ),
        )

    service = InvoiceService(session, storage)
    invoice = service.create(InvoiceCreate(customer_id=contract.customer_id, righe=[]), actor)
    invoice = service.replace_lines(invoice.id, righe, actor)

    line_ids = list(
        session.execute(
            select(InvoiceLine.id)
            .where(InvoiceLine.invoice_id == invoice.id)
            .order_by(InvoiceLine.numero_linea)
        ).scalars()
    )
    bind_contract_expenses(session, expense_ids, line_ids)
    session.commit()
    return invoice


__all__ = [
    "assemble_unbilled_contract_expenses_into_new_invoice",
    "bind_contract_expenses",
    "unbilled_contract_expense_lines",
]
