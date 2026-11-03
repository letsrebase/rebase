from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.schemas import (
    CostCreate,
    CostListQuery,
    CostPage,
    CostRead,
    CostUpdate,
)
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import CustomParams, KeysetParams

router = APIRouter(
    prefix="/api/costs",
    tags=["costs"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=CostRead, status_code=status.HTTP_201_CREATED)
def create(data: CostCreate, session: SessionDep, actor: ActorDep) -> CostRead:
    return CostService(session).create(data, actor)


class CostListParams(KeysetParams, CustomParams):
    deal_id: UUID | None = None
    azienda_id: UUID | None = Field(
        default=None,
        description="Solo i costi di questa azienda; omesso, tutti, i generali compresi",
    )
    solo_generali: bool = Field(
        default=False, description="Solo spese generali, cioè senza deal (§7.4)"
    )
    category_id: UUID | None = None
    da: date | None = None
    a: date | None = None


@router.get("", response_model=CostPage)
def list_costs(
    session: SessionDep, actor: ActorDep, params: Annotated[CostListParams, Query()]
) -> CostPage:
    return CostService(session).list(params.to_query(CostListQuery), actor)


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
