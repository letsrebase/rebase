from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.proposals.schemas import (
    ProposalAccept,
    ProposalCreate,
    ProposalListQuery,
    ProposalPage,
    ProposalRead,
    ProposalReject,
    ProposalStato,
    ProposalTargetType,
)
from pigrocrm.core.proposals.service import ProposalService
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/proposals", tags=["proposals"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=ProposalRead, status_code=status.HTTP_201_CREATED)
def create(data: ProposalCreate, session: SessionDep, actor: ActorDep) -> ProposalRead:
    return ProposalService(session).create(data, actor)


@router.get("", response_model=ProposalPage)
def list_proposals(
    session: SessionDep,
    actor: ActorDep,
    stato: ProposalStato | None = None,
    target_type: ProposalTargetType | None = None,
    document_id: UUID | None = None,
    contract_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ProposalPage:
    query = ProposalListQuery(
        stato=stato,
        target_type=target_type,
        document_id=document_id,
        contract_id=contract_id,
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
    named, in one transaction with the proposal's own `stato` flip."""
    return ProposalService(session).accept(proposal_id, data, actor)


@router.post("/{proposal_id}/reject", response_model=ProposalRead)
def reject(
    proposal_id: UUID, data: ProposalReject, session: SessionDep, actor: ActorDep
) -> ProposalRead:
    return ProposalService(session).reject(proposal_id, data, actor)
