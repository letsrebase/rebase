from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from pigrocrm.core.analytics.schemas import (
    BindTimeRequest,
    BudgetQuery,
    BudgetVsActualRow,
    DealPnl,
)
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.contracts.schemas import ContractFromDeal, ContractFromDealProposal, ContractRead
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.deals.schemas import (
    DealCreate,
    DealDetailRead,
    DealListQuery,
    DealPage,
    DealRead,
    DealUpdate,
    ValuePreviewIn,
    ValuePreviewRead,
)
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents.schemas import DocumentRead
from pigrocrm.core.errors import NotFound
from pigrocrm.core.invoices.schemas import InvoiceRead
from pigrocrm.core.timetracking.billing import TimeBilling
from pigrocrm.core.timetracking.report import TimeReportService
from pigrocrm.core.timetracking.schemas import (
    DealRateUpdate,
    DealTimeSummary,
    RateDescription,
    RecalculateRatesRequest,
    TimeEntryListQuery,
    TimeEntryPage,
)
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep, get_actor
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import (
    LEGAL_ENTITY_QUERY_DESCRIPTION,
    CustomParams,
    KeysetParams,
    SearchParams,
    SortedParams,
    sort_field,
)
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/deals",
    tags=["deals"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class MoveStageRequest(BaseModel):
    stage_id: UUID


class RecalculateResponse(BaseModel):
    """`updated_entries` rather than a bare integer: the UI says "12 voci aggiornate",
    and a naked number in a JSON body is a value nobody can label."""

    updated_entries: int


@router.post("", response_model=DealDetailRead, status_code=status.HTTP_201_CREATED)
def create(data: DealCreate, session: SessionDep, actor: ActorDep) -> DealDetailRead:
    """Creates a deal. Its value is written as `value_lines`; `expected_value` is derived from
    them and cannot be sent."""
    return DealService(session).create(data, actor)


@router.post("/value-preview", response_model=ValuePreviewRead)
def value_preview(data: ValuePreviewIn, session: SessionDep) -> ValuePreviewRead:
    """The total, the one-off figure and the monthly recurring figure a list of value lines
    would give, with the same checks a write applies. Writes nothing: a form calls it as the
    lines are typed, so the browser never adds or multiplies money."""
    return DealService(session).preview_value(data)


class DealListParams(SortedParams, SearchParams, CustomParams):
    customer_id: UUID | None = None
    stage_id: UUID | None = None
    legal_entity_id: UUID | None = Field(default=None, description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    # The two drill-throughs of the operational dashboard's signal cards (§6.2). Each card
    # links here and nowhere else, and the rows returned are counted by the same predicate
    # function the card's `COUNT` uses -- so the count and the list cannot drift apart.
    invoiced_unwon: bool = False
    to_invoice: bool = False
    sort: SafeStr | None = sort_field("created_at", "updated_at", "name")


@router.get("", response_model=DealPage)
def list_deals(
    session: SessionDep, actor: ActorDep, params: Annotated[DealListParams, Query()]
) -> DealPage:
    return DealService(session).list(params.to_query(DealListQuery), actor)


@router.get("/{deal_id}", response_model=DealDetailRead)
def get(deal_id: UUID, session: SessionDep, actor: ActorDep) -> DealDetailRead:
    """One deal with its value lines; the list carries only the figures. `contract_id` is the
    contract it was converted into, once it was."""
    return DealService(session).get(deal_id, actor)


@router.get("/{deal_id}/contract-proposal", response_model=ContractFromDealProposal)
def contract_proposal(
    deal_id: UUID, session: SessionDep, actor: ActorDep
) -> ContractFromDealProposal:
    """What converting this deal into a contract would start from: the suggested title, start
    and end, and the rate cards its lines become. Refused (409) like the conversion: the deal
    must be won, have value lines and no contract yet. Writes nothing."""
    return ContractService(session).proposal_from_deal(deal_id, actor)


@router.post(
    "/{deal_id}/contract", response_model=ContractRead, status_code=status.HTTP_201_CREATED
)
def convert_to_contract(
    deal_id: UUID, data: ContractFromDeal, session: SessionDep, actor: ActorDep
) -> ContractRead:
    """Turns a won deal into a draft contract for its customer, with one rate card per value
    line. The body is what the person confirms: title, start, end (`null` for none), the
    renewal type and notices, the billing cadence and the expense policy. Payment terms stay
    empty and cascade from the customer. Refused (409) unless the deal is won, has value
    lines and has no contract yet; from then on its value lines are locked, and the contract
    is the price."""
    return ContractService(session).create_from_deal(deal_id, data, actor)


@router.patch("/{deal_id}", response_model=DealDetailRead)
def update(deal_id: UUID, data: DealUpdate, session: SessionDep, actor: ActorDep) -> DealDetailRead:
    """Updates a deal. `value_lines`, when sent, replaces the whole list."""
    return DealService(session).update(deal_id, data, actor)


@router.patch("/{deal_id}/stage", response_model=DealRead)
def move_stage(
    deal_id: UUID, data: MoveStageRequest, session: SessionDep, actor: ActorDep
) -> DealRead:
    """Backs the Kanban drag. Optimistic on the client, authoritative here."""
    return DealService(session).move_stage(deal_id, data.stage_id, actor)


@router.delete("/{deal_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(deal_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    DealService(session).soft_delete(deal_id, actor)


@router.post("/{deal_id}/restore", response_model=DealRead)
def restore(deal_id: UUID, session: SessionDep, actor: ActorDep) -> DealRead:
    return DealService(session).restore(deal_id, actor)


router.include_router(timeline_router("deal", id_param="deal_id"))


class DealTimeEntriesParams(KeysetParams):
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")


@router.get("/{deal_id}/time-entries", response_model=TimeEntryPage)
def deal_time_entries(
    deal_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    params: Annotated[DealTimeEntriesParams, Query()],
) -> TimeEntryPage:
    return TimeEntryService(session).list(
        params.to_query(TimeEntryListQuery, deal_id=deal_id), actor
    )


@router.get("/{deal_id}/time-summary", response_model=DealTimeSummary)
def deal_time_summary(deal_id: UUID, session: SessionDep, actor: ActorDep) -> DealTimeSummary:
    return TimeEntryService(session).deal_summary(deal_id, actor)


@router.get("/{deal_id}/rates", response_model=RateDescription)
def deal_rates(
    deal_id: UUID, user_id: Annotated[UUID, Query()], session: SessionDep, actor: ActorDep
) -> RateDescription:
    """What a new entry would freeze right now. Shown next to the hours field so the
    rate is visible before saving, not discovered after."""
    return TimeEntryService(session).describe_rates(deal_id, user_id, actor)


@router.put("/{deal_id}/rate", status_code=status.HTTP_204_NO_CONTENT)
def set_deal_rate(
    deal_id: UUID, data: DealRateUpdate, session: SessionDep, actor: ActorDep
) -> None:
    TimeEntryService(session).update_deal_rate(deal_id, data, actor)


@router.post("/{deal_id}/rates/recalculate", response_model=RecalculateResponse)
def recalculate_rates(
    deal_id: UUID, data: RecalculateRatesRequest, session: SessionDep, actor: ActorDep
) -> RecalculateResponse:
    return RecalculateResponse(
        updated_entries=TimeEntryService(session).recalculate_rates(deal_id, data, actor)
    )


@router.get("/{deal_id}/time-report")
def time_report_xlsx(
    deal_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    month: Annotated[str, Query(description="Period in the form YYYY-MM")],
) -> Response:
    """The XLSX of the month's hours, streamed: it is a working copy that whoever checks
    it filters, not an artefact, so nothing is archived and a GET is safe. The PDF is
    `POST /time-report/pdf`, which archives a document (§2.1)."""
    filename, content = TimeReportService(session, storage).build_xlsx(deal_id, month, actor)
    return attachment_response(content, XLSX_CONTENT_TYPE, filename)


@router.post(
    "/{deal_id}/time-report/pdf",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
def render_time_report_pdf(
    deal_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    month: Annotated[str, Query(description="Period in the form YYYY-MM")],
) -> DocumentRead:
    """The month's hours as a PDF, archived as a `document` of the deal, which this
    returns: the bytes are fetched from the document download endpoint, like every other
    archived file, and the PDF gets attached to the invoice. A POST because it creates a
    document -- every call archives a new version, never overwriting an earlier one."""
    # Before the lookups: a `readonly` caller learns nothing, not even that the deal exists.
    actor.require_write("create_document")
    return TimeReportService(session, storage).render_pdf(deal_id, month, actor)


@router.get("/{deal_id}/pnl", response_model=DealPnl)
def deal_pnl(deal_id: UUID, session: SessionDep, actor: ActorDep) -> DealPnl:
    """Every row of §7.1, already summed. No role gate, deliberately: a deal's P&L is not
    more sensitive than the deal, the hours and the invoices it is derived from, each of
    which a `readonly` actor can already list."""
    return AnalyticsService(session).deal_pnl(deal_id, actor)


@router.get("/{deal_id}/budget", response_model=BudgetVsActualRow)
def deal_budget(
    deal_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    date_from: Annotated[date, Query(alias="from", description="Start of the period, YYYY-MM-DD")],
    date_to: Annotated[date, Query(alias="to", description="End of the period, YYYY-MM-DD")],
) -> BudgetVsActualRow:
    """One deal's row of the estimate-versus-actual report. Served from the same method as
    the list so the two can never disagree: a per-deal reimplementation is how the detail
    page and the report start showing different variances.

    `limit=200` -- the schema's own ceiling -- rather than a paged scan: `deals_in_range`
    orders by `(name, id)`, so the row wanted here can sit anywhere in that order, and one
    page at the maximum is the widest single window this query is allowed to open. Above
    that the answer is a 404 the caller can act on (narrow the window) instead of a page
    walk hidden behind a detail endpoint.
    """
    page = AnalyticsService(session).budget_vs_actual(
        BudgetQuery(date_from=date_from, date_to=date_to, limit=200), actor
    )
    for row in page.items:
        if row.deal_id == deal_id:
            return row
    # Not a row of zeroes: a deal with no activity in the window did not spend nothing,
    # it was not in the report at all.
    raise NotFound("deal_budget", deal_id)


@router.post("/{deal_id}/time-entries/to-invoice-draft", response_model=InvoiceRead)
def to_invoice_draft(
    deal_id: UUID,
    data: BindTimeRequest,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
) -> InvoiceRead:
    """Builds a draft; issues nothing. `InvoiceService` stays the sole owner of numbering,
    fiscal validation, rounding and freezing.

    `admin`, enforced by the service like every other role check in this codebase -- and
    with no MCP tool at all (§11's exclusion list), since choosing *which* hours to
    invoice is a commercial decision. The invoice state it may target is decided there
    too: hours sitting on a *draft* can be rebound, because that draft may be a mistake
    somebody is redoing, while hours on an *issued* invoice come back as a 409 naming the
    document -- one does not invoice the same work twice.

    `storage` is passed rather than left to the service's own settings fallback: the
    backend is the same process-wide one every other document route resolves through
    `StorageDep`.
    """
    return TimeBilling(session, storage).bind_to_invoice(deal_id, data, actor)
