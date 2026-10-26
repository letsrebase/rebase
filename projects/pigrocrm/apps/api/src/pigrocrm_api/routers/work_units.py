from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.work_units.schemas import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    WorkUnitCreate,
    WorkUnitDetail,
    WorkUnitList,
    WorkUnitListQuery,
    WorkUnitRead,
    WorkUnitStato,
    WorkUnitTransitionIn,
)
from pigrocrm.core.work_units.service import WorkUnitService
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/work-units", tags=["giornate"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=WorkUnitRead, status_code=status.HTTP_201_CREATED)
def create(data: WorkUnitCreate, session: SessionDep, actor: ActorDep) -> WorkUnitRead:
    """Registra una giornata. Una giornata `lavorato` su un contratto che richiede
    l'approvazione preventiva, senza approvazione, nasce `lavorato_senza_approvazione`: il
    trigger la segna invece di rifiutarla. Chiusa a ogni credenziale di tipo agente: un
    agente propone (`propose_day`), una persona conferma."""
    return WorkUnitService(session).create(data, actor)


@router.get("", response_model=WorkUnitList)
def list_work_units(
    session: SessionDep,
    actor: ActorDep,
    contract_id: Annotated[UUID | None, Query()] = None,
    azienda_id: Annotated[
        UUID | None, Query(description="Solo i contratti di questa azienda; omesso, tutte")
    ] = None,
    da: Annotated[date | None, Query(description="Dal giorno, YYYY-MM-DD, incluso")] = None,
    a: Annotated[date | None, Query(description="Al giorno, YYYY-MM-DD, incluso")] = None,
    stato: Annotated[WorkUnitStato | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = LIST_LIMIT_DEFAULT,
) -> WorkUnitList:
    """Le giornate, dalla più recente. Serve almeno un contratto o una data (`da`, `a`):
    il registro intero di uno spazio non è una domanda che una schermata faccia."""
    query = WorkUnitListQuery(
        contract_id=contract_id, azienda_id=azienda_id, da=da, a=a, stato=stato, limit=limit
    )
    return WorkUnitService(session).list(query, actor)


@router.get("/{work_unit_id}", response_model=WorkUnitDetail)
def get(work_unit_id: UUID, session: SessionDep, actor: ActorDep) -> WorkUnitDetail:
    return WorkUnitService(session).detail(work_unit_id, actor)


@router.post("/{work_unit_id}/transitions", response_model=WorkUnitDetail)
def transition(
    work_unit_id: UUID, data: WorkUnitTransitionIn, session: SessionDep, actor: ActorDep
) -> WorkUnitDetail:
    """Sposta una giornata lungo il suo ciclo di vita. L'arco lo decide il database
    (`work_unit_enforce_state_machine`); un arco che il grafo non ammette risponde 409."""
    service = WorkUnitService(session)
    service.transition(work_unit_id, data.stato, actor, data.motivo)
    return service.detail(work_unit_id, actor)
