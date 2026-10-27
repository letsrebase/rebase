from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.contracts.schemas import (
    ClauseNoteCreate,
    ClauseNoteRead,
    ClauseNoteUpdate,
    ContractConcentrationCap,
    ContractCreate,
    ContractDetail,
    ContractListQuery,
    ContractPage,
    ContractProjectionQuery,
    ContractProjectionRead,
    ContractRead,
    ContractStateChange,
    ContractUpdate,
    RateCardCreate,
    RateCardRead,
    RateCardUpdate,
    RenewalAssumptionRead,
    RenewalAssumptionUpsert,
)
from pigrocrm.core.contracts.service import (
    ClauseNoteService,
    ContractProjectionService,
    ContractService,
    RateCardService,
    RenewalAssumptionService,
)
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/contracts", tags=["contracts"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=ContractRead, status_code=status.HTTP_201_CREATED)
def create(data: ContractCreate, session: SessionDep, actor: ActorDep) -> ContractRead:
    return ContractService(session).create(data, actor)


@router.get("", response_model=ContractPage)
def list_contracts(
    session: SessionDep,
    actor: ActorDep,
    customer_id: Annotated[UUID | None, Query()] = None,
    stato: Annotated[str | None, Query()] = None,
    azienda_id: Annotated[
        UUID | None, Query(description="Solo le righe di questa azienda; omesso, tutte")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)] = None,
    sort: Annotated[SafeStr | None, Query(description="created_at | updated_at | titolo")] = None,
    dir: Annotated[SortDirection, Query()] = "asc",
) -> ContractPage:
    query = ContractListQuery(
        customer_id=customer_id,
        stato=stato,
        azienda_id=azienda_id,
        limit=limit,
        cursor=cursor,
        sort=sort,
        dir=dir,
    )
    return ContractService(session).list(query, actor)


@router.get("/{contract_id}", response_model=ContractDetail)
def get(contract_id: UUID, session: SessionDep, actor: ActorDep) -> ContractDetail:
    """Il contratto con le sue note sulle clausole (REB-762) e, se è stato rinnovato,
    l'id di chi lo rinnova (REB-763)."""
    return ContractService(session).detail(contract_id, actor)


@router.patch("/{contract_id}", response_model=ContractRead)
def update(
    contract_id: UUID, data: ContractUpdate, session: SessionDep, actor: ActorDep
) -> ContractRead:
    return ContractService(session).update(contract_id, data, actor)


@router.post("/{contract_id}/state", response_model=ContractRead)
def set_state(
    contract_id: UUID, data: ContractStateChange, session: SessionDep, actor: ActorDep
) -> ContractRead:
    return ContractService(session).set_state(contract_id, data, actor)


@router.get("/{contract_id}/timeline", response_model=list[ActivityRead])
def timeline(
    contract_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActivityRead]:
    ContractService(session).get(contract_id, actor)
    return ActivityService(session).timeline("contract", contract_id, limit)


@router.get("/{contract_id}/concentration", response_model=ContractConcentrationCap)
def concentration_cap(
    contract_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    as_of: Annotated[date | None, Query()] = None,
    soglia: Annotated[float | None, Query(ge=0, le=1)] = None,
) -> ContractConcentrationCap:
    return ContractService(session).concentration_cap(contract_id, actor, as_of, soglia)


@router.post(
    "/{contract_id}/rate-cards",
    response_model=RateCardRead,
    status_code=status.HTTP_201_CREATED,
)
def create_rate_card(
    contract_id: UUID,
    data: RateCardCreate,
    session: SessionDep,
    actor: ActorDep,
    chiudi_precedente: Annotated[
        bool,
        Query(
            description="Chiude il giorno prima la scheda in vigore alla data di inizio "
            "di questa, invece di rifiutare la sovrapposizione"
        ),
    ] = False,
) -> RateCardRead:
    return RateCardService(session).create(
        contract_id, data, actor, close_previous=chiudi_precedente
    )


@router.patch("/{contract_id}/rate-cards/{rate_card_id}", response_model=RateCardRead)
def update_rate_card(
    contract_id: UUID,
    rate_card_id: UUID,
    data: RateCardUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> RateCardRead:
    return RateCardService(session).update(contract_id, rate_card_id, data, actor)


@router.get("/{contract_id}/rate-cards", response_model=list[RateCardRead])
def list_rate_cards(contract_id: UUID, session: SessionDep, actor: ActorDep) -> list[RateCardRead]:
    return RateCardService(session).list_for_contract(contract_id, actor)


@router.post(
    "/{contract_id}/clause-notes",
    response_model=ClauseNoteRead,
    status_code=status.HTTP_201_CREATED,
)
def create_clause_note(
    contract_id: UUID, data: ClauseNoteCreate, session: SessionDep, actor: ActorDep
) -> ClauseNoteRead:
    """Una lettura di una clausola: il riferimento, il testo letterale e l'interpretazione
    adottata (REB-762)."""
    return ClauseNoteService(session).create(contract_id, data, actor)


@router.patch("/{contract_id}/clause-notes/{note_id}", response_model=ClauseNoteRead)
def update_clause_note(
    contract_id: UUID,
    note_id: UUID,
    data: ClauseNoteUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> ClauseNoteRead:
    return ClauseNoteService(session).update(contract_id, note_id, data, actor)


@router.get("/{contract_id}/clause-notes", response_model=list[ClauseNoteRead])
def list_clause_notes(
    contract_id: UUID, session: SessionDep, actor: ActorDep
) -> list[ClauseNoteRead]:
    return ClauseNoteService(session).list_for_contract(contract_id, actor)


@router.put("/{contract_id}/renewal-assumption", response_model=RenewalAssumptionRead)
def upsert_renewal_assumption(
    contract_id: UUID, data: RenewalAssumptionUpsert, session: SessionDep, actor: ActorDep
) -> RenewalAssumptionRead:
    """Admin or collaboratore, enforced by the service (`actor.require_write`), not
    here -- there is no role dependency in this codebase, and adding one here would
    put the same rule in two places."""
    return RenewalAssumptionService(session).upsert(contract_id, data, actor)


@router.get("/{contract_id}/renewal-assumption", response_model=RenewalAssumptionRead)
def get_renewal_assumption(
    contract_id: UUID, session: SessionDep, actor: ActorDep
) -> RenewalAssumptionRead:
    return RenewalAssumptionService(session).get(contract_id, actor)


@router.get("/{contract_id}/projection", response_model=ContractProjectionRead)
def project_revenue(
    contract_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    da: Annotated[date, Query(description="Inizio della finestra, YYYY-MM-DD, inclusa")],
    a: Annotated[date, Query(description="Fine della finestra, YYYY-MM-DD, esclusa")],
    come_di: Annotated[
        date | None,
        Query(description="Data di riferimento per la finestra di irrevocabilità; default oggi"),
    ] = None,
) -> ContractProjectionRead:
    """Il ricavo «programmato» oltre la finestra di irrevocabilità, più il contributo
    dell'eventuale assunzione di rinnovo -- una figura distinta dal `proiettato` di
    `GET /api/analytics/overview`, mai combinata con esso."""
    query = ContractProjectionQuery(da=da, a=a, come_di=come_di)
    return ContractProjectionService(session).project(contract_id, query, actor)
