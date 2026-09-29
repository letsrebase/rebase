"""The team builder's public routes (REB-512, spec § 3.2): a visitor describes a project
and gets an anonymous team, then files «Assumi team» with a company name, an email and a
phone. No login, like the wizards, and behind the same speed bump (`spend_one`): the
proposal because each one costs a call to Claude, the request because each one mails
rebase.

A proposal also waits for a slot of the process (`ProposalSlotsDep`, spec § 5): Claude
holds a worker thread for seconds to tens of seconds, on the pool the member area and
the webhooks share, so the one past `REBASE_TEAM_BUILDER_CONCURRENCY` answers 503 at
once; and for room in the day (`team_caps.require_daily_room`). Both answer the same
«Troppe richieste» sentence. What the public read carries is C4's (`TeamBuilder`): no
freelancer id and no place of a card.

The request's mail to rebase leaves after the answer, as a background task, the way the
magic link does: the request is committed by then, so a slow provider does not hold the
201 and a failing one does not turn it into a 500.

A talent's answer to the availability mail (REB-517, spec § 3.2) is a post from the
hub's page, never the mail's link itself, which a scanner may fetch: there is no GET
here. The token is the only guard, 256 random bits and one answer, and the route sits
behind the same speed bump as the wizards all the same.
"""

import logging
import threading
from datetime import datetime
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request, status
from sqlalchemy.orm import Session

from rebase_api.deps import (
    ProposalSlotsDep,
    SenderDep,
    SessionDep,
    SettingsDep,
    TeamBuilderDep,
    TrackerDep,
)
from rebase_api.ratelimit import spend_one
from rebase_core.config import Settings
from rebase_core.errors import TeamBuilderBusy, TeamBuilderOff
from rebase_core.mail import EmailSender, Mail
from rebase_core.team_builder import OFF_SENTENCE, TeamBuilder
from rebase_core.team_caps import BUSY_SENTENCE, require_daily_room
from rebase_core.team_requests import TeamRequestService
from rebase_core.team_schemas import (
    TeamAvailabilityAnswer,
    TeamAvailabilityOutcome,
    TeamProposalCreate,
    TeamProposalRead,
    TeamRequestCreate,
    TeamRequestCreated,
)

router = APIRouter(prefix="/api/hub/team", tags=["hub"])

_log = logging.getLogger(__name__)

# How many refused asks may be written at once, how many more may wait for a turn,
# and for how long (PR #495): a refusal holds no proposal slot, so under a flood from
# many clients the rows would be the one thing left unbounded. Two writes at a time
# serve hundreds of refusals a second, since each is one insert; a few waiters keep a
# burst from losing a row; and the two ceilings together keep a flood from holding
# more than ten of the worker threads the member area and the webhooks share. Past
# them, the refusal is answered, the row is not written, and the log says so: a
# metric lost under a flood is cheaper than a pool held by the flood.
REFUSAL_WRITES = 2
REFUSAL_WAITERS = 8
REFUSAL_WAIT_SECONDS = 0.5
_refusal_slots = threading.BoundedSemaphore(REFUSAL_WRITES)
_refusal_waiters = threading.BoundedSemaphore(REFUSAL_WAITERS)


def send_request_mail(sender: EmailSender, mail: Mail, request_id: UUID) -> None:
    """Runs after the response: a refusal is logged by the request's id alone, never
    the address, the company or the summary. The cloud's requests (REB-519) send theirs
    through it too."""
    if not sender.send(mail):
        _log.warning("team request %s: the provider refused the mail", request_id)


def propose_in_a_slot(
    data: TeamProposalCreate,
    *,
    origine: str,
    user_id: UUID | None,
    session: Session,
    settings: Settings,
    builder: TeamBuilder,
    slots: threading.BoundedSemaphore,
) -> TeamProposalRead:
    """A proposal behind the switch, a slot of the process and the day's room, in that
    order: the public page's and the cloud's (REB-519), which share the one semaphore
    and the one daily cap (spec § 5). The builder's clock (`get_clock`) is read once:
    the cap counts the day of that instant and the new row is stamped with it, so a call
    to Claude that ends past midnight in Rome is not counted on one day and written on
    the next (REB-581)."""
    # Before the caps: an environment with the builder off says so, not «Troppe
    # richieste», whatever the day's count.
    if not settings.team_builder_enabled or builder.llm is None:
        raise TeamBuilderOff(OFF_SENTENCE)
    now = builder.now()
    if not slots.acquire(blocking=False):
        _log.info("team builder: every proposal slot is taken")
        _refuse(data, TeamBuilderBusy(BUSY_SENTENCE), origine, user_id, builder, now)
    # One release site for the slot, whatever the count or the call raises: a query that
    # fails here with the slot still held would, four times over, close the builder
    # until the process restarts.
    try:
        try:
            require_daily_room(session, settings, now=now)
        except TeamBuilderBusy as busy:
            refused = busy
        else:
            return builder.propose(data, origine=origine, user_id=user_id, now=now)
    finally:
        slots.release()
    _refuse(data, refused, origine, user_id, builder, now)


def _refuse(
    data: TeamProposalCreate,
    busy: TeamBuilderBusy,
    origine: str,
    user_id: UUID | None,
    builder: TeamBuilder,
    now: datetime,
) -> NoReturn:
    """A «Troppe richieste» is kept as an attempt row before it is answered (0028):
    what was asked is a measure of use whether the hub had room for it or not. The
    write holds no proposal slot, only one of `REFUSAL_WRITES`, waited for up to
    `REFUSAL_WAIT_SECONDS` by at most `REFUSAL_WAITERS` at once; past either the row
    is skipped, and a database that refuses it is logged. Neither is ever a 500 in
    place of the 503 the visitor gets either way."""
    if not _refusal_waiters.acquire(blocking=False):
        _log.warning("team builder: refused ask not kept, every refusal write is busy")
        raise busy
    try:
        if not _refusal_slots.acquire(timeout=REFUSAL_WAIT_SECONDS):
            _log.warning("team builder: refused ask not kept, every refusal write is busy")
            raise busy
        try:
            builder.record_refusal(data, origine=origine, user_id=user_id, error=busy, now=now)
        except Exception:
            _log.exception("team builder: the refused ask was not kept")
        finally:
            _refusal_slots.release()
    finally:
        _refusal_waiters.release()
    raise busy


@router.post("/proposals", response_model=TeamProposalRead)
def propose_team(
    data: TeamProposalCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    builder: TeamBuilderDep,
    slots: ProposalSlotsDep,
) -> TeamProposalRead:
    """«Proponi il team» and «Rigenera» (`previous_id`, `nota`). 503 «Il team builder è
    spento.» without a key or with the switch off, 503 «Troppe richieste…» with every
    slot taken or the day's proposals spent, 502 when Claude does not answer."""
    spend_one(request)
    return propose_in_a_slot(
        data,
        origine="pubblico",
        user_id=None,
        session=session,
        settings=settings,
        builder=builder,
        slots=slots,
    )


@router.post("/requests", response_model=TeamRequestCreated, status_code=status.HTTP_201_CREATED)
def request_team(
    data: TeamRequestCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    sender: SenderDep,
    tracker: TrackerDep,
    background: BackgroundTasks,
) -> TeamRequestCreated:
    """«Invia la richiesta»: 201 with the request's id; 409 on a proposal already
    requested, a second click included; 422 on a proposal older than a day, not a
    public one, or with nobody in it. Without a mail key the request is filed all the
    same and waits in «Richieste team»."""
    spend_one(request)
    read, mail = TeamRequestService(session, settings=settings, tracker=tracker).create(
        data, origine="pubblico", user_id=None, company_id=None
    )
    if sender is None:
        _log.info("team request %s: no mail sender, not mailed", read.id)
    else:
        background.add_task(send_request_mail, sender, mail, read.id)
    return TeamRequestCreated(id=read.id)


@router.post("/availability", response_model=TeamAvailabilityOutcome)
def answer_availability(
    data: TeamAvailabilityAnswer,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    tracker: TrackerDep,
) -> TeamAvailabilityOutcome:
    """«Conferma» on `/hub/team/risposta`: `{esito: "si" | "no"}` once recorded, and
    `{esito: "invalid"}` for a token unknown, spent or expired, never saying which."""
    spend_one(request)
    esito = TeamRequestService(session, settings=settings, tracker=tracker).answer(
        data.t, data.risposta
    )
    return TeamAvailabilityOutcome(esito=esito)
