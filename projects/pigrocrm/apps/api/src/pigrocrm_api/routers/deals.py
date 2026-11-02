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
from pigrocrm.core.deals.schemas import (
    DealCreate,
    DealListQuery,
    DealPage,
    DealRead,
    DealUpdate,
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
    AZIENDA_QUERY_DESCRIPTION,
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
    """`voci_aggiornate` rather than a bare integer: the UI says "12 voci aggiornate",
    and a naked number in a JSON body is a value nobody can label."""

    voci_aggiornate: int


@router.post("", response_model=DealRead, status_code=status.HTTP_201_CREATED)
def create(data: DealCreate, session: SessionDep, actor: ActorDep) -> DealRead:
    return DealService(session).create(data, actor)


class DealListParams(SortedParams, SearchParams, CustomParams):
    customer_id: UUID | None = None
    stage_id: UUID | None = None
    azienda_id: UUID | None = Field(default=None, description=AZIENDA_QUERY_DESCRIPTION)
    # The two drill-throughs of the operational dashboard's signal cards (§6.2). Each card
    # links here and nowhere else, and the rows returned are counted by the same predicate
    # function the card's `COUNT` uses -- so the count and the list cannot drift apart.
    fatturato_non_vinto: bool = False
    da_fatturare: bool = False
    sort: SafeStr | None = sort_field("created_at", "updated_at", "nome")


@router.get("", response_model=DealPage)
def list_deals(
    session: SessionDep, actor: ActorDep, params: Annotated[DealListParams, Query()]
) -> DealPage:
    return DealService(session).list(params.to_query(DealListQuery), actor)


@router.get("/{deal_id}", response_model=DealRead)
def get(deal_id: UUID, session: SessionDep, actor: ActorDep) -> DealRead:
    return DealService(session).get(deal_id, actor)


@router.patch("/{deal_id}", response_model=DealRead)
def update(deal_id: UUID, data: DealUpdate, session: SessionDep, actor: ActorDep) -> DealRead:
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
    da: date | None = None
    a: date | None = None


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
        voci_aggiornate=TimeEntryService(session).recalculate_rates(deal_id, data, actor)
    )


@router.get("/{deal_id}/time-report")
def time_report_xlsx(
    deal_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    mese: Annotated[str, Query(description="Periodo nella forma AAAA-MM")],
) -> Response:
    """The XLSX of the month's hours, streamed: it is a working copy that whoever checks
    it filters, not an artefact, so nothing is archived and a GET is safe. The PDF is
    `POST /time-report/pdf`, which archives a document (§2.1)."""
    filename, content = TimeReportService(session, storage).build_xlsx(deal_id, mese, actor)
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
    mese: Annotated[str, Query(description="Periodo nella forma AAAA-MM")],
) -> DocumentRead:
    """The month's hours as a PDF, archived as a `document` of the deal, which this
    returns: the bytes are fetched from the document download endpoint, like every other
    archived file, and the PDF gets attached to the invoice. A POST because it creates a
    document -- every call archives a new version, never overwriting an earlier one."""
    # Before the lookups: a `readonly` caller learns nothing, not even that the deal exists.
    actor.require_write("create_document")
    return TimeReportService(session, storage).render_pdf(deal_id, mese, actor)


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
    da: Annotated[date, Query(alias="from", description="Inizio del periodo, YYYY-MM-DD")],
    a: Annotated[date, Query(alias="to", description="Fine del periodo, YYYY-MM-DD")],
) -> BudgetVsActualRow:
    """One deal's row of the estimate-versus-actual report. Served from the same method as
    the list so the two can never disagree: a per-deal reimplementation is how the detail
    page and the report start showing different variances.

    `limit=200` -- the schema's own ceiling -- rather than a paged scan: `deals_in_range`
    orders by `(nome, id)`, so the row wanted here can sit anywhere in that order, and one
    page at the maximum is the widest single window this query is allowed to open. Above
    that the answer is a 404 the caller can act on (narrow the window) instead of a page
    walk hidden behind a detail endpoint.
    """
    page = AnalyticsService(session).budget_vs_actual(BudgetQuery(da=da, a=a, limit=200), actor)
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
