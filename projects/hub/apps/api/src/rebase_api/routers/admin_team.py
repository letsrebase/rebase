"""«Richieste team» (REB-512, spec § 3.5): the team requests the public page and the
cloud file, as the admin reads and works them, behind the admin cookie. A router of its
own beside `admin.py`, under the same `/api/hub/team/requests` path the public route
posts to, answering only other methods and the paths below it.

Everything here reads or edits what an admin owns: the state, their note, and the
summary the talents will read; and «Contatta i talenti» (REB-517, `/contact`), whose
mails leave after the answer, in a session of their own, so a slow provider never holds
the admin's page and the answer already shows every talent as contacted.

«Proposte» (`GET /proposals`, 0028) is the other list: every «Proponi il team» the
public page, the cloud and an admin asked, answered or not, with the request filed on
it when there is one. The public route posts to the same path; this one only reads it.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from rebase_api.deps import (
    AdminDep,
    SenderDep,
    SessionDep,
    SessionOpenerDep,
    SettingsDep,
    TeamBuilderDep,
)
from rebase_api.params import Cursor, Limit
from rebase_core.config import Settings
from rebase_core.db import SessionOpener
from rebase_core.mail import EmailSender
from rebase_core.team_availability import NO_SENDER, AvailabilityBatch, TeamAvailabilityService
from rebase_core.team_builder import (
    LIST_LIMIT_DEFAULT as PROPOSALS_LIMIT_DEFAULT,
)
from rebase_core.team_requests import LIST_LIMIT_DEFAULT, TeamRequestService
from rebase_core.team_schemas import (
    TeamProposalList,
    TeamRequestList,
    TeamRequestNote,
    TeamRequestRead,
    TeamRequestStatus,
    TeamRequestSummary,
)

router = APIRouter(prefix="/api/hub/team", tags=["hub-admin"])

_log = logging.getLogger(__name__)

Word = Annotated[str | None, Query(max_length=20)]


@router.get("/requests", response_model=TeamRequestList)
def list_team_requests(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    status: Word = None,
    origin: Word = None,
    limit: Limit = LIST_LIMIT_DEFAULT,
    cursor: Cursor = None,
) -> TeamRequestList:
    """Newest first, by cursor; `status` and `origin` filter, and a word that is not one
    of theirs is a 422 naming the field."""
    return TeamRequestService(session, settings=settings).list_recent(
        status=status, origin=origin, limit=limit, cursor=cursor
    )


@router.get("/proposals", response_model=TeamProposalList)
def list_team_proposals(
    _: AdminDep,
    builder: TeamBuilderDep,
    origin: Word = None,
    outcome: Word = None,
    limit: Limit = PROPOSALS_LIMIT_DEFAULT,
    cursor: Cursor = None,
) -> TeamProposalList:
    """«Proposte» (0028): every ask, newest first, by cursor; `origin` (`public`,
    `cloud`, `admin`) and `outcome` (`ok`, `error`) filter, and a word that is not one of
    theirs is a 422 naming the field. The builder needs no key to read."""
    return builder.list_recent(origin=origin, outcome=outcome, limit=limit, cursor=cursor)


@router.get("/requests/{request_id}", response_model=TeamRequestRead)
def get_team_request(
    _: AdminDep, session: SessionDep, settings: SettingsDep, request_id: UUID
) -> TeamRequestRead:
    """The request's page: the proposal with the talents' ids, the talents by name with
    their own rates, the company's contacts, the state and the note."""
    return TeamRequestService(session, settings=settings).get(request_id)


@router.post("/requests/{request_id}/status", response_model=TeamRequestRead)
def set_team_request_status(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    request_id: UUID,
    change: TeamRequestStatus,
) -> TeamRequestRead:
    """«Segna come contattata», «Chiudi»; recorded as the admin's."""
    return TeamRequestService(session, settings=settings).set_status(
        request_id, change.status, admin.id
    )


@router.patch("/requests/{request_id}/note", response_model=TeamRequestRead)
def set_team_request_note(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    request_id: UUID,
    change: TeamRequestNote,
) -> TeamRequestRead:
    """«Salva la nota»; `null` or only spaces clears it."""
    return TeamRequestService(session, settings=settings).set_note(
        request_id, change.note, admin.id
    )


@router.patch("/requests/{request_id}/summary", response_model=TeamRequestRead)
def set_team_request_summary(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    request_id: UUID,
    change: TeamRequestSummary,
) -> TeamRequestRead:
    """«Salva il riassunto»: the proposal's summary, what the talents will read."""
    return TeamRequestService(session, settings=settings).set_summary(
        request_id, change.summary, admin.id
    )


def _deliver(
    open_session: SessionOpener,
    settings: Settings,
    sender: EmailSender,
    batch: AvailabilityBatch,
) -> None:
    """After the response: a batch of mails run in a session of its own, not one mail
    through `send_after_response`. It never raises, since nobody is left to read it, and
    logs the request's id alone."""
    try:
        with open_session() as session:
            TeamAvailabilityService(session, settings=settings, sender=sender).deliver(batch)
    except Exception:
        # A database error mid-batch leaves talents marked as mailed: «Rimanda» mails them too.
        _log.exception("team request %s: sending the availability mails failed", batch.request_id)


@router.post("/requests/{request_id}/contact", response_model=TeamRequestRead)
def contact_team_talents(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    sender: SenderDep,
    open_session: SessionOpenerDep,
    background: BackgroundTasks,
    request_id: UUID,
    only_silent: bool = False,
) -> TeamRequestRead:
    """«Contatta i talenti», and with `only_silent` «Rimanda a chi non ha risposto»: a
    mail to each talent with the two answers, sent after this answer. 409 with the
    sentence for a closed request, a summary that names the company, a second first
    time, or nobody left to write to; 503 without a mail key."""
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_SENDER)
    read, batch = TeamAvailabilityService(session, settings=settings).prepare_contact(
        request_id, admin.id, only_silent=only_silent
    )
    background.add_task(_deliver, open_session, settings, sender, batch)
    return read
