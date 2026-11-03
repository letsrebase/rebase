from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from pigrocrm.core.templates.schemas import (
    TemplateCreate,
    TemplateDescription,
    TemplateListQuery,
    TemplatePage,
    TemplateRead,
    TemplateType,
    TemplateUpdate,
)
from pigrocrm.core.templates.service import TemplateService
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import KeysetParams, SearchParams

router = APIRouter(
    prefix="/api/templates",
    tags=["templates"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


class PreviewBody(BaseModel):
    variables: dict[str, Any] = {}


class PreviewResult(BaseModel):
    markdown: str


@router.post("", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def create(data: TemplateCreate, session: SessionDep, actor: ActorDep) -> TemplateRead:
    return TemplateService(session).create(data, actor)


@router.get("/{template_id}", response_model=TemplateRead)
def get(template_id: UUID, session: SessionDep, actor: ActorDep) -> TemplateRead:
    return TemplateService(session).get(template_id, actor)


@router.patch("/{template_id}", response_model=TemplateRead)
def update(
    template_id: UUID, data: TemplateUpdate, session: SessionDep, actor: ActorDep
) -> TemplateRead:
    return TemplateService(session).update(template_id, data, actor)


@router.delete("/{template_id}", response_model=TemplateRead)
def deactivate(template_id: UUID, session: SessionDep, actor: ActorDep) -> TemplateRead:
    """Deactivate, not delete: `Template` has no `deleted_at` of its own (task 6's own
    decision), and a document version still points at this template so it can be
    regenerated. `TemplateService` exposes `deactivate`/`activate`, not `archive` --
    the plan this router was drafted from predates that rename."""
    return TemplateService(session).deactivate(template_id, actor)


@router.post("/{template_id}/activate", response_model=TemplateRead)
def activate(template_id: UUID, session: SessionDep, actor: ActorDep) -> TemplateRead:
    return TemplateService(session).activate(template_id, actor)


@router.get("/{template_id}/describe", response_model=TemplateDescription)
def describe(template_id: UUID, session: SessionDep, actor: ActorDep) -> TemplateDescription:
    return TemplateService(session).describe(template_id, actor)


@router.post("/{template_id}/preview", response_model=PreviewResult)
def preview(
    template_id: UUID, body: PreviewBody, session: SessionDep, actor: ActorDep
) -> PreviewResult:
    markdown = TemplateService(session).preview(template_id, body.variables, actor)
    return PreviewResult(markdown=markdown)


# `list` must stay the last route function defined in this module for the same
# reason it must stay the last *method* in a service/repository class (see
# TemplateService.list's own comment): naming a function `list` shadows the
# builtin for the rest of this module, and any later function whose own
# annotation is a bare `list[...]` would fail at import time on Python 3.13.
# There is no such annotation left below this point, but the ordering is kept
# anyway so the rule stays mechanically obvious to the next person editing this
# file, rather than "safe by accident."
class TemplateListParams(KeysetParams, SearchParams):
    type: TemplateType | None = None
    include_inactive: bool = False


@router.get("", response_model=TemplatePage)
def list_templates(
    session: SessionDep, actor: ActorDep, params: Annotated[TemplateListParams, Query()]
) -> TemplatePage:
    return TemplateService(session).list(params.to_query(TemplateListQuery), actor)
