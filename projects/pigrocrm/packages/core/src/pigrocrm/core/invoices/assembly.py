"""One transaction for "create a draft invoice, then bind what it bills to its lines".

The days of a contract, the reimbursable expenses and the logged hours all become an
invoice the same way: the draft is created with its lines, the caller reads the new line
ids back in order and points each billed row at its line, and everything commits
together. Done by hand that took several commits, and a failure between them left an
empty draft behind (or a hand-made soft delete to remove it).
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import select

from pigrocrm.core.actor import Actor
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceRead
from pigrocrm.core.invoices.service import InvoiceService


def assemble_draft(
    service: InvoiceService,
    data: InvoiceCreate,
    actor: Actor,
    *,
    legal_entity_id: UUID | None = None,
    bind: Callable[[list[UUID]], None],
) -> InvoiceRead:
    """Create the draft in `data` and let `bind` attach what it bills, in ONE commit.

    The lines come from `data.lines`. Inside a SAVEPOINT the draft is created without
    committing, the ids of its new lines are read ordered by `line_number`, and
    `bind(line_ids)` is called: the caller points its days, expenses or hours at those ids
    positionally (the n-th billed row to the n-th line) and may record its own activity.
    Then the transaction commits and the draft is returned.

    If `bind` or anything before it raises, only the SAVEPOINT is rolled back: the draft
    and its lines vanish, whatever else the caller had pending stays, and the exception
    propagates unchanged. Nothing here compensates by hand.
    """
    session = service.session
    with session.begin_nested():
        draft = service.create(data, actor, legal_entity_id=legal_entity_id, commit=False)
        line_ids = list(
            session.execute(
                select(InvoiceLine.id)
                .where(InvoiceLine.invoice_id == draft.id)
                .order_by(InvoiceLine.line_number)
            ).scalars()
        )
        bind(line_ids)
    session.commit()
    return draft
