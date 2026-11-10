"""The talent cloud (REB-519, spec § 4.2): what a company rebase admitted reads and does
from `/hub/me/cloud`. Every route stands behind `CloudDep`: signed in, with a live
grant, else 401 or 403 with «Il talent cloud non è aperto per questo account.».

The talents come by name, with the links, the CV and the anonymous card, filtered and
at most 200 (`CloudTalentService`); never the freelancer's own rate, their state or the
admin's notes. The CV has a route of its own here, behind the grant and the cloud's one
filter, never the member's own `/me/cv`.

The builder inside is the public engine with the caller's name on it: `origin`
`cloud` and the caller's user, behind the same slots of the process and the same daily
cap as the public page (`rebase_core.team_slots`), so a company cannot run up what a
stranger cannot; its read keeps who each member is. The public page's per-client share
does not apply: the caller is admitted and signed in. No speed bump (`spend_one`): the
caller is signed in and admitted, and a company asking for six talents in a minute is
the page doing its job.

«Assumi team» and «Richiedi» file with no form, for the grant's company, the caller's
address and phone (none when they gave none), and mail rebase after the answer, the
way the public request does.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status

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
from rebase_core.cloud import CloudTalentService
from rebase_core.mail import send_after_response
from rebase_core.team_requests import TeamRequestService
from rebase_core.team_schemas import (
    CLOUD_REQUEST_ONE_OF,
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
    builder: TeamBuilderDep,
    slots: ProposalSlotsDep,
) -> TeamProposalRead:
    """«Proponi il team» and «Rigenera» in the cloud: the public route's answers (503
    off, 503 busy, 502, 422), with `previous_id` a cloud proposal of the caller's own."""
    return slots.propose(
        builder, data, origin="cloud", user_id=caller.user_id, company_id=caller.company_id
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
    requested, 422 when it is not theirs, older than a day or has nobody, and 422 «Il
    talent cloud con cui hai fatto questa proposta non è più aperto per te…» when the
    caller holds no live grant for the company the proposal was made under, which is
    the company it is filed for; a proposal from before the company was kept falls back
    to «La proposta è di un altro contesto: rigenerala.» when it is older than the
    caller's newest live grant) or «Richiedi» on one card
    (`freelancer_id`: 404 «Profilo non disponibile.» outside the cloud)."""
    service = TeamRequestService(session, settings=settings, tracker=tracker)
    if data.proposal_id is not None:
        read, mail = service.create_in_cloud(data.proposal_id, caller=caller)
    elif data.freelancer_id is not None:
        read = service.create_for_talent(
            data.freelancer_id,
            company=caller.company,
            email=caller.email,
            phone=caller.phone,
            user_id=caller.user_id,
            company_id=caller.company_id,
        )
        mail = service.request_mail(read)
    else:  # `CloudRequestCreate` refuses this before the route runs
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, CLOUD_REQUEST_ONE_OF)
    if sender is None:
        _log.info("team request %s: no mail sender, not mailed", read.id)
    else:
        background.add_task(send_after_response, sender, mail, f"team request {read.id}")
    return TeamRequestCreated(id=read.id)
