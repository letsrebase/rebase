"""The talent cloud (REB-519, spec § 4.2): what a company rebase admitted reads and does
from `/hub/me/cloud`. Every route stands behind `CloudDep`: signed in, with a live
grant, else 401 or 403 with «Il talent cloud non è aperto per questo account.».

The talents come by name, with the links, the CV and the anonymous card, filtered and
at most 200 (`CloudTalentService`); never the freelancer's own rate, their state or the
admin's notes. The CV has a route of its own here, behind the grant and the cloud's one
filter, never the member's own `/me/cv`.

The builder inside is the public engine with the caller's name on it: `origine`
`cloud` and the caller's user, behind the same slots of the process and the same daily
cap as the public page (`propose_in_a_slot`), so a company cannot run up what a
stranger cannot; its read keeps who each member is. No speed bump (`spend_one`): the
caller is signed in and admitted, and a company asking for six talents in a minute is
the page doing its job.

«Assumi team» and «Richiedi» file with no form, for the grant's company, the caller's
address and phone (none when they gave none), and mail rebase after the answer, the
way the public request does.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Query, Response, status

from rebase_api.deps import (
    CloudDep,
    ProposalSlotsDep,
    SenderDep,
    SessionDep,
    SettingsDep,
    TeamBuilderDep,
    TrackerDep,
)
from rebase_api.downloads import cv_response
from rebase_api.routers.team import propose_in_a_slot, send_request_mail
from rebase_core.cloud import CloudTalentService
from rebase_core.team_requests import TeamRequestService
from rebase_core.team_schemas import (
    CloudRequestCreate,
    CloudTalentList,
    CloudTalentQuery,
    TeamProposalCreate,
    TeamProposalRead,
    TeamRequestCreated,
)

router = APIRouter(prefix="/api/hub/me/cloud", tags=["hub"])

_log = logging.getLogger(__name__)


@router.get("/talents", response_model=CloudTalentList)
def list_talents(
    _: CloudDep, session: SessionDep, query: Annotated[CloudTalentQuery, Query()]
) -> CloudTalentList:
    """The cloud's talents the filters leave, vetted first then by name, at most 200
    (`capped` when there were more), with every role of the cloud for the role filter.
    422 on a seniority or a work mode the hub does not know."""
    return CloudTalentService(session).list(query)


@router.get("/talents/{freelancer_id}/cv")
def talent_cv(_: CloudDep, session: SessionDep, freelancer_id: UUID) -> Response:
    """«Apri il CV»: the stored file with its stored type; 404 «Profilo non
    disponibile.» for anyone the cloud does not show, and for a talent with no file."""
    return cv_response(CloudTalentService(session).cv(freelancer_id))


@router.post("/proposals", response_model=TeamProposalRead)
def propose_in_the_cloud(
    data: TeamProposalCreate,
    caller: CloudDep,
    session: SessionDep,
    settings: SettingsDep,
    builder: TeamBuilderDep,
    slots: ProposalSlotsDep,
) -> TeamProposalRead:
    """«Proponi il team» and «Rigenera» in the cloud: the public route's answers (503
    off, 503 busy, 502, 422), with `previous_id` a cloud proposal of the caller's own."""
    return propose_in_a_slot(
        data,
        origine="cloud",
        user_id=caller.user_id,
        session=session,
        settings=settings,
        builder=builder,
        slots=slots,
    )


@router.post("/requests", response_model=TeamRequestCreated, status_code=status.HTTP_201_CREATED)
def request_in_the_cloud(
    data: CloudRequestCreate,
    caller: CloudDep,
    session: SessionDep,
    settings: SettingsDep,
    sender: SenderDep,
    tracker: TrackerDep,
    background: BackgroundTasks,
) -> TeamRequestCreated:
    """«Assumi team» on the caller's own cloud proposal (`proposal_id`: 409 once
    requested, 422 when it is not theirs, older than a day or has nobody) or «Richiedi»
    on one card (`freelancer_id`: 404 «Profilo non disponibile.» outside the cloud)."""
    service = TeamRequestService(session, settings=settings, tracker=tracker)
    if data.proposal_id is not None:
        read, mail = service.create_in_cloud(
            data.proposal_id,
            azienda=caller.azienda,
            email=caller.email,
            telefono=caller.telefono,
            user_id=caller.user_id,
            company_id=caller.company_id,
        )
    else:
        assert data.freelancer_id is not None  # `CloudRequestCreate` holds one of the two
        read = service.create_for_talent(
            data.freelancer_id,
            azienda=caller.azienda,
            email=caller.email,
            telefono=caller.telefono,
            user_id=caller.user_id,
            company_id=caller.company_id,
        )
        mail = service.request_mail(read)
    if sender is None:
        _log.info("team request %s: no mail sender, not mailed", read.id)
    else:
        background.add_task(send_request_mail, sender, mail, read.id)
    return TeamRequestCreated(id=read.id)
