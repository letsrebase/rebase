from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.schemas import (
    CostCreate,
    CostListQuery,
    CostPage,
    CostRead,
    CostUpdate,
)
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import CUSTOM_QUERY_DESCRIPTION, parse_custom_filter

router = APIRouter(prefix="/api/costs", tags=["costs"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=CostRead, status_code=status.HTTP_201_CREATED)
def create(data: CostCreate, session: SessionDep, actor: ActorDep) -> CostRead:
    return CostService(session).create(data, actor)


@router.get("", response_model=CostPage)
def list_costs(
    session: SessionDep,
    actor: ActorDep,
    deal_id: Annotated[UUID | None, Query()] = None,
    azienda_id: Annotated[
        UUID | None,
        Query(description="Solo i costi di questa azienda; omesso, tutti, i generali compresi"),
    ] = None,
    solo_generali: Annotated[
        bool, Query(description="Solo spese generali, cioè senza deal (§7.4)")
    ] = False,
    category_id: Annotated[UUID | None, Query()] = None,
    da: Annotated[date | None, Query()] = None,
    a: Annotated[date | None, Query()] = None,
    custom: Annotated[list[SafeStr] | None, Query(description=CUSTOM_QUERY_DESCRIPTION)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> CostPage:
    query = CostListQuery(
        deal_id=deal_id,
        azienda_id=azienda_id,
        solo_generali=solo_generali,
        category_id=category_id,
        da=da,
        a=a,
        custom=parse_custom_filter(custom),
        limit=limit,
        cursor=cursor,
    )
    return CostService(session).list(query, actor)


@router.get("/{cost_id}", response_model=CostRead)
def get(cost_id: UUID, session: SessionDep, actor: ActorDep) -> CostRead:
    return CostService(session).get(cost_id, actor)


@router.patch("/{cost_id}", response_model=CostRead)
def update(cost_id: UUID, data: CostUpdate, session: SessionDep, actor: ActorDep) -> CostRead:
    return CostService(session).update(cost_id, data, actor)


@router.delete("/{cost_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(cost_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    CostService(session).soft_delete(cost_id, actor)


@router.post("/{cost_id}/restore", response_model=CostRead)
def restore(cost_id: UUID, session: SessionDep, actor: ActorDep) -> CostRead:
    return CostService(session).restore(cost_id, actor)
