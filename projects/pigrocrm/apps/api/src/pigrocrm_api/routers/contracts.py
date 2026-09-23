from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.contracts.schemas import (
    ContractCreate,
    ContractListQuery,
    ContractPage,
    ContractRead,
    RateCardCreate,
    RateCardRead,
)
from pigrocrm.core.contracts.service import ContractService, RateCardService
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/contracts", tags=["contracts"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=ContractRead, status_code=status.HTTP_201_CREATED)
def create(data: ContractCreate, session: SessionDep, actor: ActorDep) -> ContractRead:
    return ContractService(session).create(data, actor)


@router.get("", response_model=ContractPage)
def list_contracts(
    session: SessionDep,
    actor: ActorDep,
    customer_id: Annotated[UUID | None, Query()] = None,
    stato: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)] = None,
    sort: Annotated[SafeStr | None, Query(description="created_at | updated_at | titolo")] = None,
    dir: Annotated[SortDirection, Query()] = "asc",
) -> ContractPage:
    query = ContractListQuery(
        customer_id=customer_id,
        stato=stato,
        limit=limit,
        cursor=cursor,
        sort=sort,
        dir=dir,
    )
    return ContractService(session).list(query, actor)


@router.get("/{contract_id}", response_model=ContractRead)
def get(contract_id: UUID, session: SessionDep, actor: ActorDep) -> ContractRead:
    return ContractService(session).get(contract_id, actor)


@router.post(
    "/{contract_id}/rate-cards",
    response_model=RateCardRead,
    status_code=status.HTTP_201_CREATED,
)
def create_rate_card(
    contract_id: UUID, data: RateCardCreate, session: SessionDep, actor: ActorDep
) -> RateCardRead:
    return RateCardService(session).create(contract_id, data, actor)


@router.get("/{contract_id}/rate-cards", response_model=list[RateCardRead])
def list_rate_cards(contract_id: UUID, session: SessionDep, actor: ActorDep) -> list[RateCardRead]:
    return RateCardService(session).list_for_contract(contract_id, actor)
