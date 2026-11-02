from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.schema_registry import describe_entity
from pigrocrm_api.deps import SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/schema",
    tags=["schema"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


class EntitySchema(BaseModel):
    entity_type: str
    native_fields: list[str]
    custom_fields: list[dict[str, Any]]


@router.get("/{entity_type}", response_model=EntitySchema)
def describe(entity_type: EntityType, session: SessionDep) -> EntitySchema:
    """The same information the MCP `describe_schema` tool returns — literally the same
    function — so the UI and an agent can never disagree about what fields exist."""
    return EntitySchema(**describe_entity(session, entity_type))
