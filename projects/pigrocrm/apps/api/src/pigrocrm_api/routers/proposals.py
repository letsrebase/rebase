from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.proposals.schemas import (
    ProposalAccept,
    ProposalCreate,
    ProposalListQuery,
    ProposalOrder,
    ProposalPage,
    ProposalRead,
    ProposalReject,
    ProposalStatus,
    ProposalTargetType,
)
from pigrocrm.core.proposals.service import ProposalService
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/proposals",
    tags=["proposals"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=ProposalRead, status_code=status.HTTP_201_CREATED)
def create(data: ProposalCreate, session: SessionDep, actor: ActorDep) -> ProposalRead:
    return ProposalService(session).create(data, actor)


@router.get("", response_model=ProposalPage)
def list_proposals(
    session: SessionDep,
    actor: ActorDep,
    status: ProposalStatus | None = None,
    target_type: ProposalTargetType | None = None,
    document_id: UUID | None = None,
    contract_id: UUID | None = None,
    order: ProposalOrder = "arrival",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ProposalPage:
    query = ProposalListQuery(
        status=status,
        target_type=target_type,
        document_id=document_id,
        contract_id=contract_id,
        order=order,
        limit=limit,
    )
    return ProposalService(session).list(query, actor)


@router.get("/{proposal_id}", response_model=ProposalRead)
def get(proposal_id: UUID, session: SessionDep, actor: ActorDep) -> ProposalRead:
    return ProposalService(session).get(proposal_id, actor)


@router.post("/{proposal_id}/accept", response_model=ProposalRead)
def accept(
    proposal_id: UUID, data: ProposalAccept, session: SessionDep, actor: ActorDep
) -> ProposalRead:
    """The confirm half of "agents propose, humans confirm": creates the
    `contracts`+`rate_cards` pair or the `approvals`+`work_units` pair the proposal
    named, in one transaction with the proposal's own `status` flip."""
    return ProposalService(session).accept(proposal_id, data, actor)


@router.post("/{proposal_id}/reject", response_model=ProposalRead)
def reject(
    proposal_id: UUID, data: ProposalReject, session: SessionDep, actor: ActorDep
) -> ProposalRead:
    return ProposalService(session).reject(proposal_id, data, actor)


def _proposal_exists(session: Session, proposal_id: UUID, actor: Actor) -> None:
    # A rejection's `reason` has no column on `proposals` (see `ProposalReject`). The
    # proposal is read first so an id that is not one answers 404, not an empty list.
    ProposalService(session).get(proposal_id, actor)


router.include_router(
    timeline_router(
        "proposal",
        id_param="proposal_id",
        guard=_proposal_exists,
        description=(
            "The proposal's timeline: filed, then accepted or rejected. The reason for a "
            "rejection lives only here, in the payload of the `rejected` row."
        ),
    )
)
