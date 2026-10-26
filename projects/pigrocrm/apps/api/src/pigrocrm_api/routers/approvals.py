from fastapi import APIRouter, status

from pigrocrm.core.work_units.schemas import ApprovalRecord, ApprovalRecorded
from pigrocrm.core.work_units.service import ApprovalService
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/approvals", tags=["giornate"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=ApprovalRecorded, status_code=status.HTTP_201_CREATED)
def record(
    data: ApprovalRecord, session: SessionDep, actor: ActorDep, storage: StorageDep
) -> ApprovalRecorded:
    """Registra l'approvazione di un cliente e le giornate che copre, in una transazione:
    la prova (un documento già archiviato, o un messaggio di posta già sincronizzato che
    il servizio archivia come documento del contratto), l'approvazione, e il passaggio di
    ogni giornata (`proposto` diventa `approvato`, `lavorato_senza_approvazione` torna
    `lavorato`). Immutabile: una correzione è una nuova approvazione. Chiusa a ogni
    credenziale di tipo agente: un agente propone una giornata (`propose_day`), una persona
    registra l'approvazione."""
    return ApprovalService(session, storage).record(data, actor)
