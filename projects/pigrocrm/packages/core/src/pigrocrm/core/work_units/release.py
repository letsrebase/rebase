"""Giving a day back when the invoice it was billed on stops existing (REB-751).

Binding a day to a draft moves it to `fatturato` (`invoicing.py`). The state graph has no
edge from there back to `lavorato`, and a day left in `fatturato` on a deleted draft or an
annulled invoice could never be billed again: it would read «fatturato» with nothing
behind it. This is the one place that undoes the binding, through the two edges the graph
does have (`fatturato` to `contestato`, `contestato` to `lavorato`), both logged under the
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
    old ids, so they would be left `fatturato` with no line and no way back."""
    return int(
        session.execute(
            select(func.count(WorkUnit.id))
            .join(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
            .where(InvoiceLine.invoice_id == invoice_id)
        ).scalar_one()
    )


def release_days_of_invoice(session: Session, invoice_id: UUID, actor: Actor, motivo: str) -> int:
    """Every day on a line of `invoice_id` that is billed and not in dispute goes back to
    `lavorato` with no line, and how many that was. That is a `fatturato` day, and a
    `lavorato` one a dispute was resolved back to without clearing its line (the day graph
    has the edge and nothing clears the link): both are on the invoice and billable again
    once it is gone. A day `pagato` or still `contestato` stays where it is: money moved, or
    somebody is disputing it, and neither is this step's to undo.
    Flushes; the caller commits with the rest of its own change."""
    days = list(
        session.scalars(
            select(WorkUnit)
            .join(InvoiceLine, InvoiceLine.id == WorkUnit.invoice_line_id)
            .where(
                InvoiceLine.invoice_id == invoice_id,
                WorkUnit.stato.in_(("fatturato", "lavorato")),
            )
            .order_by(WorkUnit.data, WorkUnit.id)
        )
    )
    if not days:
        return 0
    set_transition_context(session, actor, motivo)
    for day in days:
        if day.stato == "fatturato":
            day.stato = "contestato"
    session.flush()
    for day in days:
        day.stato = "lavorato"
        day.invoice_line_id = None
    session.flush()
    return len(days)


__all__ = [
    "ANNULLED_INVOICE_REASON",
    "DELETED_DRAFT_REASON",
    "count_days_billed_on",
    "release_days_of_invoice",
]
