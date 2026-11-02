"""The `GET /{id}/timeline` route of an entity, written once.

Ten routers carried the same handler: read the activity rows of one entity, newest first,
bounded by `limit`. They differ in the name of the id segment, in who may read (an
administrator, or the owner of the account) and in whether an unknown id is a 404 or an
empty list -- so those three are this factory's arguments and nothing else is.

An unknown id is an empty list by default, and a 404 only where the caller passes a
`guard` that reads the entity. That is deliberate: a pipeline stage is hard-deleted and its
timeline is the only record that it existed, and a soft-deleted deal or customer keeps a
history somebody opens to decide whether to restore it, so a read-first on every route
would turn those answers into a 404.
"""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query
from sqlalchemy.orm import Session

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.query_params import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX

# Runs before the read and raises to refuse: `(session, entity_id, actor)`.
TimelineGuard = Callable[[Session, UUID, Actor], None]


def timeline_router(
    entity: str,
    *,
    id_param: str,
    guard: TimelineGuard | None = None,
    description: str | None = None,
) -> APIRouter:
    """A router with the one route `/{<id_param>}/timeline`, to be included in the entity's
    own router. `entity` is the `entity_type` the activities were recorded under."""
    router = APIRouter()

    @router.get(
        "/{" + id_param + "}/timeline", response_model=list[ActivityRead], description=description
    )
    def timeline(
        entity_id: Annotated[UUID, Path(alias=id_param)],
        session: SessionDep,
        actor: ActorDep,
        limit: Annotated[int, Query(ge=1, le=PAGE_LIMIT_MAX)] = PAGE_LIMIT_DEFAULT,
    ) -> list[ActivityRead]:
        if guard is not None:
            guard(session, entity_id, actor)
        return ActivityService(session).timeline(entity, entity_id, limit)

    return router
