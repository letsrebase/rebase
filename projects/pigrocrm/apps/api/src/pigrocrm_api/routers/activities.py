from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.attivita.schemas import (
    AttivitaCreate,
    AttivitaListQuery,
    AttivitaPage,
    AttivitaRead,
    AttivitaStato,
    AttivitaUpdate,
)
from pigrocrm.core.attivita.service import AttivitaService
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/activities", tags=["attivita"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=AttivitaRead, status_code=status.HTTP_201_CREATED)
def create(data: AttivitaCreate, session: SessionDep, actor: ActorDep) -> AttivitaRead:
    return AttivitaService(session).create(data, actor)


@router.get("", response_model=AttivitaPage)
def list_attivita(
    session: SessionDep,
    actor: ActorDep,
    stato: Annotated[AttivitaStato | None, Query()] = None,
    assegnata_a: Annotated[UUID | None, Query()] = None,
    customer_id: Annotated[UUID | None, Query()] = None,
    person_id: Annotated[UUID | None, Query()] = None,
    deal_id: Annotated[UUID | None, Query()] = None,
    invoice_id: Annotated[UUID | None, Query()] = None,
    scade_entro: Annotated[date | None, Query()] = None,
    # Three states and not two: `None` is «both», which is what a caller who does not
    # care has to be able to say. See `AttivitaListQuery`.
    senza_scadenza: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    # `str` and not `UUID` since slice 6: the cursor is opaque and carries the sort
    # value as well as the id.
    cursor: Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)] = None,
    # A plain string and not a `Literal`, like every other list in this API: the
    # whitelist lives in `ATTIVITA_SORTS` and an unknown key resolves to the default
    # there, in one place.
    sort: Annotated[SafeStr | None, Query(description="created_at | scadenza")] = None,
    dir: Annotated[SortDirection, Query()] = "asc",
) -> AttivitaPage:
    query = AttivitaListQuery(
        stato=stato,
        assegnata_a=assegnata_a,
        customer_id=customer_id,
        person_id=person_id,
        deal_id=deal_id,
        invoice_id=invoice_id,
        scade_entro=scade_entro,
        senza_scadenza=senza_scadenza,
        limit=limit,
        cursor=cursor,
        sort=sort,
        dir=dir,
    )
    return AttivitaService(session).list(query, actor)


@router.get("/{attivita_id}", response_model=AttivitaRead)
def get(attivita_id: UUID, session: SessionDep, actor: ActorDep) -> AttivitaRead:
    return AttivitaService(session).get(attivita_id, actor)


@router.patch("/{attivita_id}", response_model=AttivitaRead)
def update(
    attivita_id: UUID, data: AttivitaUpdate, session: SessionDep, actor: ActorDep
) -> AttivitaRead:
    return AttivitaService(session).update(attivita_id, data, actor)


# The three closures, each its own route rather than a `PATCH {"stato": ...}`. Two
# reasons: the state machine is the service's business (a cancelled activity does not
# become completed by writing a column), and «done» has a side effect -- the day it was
# done -- that a caller must not be able to supply.
@router.post("/{attivita_id}/complete", response_model=AttivitaRead)
def complete(attivita_id: UUID, session: SessionDep, actor: ActorDep) -> AttivitaRead:
    return AttivitaService(session).complete(attivita_id, actor)


@router.post("/{attivita_id}/cancel", response_model=AttivitaRead)
def cancel(attivita_id: UUID, session: SessionDep, actor: ActorDep) -> AttivitaRead:
    return AttivitaService(session).cancel(attivita_id, actor)


@router.post("/{attivita_id}/reopen", response_model=AttivitaRead)
def reopen(attivita_id: UUID, session: SessionDep, actor: ActorDep) -> AttivitaRead:
    return AttivitaService(session).reopen(attivita_id, actor)


@router.delete("/{attivita_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive(attivita_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Archiving, which is for the typo. The change of plan is `cancel` -- and the two
    being different operations is the whole reason there are three states."""
    AttivitaService(session).soft_delete(attivita_id, actor)


@router.post("/{attivita_id}/restore", response_model=AttivitaRead)
def restore(attivita_id: UUID, session: SessionDep, actor: ActorDep) -> AttivitaRead:
    return AttivitaService(session).restore(attivita_id, actor)
