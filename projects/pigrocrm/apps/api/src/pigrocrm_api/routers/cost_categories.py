from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pigrocrm.core.timetracking.categories import CostCategoryService
from pigrocrm.core.timetracking.schemas import (
    CostCategoryCreate,
    CostCategoryRead,
    CostCategoryUpdate,
)
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/cost-categories",
    tags=["cost-categories"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("costs")],
)


@router.get("", response_model=list[CostCategoryRead])
def list_cost_categories(
    session: SessionDep,
    include_archived: Annotated[bool, Query()] = False,
) -> list[CostCategoryRead]:
    return CostCategoryService(session).list_cost_categories(include_archived=include_archived)


@router.post("", response_model=CostCategoryRead, status_code=status.HTTP_201_CREATED)
def create(data: CostCategoryCreate, session: SessionDep, actor: ActorDep) -> CostCategoryRead:
    return CostCategoryService(session).create_cost_category(data, actor)


@router.post("/seed", response_model=list[CostCategoryRead])
def seed(session: SessionDep, actor: ActorDep) -> list[CostCategoryRead]:
    """Idempotent: returns only what it actually created, so a second call answers
    `[]` rather than duplicating the taxonomy."""
    return CostCategoryService(session).seed_defaults(actor)


@router.patch("/{category_id}", response_model=CostCategoryRead)
def update(
    category_id: UUID, data: CostCategoryUpdate, session: SessionDep, actor: ActorDep
) -> CostCategoryRead:
    return CostCategoryService(session).update_cost_category(category_id, data, actor)


@router.post("/{category_id}/archive", response_model=CostCategoryRead)
def archive(category_id: UUID, session: SessionDep, actor: ActorDep) -> CostCategoryRead:
    """Archive, never delete: a deleted category with costs still attached leaves orphan
    rows nobody can see. There is deliberately no DELETE verb on this resource."""
    return CostCategoryService(session).archive_cost_category(category_id, actor)


@router.post("/{category_id}/unarchive", response_model=CostCategoryRead)
def unarchive(category_id: UUID, session: SessionDep, actor: ActorDep) -> CostCategoryRead:
    return CostCategoryService(session).unarchive_cost_category(category_id, actor)
