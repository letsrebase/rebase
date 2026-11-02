from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pigrocrm.core.notes.schemas import (
    NoteCreate,
    NoteEntityType,
    NoteRead,
    NoteRevisionRead,
    NoteUpdate,
)
from pigrocrm.core.notes.service import NoteService
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
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


@router.get("/{collection}/{entity_id}/notes", response_model=list[NoteRead])
def list_notes(
    collection: Collection,
    entity_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=PAGE_LIMIT_MAX)] = PAGE_LIMIT_MAX,
) -> list[NoteRead]:
    """Le note di un deal, di un cliente o di una persona, la più recente per prima. Ognuna
    dice chi l'ha scritta, quando e da dove, e se chi chiede può modificarla. Un thread è
    una manciata di osservazioni: `limit` è un tetto contro una lettura senza fine, e il
    suo valore predefinito è il massimo, perché mezzo thread vale meno di uno intero."""
    return NoteService(session).list_for_entity(
        ENTITY_TYPES[collection], entity_id, actor, limit=limit
    )


@router.post(
    "/{collection}/{entity_id}/notes",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
)
def add_note(
    collection: Collection,
    entity_id: UUID,
    data: NoteCreate,
    session: SessionDep,
    actor: ActorDep,
) -> NoteRead:
    """Aggiunge una nota. Autore, data e origine li scrive il CRM: il corpo porta solo il
    testo."""
    return NoteService(session).add(ENTITY_TYPES[collection], entity_id, data, actor)


@router.patch("/notes/{note_id}", response_model=NoteRead)
def update_note(note_id: UUID, data: NoteUpdate, session: SessionDep, actor: ActorDep) -> NoteRead:
    """Cambia il testo di una nota: la modifica chi l'ha scritta o un amministratore, e
    mai una nota scritta dal sistema o da rebase."""
    return NoteService(session).update(note_id, data, actor)


@router.get("/notes/{note_id}/revisions", response_model=list[NoteRevisionRead])
def list_revisions(note_id: UUID, session: SessionDep, actor: ActorDep) -> list[NoteRevisionRead]:
    """Cosa diceva la nota prima di ogni modifica, per intero, la più recente per prima."""
    return NoteService(session).revisions(note_id, actor)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Cancella una nota (resta nella cronologia): stesse regole della modifica."""
    NoteService(session).delete(note_id, actor)
