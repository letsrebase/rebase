"""«Richieste team» (REB-512, spec § 3.5): the team requests the public page and the
cloud file, as the admin reads and works them, behind the admin cookie. A router of its
own beside `admin.py`, under the same `/api/hub/team/requests` path the public route
posts to, answering only other methods and the paths below it.

«Contatta i talenti» is D1's (`/contact`); everything here reads or edits what an
admin owns: the state, their note, and the summary the talents will read.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from rebase_api.deps import AdminDep, SessionDep, SettingsDep
from rebase_core.pagination import CURSOR_MAX_LENGTH
from rebase_core.team_requests import LIST_LIMIT_DEFAULT, LIST_LIMIT_MAX, TeamRequestService
from rebase_core.team_schemas import (
    TeamRequestList,
    TeamRequestNote,
    TeamRequestRead,
    TeamRequestStatus,
    TeamRequestSummary,
)

router = APIRouter(prefix="/api/hub/team", tags=["hub-admin"])

Limit = Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)]
Word = Annotated[str | None, Query(max_length=20)]
Cursor = Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)]


@router.get("/requests", response_model=TeamRequestList)
def list_team_requests(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    stato: Word = None,
    origine: Word = None,
    limit: Limit = LIST_LIMIT_DEFAULT,
    cursor: Cursor = None,
) -> TeamRequestList:
    """Newest first, by cursor; `stato` and `origine` filter, and a word that is not one
    of theirs is a 422 naming the field."""
    return TeamRequestService(session, settings=settings).list_recent(
        stato=stato, origine=origine, limit=limit, cursor=cursor
    )


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
        request_id, change.stato, admin.id
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
        request_id, change.riassunto, admin.id
    )
