from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

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
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep, get_actor, module_guard
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/contracts",
    tags=["contract-billing"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("contracts")],
)

DateFrom = Annotated[date, Query(alias="from", description="From the day, YYYY-MM-DD, inclusive")]
DateTo = Annotated[date, Query(alias="to", description="To the day, YYYY-MM-DD, inclusive")]


@router.get("/{contract_id}/billable-days", response_model=BillableDays)
def get_billable_days(
    contract_id: UUID,
    session: SessionDep,
    date_from: Annotated[
        date | None, Query(alias="from", description="From the day, inclusive")
    ] = None,
    date_to: Annotated[date | None, Query(alias="to", description="To the day, inclusive")] = None,
) -> BillableDays:
    """A contract's workdays ready to invoice, as the rows they would make in a
    draft: `worked` and not yet on an invoice line. Read-only."""
    return billable_days(session, contract_id, date_from, date_to)


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
    """Creates the draft invoice with the contract's ready workdays (or those between `date_from`
    and `date_to`), at the contract's rates, and marks them `invoiced`. It issues nothing. Admin
    only and never an agent: choosing what ends up on a draft decides what issuing will freeze."""
    return assemble_unbilled_work_units_into_new_invoice(
        session, storage, contract_id, actor, data.date_from, data.date_to
    )


@router.get("/{contract_id}/register", response_model=ContractRegister)
def get_register(
    contract_id: UUID, session: SessionDep, actor: ActorDep, date_from: DateFrom, date_to: DateTo
) -> ContractRegister:
    """The register of a contract's invoiced workdays in the period, each with the
    approval proof that supports it. Read-only."""
    return ContractRegisterService(session).build(contract_id, date_from, date_to, actor)


@router.get("/{contract_id}/register/csv")
def get_register_csv(
    contract_id: UUID, session: SessionDep, actor: ActorDep, date_from: DateFrom, date_to: DateTo
) -> Response:
    """The same register as CSV (RFC 4180, UTF-8 with BOM). A working copy the
    client filters: recomputed on every request and not archived, like the hours XLSX."""
    service = ContractRegisterService(session)
    register = service.build(contract_id, date_from, date_to, actor)
    return attachment_response(
        service.build_csv(register), CSV_CONTENT_TYPE, service.csv_filename(register)
    )


@router.post(
    "/{contract_id}/register/pdf", response_model=DocumentRead, status_code=status.HTTP_201_CREATED
)
def render_register_pdf(
    contract_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    date_from: DateFrom,
    date_to: DateTo,
) -> DocumentRead:
    """The register as PDF, archived as a client document: download it from the document
    and attach it to the invoice. Every call archives a new document: it never
    overwrites a previous one."""
    return ContractRegisterService(session, storage).render_pdf(
        contract_id, date_from, date_to, actor
    )
