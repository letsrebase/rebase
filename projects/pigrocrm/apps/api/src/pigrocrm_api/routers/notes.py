from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pigrocrm.core.modules import require_modules
from pigrocrm.core.notes.schemas import (
    NoteCreate,
    NoteEntityType,
    NoteRead,
    NoteRevisionRead,
    NoteUpdate,
)
from pigrocrm.core.notes.service import NoteService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import PAGE_LIMIT_MAX

router = APIRouter(
    prefix="/api",
    tags=["notes"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)

# The collection a note hangs on, as the URL spells it, and the entity type the service
# and the timeline know it by.
Collection = Literal["deals", "customers", "people"]
ENTITY_TYPES: dict[str, NoteEntityType] = {
    "deals": "deal",
    "customers": "customer",
    "people": "person",
}


def _collection_module(collection: Collection, settings: SettingsDep) -> None:
    """A deal's thread belongs to `deals` (spec 2026-11-09, A2): with the module off its
    notes answer 404 like the rest of the deal. The customer's and the person's are the core's."""
    if collection == "deals":
        require_modules(settings, "deals")


@router.get(
    "/{collection}/{entity_id}/notes",
    response_model=list[NoteRead],
    dependencies=[Depends(_collection_module)],
)
def list_notes(
    collection: Collection,
    entity_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=PAGE_LIMIT_MAX)] = PAGE_LIMIT_MAX,
) -> list[NoteRead]:
    """The notes of a deal, a client or a person, most recent first. Each one
    says who wrote it, when and from where, and whether the caller can edit it. A thread is
    a handful of remarks: `limit` is a cap against an endless read, and its
    default is the maximum, because half a thread is worth less than a whole one."""
    return NoteService(session).list_for_entity(
        ENTITY_TYPES[collection], entity_id, actor, limit=limit
    )


@router.post(
    "/{collection}/{entity_id}/notes",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_collection_module)],
)
def add_note(
    collection: Collection,
    entity_id: UUID,
    data: NoteCreate,
    session: SessionDep,
    actor: ActorDep,
) -> NoteRead:
    """Adds a note. Author, date and origin are written by the CRM: the body carries only the
    text."""
    return NoteService(session).add(ENTITY_TYPES[collection], entity_id, data, actor)


@router.patch("/notes/{note_id}", response_model=NoteRead)
def update_note(note_id: UUID, data: NoteUpdate, session: SessionDep, actor: ActorDep) -> NoteRead:
    """Changes a note's text: its author or an admin can edit it, and
    never a note written by the system or by rebase."""
    return NoteService(session).update(note_id, data, actor)


@router.get("/notes/{note_id}/revisions", response_model=list[NoteRevisionRead])
def list_revisions(note_id: UUID, session: SessionDep, actor: ActorDep) -> list[NoteRevisionRead]:
    """What the note said before each edit, in full, most recent first."""
    return NoteService(session).revisions(note_id, actor)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Deletes a note (it stays in the history): same rules as editing."""
    NoteService(session).delete(note_id, actor)
