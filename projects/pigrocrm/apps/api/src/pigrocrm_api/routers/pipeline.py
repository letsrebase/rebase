from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.schemas import (
    PipelineStageCreate,
    PipelineStageRead,
    PipelineStageUpdate,
)
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/pipeline-stages",
    tags=["pipeline"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=PipelineStageRead, status_code=status.HTTP_201_CREATED)
def create(data: PipelineStageCreate, session: SessionDep, actor: ActorDep) -> PipelineStageRead:
    return PipelineService(session).create(data, actor)


@router.get("", response_model=list[PipelineStageRead])
def list_stages(session: SessionDep) -> list[PipelineStageRead]:
    return PipelineService(session).list()


@router.patch("/{stage_id}", response_model=PipelineStageRead)
def update(
    stage_id: UUID, data: PipelineStageUpdate, session: SessionDep, actor: ActorDep
) -> PipelineStageRead:
    return PipelineService(session).update(stage_id, data, actor)


@router.delete("/{stage_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(stage_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Refuses (409) if any deal, archived or not, still points at this stage."""
    PipelineService(session).delete(stage_id, actor)


@router.post("/seed", response_model=list[PipelineStageRead])
def seed(session: SessionDep, actor: ActorDep) -> list[PipelineStageRead]:
    """The admin check lives in `PipelineService.seed_defaults` itself now, not
    here -- see that method's docstring. This router is back to the same
    validate/resolve/call/serialize shape as every other endpoint."""
    return PipelineService(session).seed_defaults(actor)


def _admin_reads_stage_timeline(_session: Session, _stage_id: UUID, actor: Actor) -> None:
    actor.require_admin("read_pipeline_stage_timeline")


router.include_router(
    timeline_router(
        "pipeline_stage",
        id_param="stage_id",
        guard=_admin_reads_stage_timeline,
        description=(
            "Administrators only, matching who may change a stage. Deliberately still "
            "answerable after the stage is gone: `delete` is the one hard DELETE in the "
            "domain, and the entry it leaves is the only remaining record that the stage "
            "existed."
        ),
    )
)
