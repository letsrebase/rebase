from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from pigrocrm.core.documents.schemas import DocumentRead
from pigrocrm.core.invoices.schemas import InvoiceRead
from pigrocrm.core.work_units.billing_schemas import BillableDays, InvoiceDaysRequest
from pigrocrm.core.work_units.invoicing import (
    assemble_unbilled_work_units_into_new_invoice,
    billable_days,
)
from pigrocrm.core.work_units.register import (
    CSV_CONTENT_TYPE,
    ContractRegister,
    ContractRegisterService,
)
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/contracts", tags=["contract-billing"], responses=PROBLEM_RESPONSES)

Da = Annotated[date, Query(description="Dal giorno, YYYY-MM-DD, incluso")]
A = Annotated[date, Query(description="Al giorno, YYYY-MM-DD, incluso")]


@router.get("/{contract_id}/billable-days", response_model=BillableDays)
def get_billable_days(
    contract_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    da: Annotated[date | None, Query(description="Dal giorno, incluso")] = None,
    a: Annotated[date | None, Query(description="Al giorno, incluso")] = None,
) -> BillableDays:
    """Le giornate pronte da fatturare di un contratto, come le righe che metterebbero in una
    bozza: `lavorato` e non ancora su una riga di fattura. Sola lettura."""
    return billable_days(session, contract_id, da, a)


@router.post(
    "/{contract_id}/invoice-days", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED
)
def invoice_days(
    contract_id: UUID,
    data: InvoiceDaysRequest,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
) -> InvoiceRead:
    """Crea la bozza di fattura con le giornate pronte del contratto (o quelle fra `da` e `a`),
    alle tariffe del contratto, e le segna `fatturato`. Non emette niente. Solo un
    amministratore e mai un agente: scegliere cosa finisce su una bozza decide cosa
    congelerà l'emissione."""
    return assemble_unbilled_work_units_into_new_invoice(
        session, storage, contract_id, actor, data.da, data.a
    )


@router.get("/{contract_id}/register", response_model=ContractRegister)
def get_register(
    contract_id: UUID, session: SessionDep, actor: ActorDep, da: Da, a: A
) -> ContractRegister:
    """Il registro delle giornate fatturate di un contratto nel periodo, ciascuna con la
    prova di approvazione che la regge. Sola lettura."""
    return ContractRegisterService(session).build(contract_id, da, a, actor)


@router.get("/{contract_id}/register/csv")
def get_register_csv(
    contract_id: UUID, session: SessionDep, actor: ActorDep, da: Da, a: A
) -> Response:
    """Lo stesso registro come CSV (RFC 4180, UTF-8 con BOM). Una copia di lavoro che il
    cliente filtra: si ricalcola a ogni richiesta e non si archivia, come l'XLSX delle ore."""
    service = ContractRegisterService(session)
    register = service.build(contract_id, da, a, actor)
    return Response(
        content=service.build_csv(register),
        media_type=CSV_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{service.csv_filename(register)}"'},
    )


@router.post(
    "/{contract_id}/register/pdf", response_model=DocumentRead, status_code=status.HTTP_201_CREATED
)
def render_register_pdf(
    contract_id: UUID, session: SessionDep, actor: ActorDep, storage: StorageDep, da: Da, a: A
) -> DocumentRead:
    """Il registro come PDF, archiviato come documento del cliente: si scarica dal documento
    e si allega alla fattura. Ogni chiamata archivia un documento nuovo: non ne sovrascrive
    mai uno precedente."""
    return ContractRegisterService(session, storage).render_pdf(contract_id, da, a, actor)
