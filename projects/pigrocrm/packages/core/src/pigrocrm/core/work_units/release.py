"""Giving a day back when the invoice it was billed on stops existing (REB-751).

Binding a day to a draft moves it to `invoiced` (`invoicing.py`). The state graph has no
edge from there back to `worked`, and a day left in `invoiced` on a deleted draft or an
annulled invoice could never be billed again: it would read «fatturato» with nothing
behind it. This is the one place that undoes the binding, through the two edges the graph
does have (`invoiced` to `disputed`, `disputed` to `worked`), both logged under the
same reason, so the day's history says why it came back instead of showing a dispute nobody
raised.

Imports nothing from `invoices`: `InvoiceService` calls this, and the reverse would be a
cycle.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.service import set_transition_context

DELETED_DRAFT_REASON = "la bozza di fattura è stata eliminata: giornata di nuovo da fatturare"
ANNULLED_INVOICE_REASON = "la fattura è stata annullata: giornata di nuovo da fatturare"


def count_days_billed_on(session: Session, invoice_id: UUID) -> int:
    """How many days sit on a line of `invoice_id`. `InvoiceService.replace_lines` refuses a
    draft that has any: it deletes every line and writes new ones, and the days point at the
    old ids, so they would be left `invoiced` with no line and no way back."""
    return int(
        session.execute(
            select(func.count(WorkUnit.id))
            .join(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
            .where(InvoiceLine.invoice_id == invoice_id)
        ).scalar_one()
    )


def release_days_of_invoice(session: Session, invoice_id: UUID, actor: Actor, reason: str) -> int:
    """Every day on a line of `invoice_id` that is billed and not in dispute goes back to
    `worked` with no line, and how many that was. That is a `invoiced` day, and a
    `worked` one a dispute was resolved back to without clearing its line (the day graph
    has the edge and nothing clears the link): both are on the invoice and billable again
    once it is gone. A day `paid` or still `disputed` stays where it is: money moved, or
    somebody is disputing it, and neither is this step's to undo.
    Flushes; the caller commits with the rest of its own change.

    The days are selected `FOR UPDATE` with `populate_existing`: a day a concurrent
    request is moving (binding it to another draft, a dispute) is waited for and read
    as it is after that, not as an earlier load in this session left it."""
    days = list(
        session.scalars(
            select(WorkUnit)
            .join(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
            .where(
                InvoiceLine.invoice_id == invoice_id,
                WorkUnit.status.in_(("invoiced", "worked")),
            )
            .order_by(WorkUnit.date, WorkUnit.id)
            .with_for_update(of=WorkUnit)
            .execution_options(populate_existing=True)
        )
    )
    if not days:
        return 0
    set_transition_context(session, actor, reason)
    for day in days:
        if day.status == "invoiced":
            day.status = "disputed"
    session.flush()
    for day in days:
        day.status = "worked"
        day.invoice_line_id = None
    session.flush()
    return len(days)


__all__ = [
    "ANNULLED_INVOICE_REASON",
    "DELETED_DRAFT_REASON",
    "count_days_billed_on",
    "release_days_of_invoice",
]
