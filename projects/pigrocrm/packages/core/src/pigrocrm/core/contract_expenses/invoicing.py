"""Spec §7's own Done-when, the second half: "a reimbursable expense becomes an
ordinary `InvoiceLine`" the way the day-lifecycle issue already wired it
(`work_units/invoicing.py`, spec §9's own call site). This
module mirrors that one's shape exactly, substituting one line per unbilled
reimbursable expense for one line per `(rate_card, month)` group of unbilled days
-- an expense has no rate card to group by, and each is its own commercial fact, so
grouping would only hide which receipt a line covers.

No schema change to `Invoice`/`InvoiceLine`, and no cap enforcement: `amount_cap`
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
directly around its own single `assemble_draft` call instead, the same warning that
module's own docstring gives.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contract_expenses.repository import ContractExpenseRepository
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.assembly import assemble_draft
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
    lines = [
        InvoiceLineIn(
            description=expense.description,
            quantity=Decimal(1),
            unit_price=expense.amount,
        )
        for expense in expenses
    ]
    return lines, [expense.id for expense in expenses]


def bind_contract_expenses(session: Session, expense_ids: list[UUID], line_ids: list[UUID]) -> None:
    """Sets `invoice_line_id` on every expense to its assembled line's id,
    positionally -- called only after the draft's lines exist, so the ids being bound
    are real, persisted lines. Flushes, never commits.

    Each expense is read again under a row lock (`populate_existing`, so a copy loaded
    by the gather is replaced by the locked row) and refused unless it is still on no
    line: the gather ran in another transaction, and two requests can both have seen the
    same expense as unbilled. The loser fails instead of repointing the first draft's
    line."""
    for expense_id, line_id in zip(expense_ids, line_ids, strict=True):
        expense = session.get(
            ContractExpense, expense_id, with_for_update=True, populate_existing=True
        )
        if expense is None:
            raise NotFound(ENTITY, expense_id)
        if expense.invoice_line_id is not None:
            raise Conflict(
                ENTITY,
                "una spesa è già stata fatturata nel frattempo: riprova",
                contract_expense_id=str(expense_id),
            )
        expense.invoice_line_id = line_id
    session.flush()


def assemble_unbilled_contract_expenses_into_new_invoice(
    session: Session, storage: DocumentStorage, contract_id: UUID, actor: Actor
) -> InvoiceRead:
    """The end-to-end common case: every unbilled, reimbursable expense on
    `contract_id` becomes one line each on a fresh draft invoice for the
    contract's own customer, under the contract's own azienda (a customer billed by
    another azienda than the contract does not move the draft), and each expense is
    bound to the line it produced. The draft, its lines and the binding are one
    transaction (`assemble_draft`): an expense billed in the meantime refuses the whole
    thing and no draft is left behind."""
    contract = session.get(Contract, contract_id)
    if contract is None or contract.deleted_at is not None:
        raise NotFound("contract", contract_id)

    lines, expense_ids = unbilled_contract_expense_lines(session, contract_id)
    if not lines:
        raise ValidationFailed(
            ENTITY,
            "contract_id",
            "nessuna spesa rimborsabile e non ancora fatturata su questo contratto",
            expected=(
                "almeno un contract_expense rimborsabile senza invoice_line_id "
                f"sul contratto {contract_id}"
            ),
        )

    return assemble_draft(
        InvoiceService(session, storage),
        InvoiceCreate(customer_id=contract.customer_id, lines=lines),
        actor,
        legal_entity_id=contract.legal_entity_id,
        bind=lambda line_ids: bind_contract_expenses(session, expense_ids, line_ids),
    )


__all__ = [
    "assemble_unbilled_contract_expenses_into_new_invoice",
    "bind_contract_expenses",
    "unbilled_contract_expense_lines",
]
