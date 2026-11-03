from uuid import UUID

from fastapi import APIRouter, Depends, status

from pigrocrm.core.work_units.approval_request import (
    ApprovalRequestCompose,
    ApprovalRequestMessage,
    ApprovalRequestPrepare,
    ApprovalRequestPrepared,
    ApprovalRequestService,
)
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/contracts/{contract_id}/approval-request",
    tags=["work_units"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("/preview", response_model=ApprovalRequestMessage)
def preview(
    contract_id: UUID,
    data: ApprovalRequestCompose,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> ApprovalRequestMessage:
    """Il testo che la richiesta di approvazione porterebbe, per le date indicate: oggetto
    e corpo, scritti e non salvati. Una lettura."""
    return ApprovalRequestService(session, settings).compose(contract_id, data, actor)


@router.post("", response_model=ApprovalRequestPrepared, status_code=status.HTTP_201_CREATED)
def prepare(
    contract_id: UUID,
    data: ApprovalRequestPrepare,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> ApprovalRequestPrepared:
    """Registra le giornate chieste come `proposed` e salva il messaggio come bozza del
    cliente, in una transazione. Non invia: l'invio è `POST /api/email-drafts/{id}/send`,
    che una persona preme dopo aver letto la bozza. Una data in cui il contratto ha già
    una giornata risponde 409. Chiusa a ogni credenziale di tipo agente."""
    return ApprovalRequestService(session, settings).prepare(contract_id, data, actor)
