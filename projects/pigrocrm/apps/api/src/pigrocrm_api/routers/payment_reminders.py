"""Which invoices are worth chasing, and preparing the letter that chases one.

Two endpoints, and the second one **sends nothing** (spec 8.3). `POST ""` writes a
`payment_reminders` row and a draft, and hands back the draft's id; the draft leaves
through `/api/email-drafts/{id}/send` like any other email. That is what lets spec 7.3
rely on 6.1's idempotence instead of reimplementing it -- one send path in the whole
slice -- and a second one here is exactly where the double send would come back.

`GET /candidates` is a plain read of this installation's own register: it makes no Google
call, spends no Gmail quota and works with the mailbox disconnected (the reply signal
degrades to `null` rather than to "nobody replied"). It is therefore not role-gated --
seeing which invoices are late is reading your own books. What is gated is preparing the
reminder, because that writes a demand for payment in the owner's name.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.gmail.schemas import (
    PaymentReminderCreate,
    PaymentReminderRead,
    SollecitiPage,
)
from pigrocrm.core.gmail.solleciti import SollecitiService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/payment-reminders",
    tags=["payment-reminders"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


def _solleciti(session: Session, settings: Settings) -> SollecitiService:
    return SollecitiService(session, settings=settings)


@router.get("/candidates", response_model=SollecitiPage)
def list_candidates(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> SollecitiPage:
    """Worst first, repliers last. Not paginated and deliberately so: the whole point of
    the list is that a person looks at all of it and decides, and a page boundary in the
    middle of "who owes me money" would hide the tail of it behind a control nobody
    presses. `total` is returned anyway, because the page renders a count."""
    items = _solleciti(session, settings).candidates(actor)
    return SollecitiPage(items=items, total=len(items))


@router.post("", response_model=PaymentReminderRead, status_code=201)
def create_reminder(
    payload: PaymentReminderCreate, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> PaymentReminderRead:
    """Creates the reminder row and its draft, and **sends nothing**.

    The body carries one field and can carry no other (`extra="forbid"`): everything the
    letter says is derived from the register, and a field that let a caller override the
    amount would produce a demand naming a figure the client's own copy of the invoice
    does not carry -- a demand they would be right to ignore.
    """
    return _solleciti(session, settings).create_reminder(payload.invoice_id, actor)
