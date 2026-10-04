from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.analytics.schemas import (
    BindTimeRequest,
    BudgetQuery,
    BudgetVsActualRow,
    DealPnl,
)
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection
from pigrocrm.core.deals.schemas import (
    DealCreate,
    DealListQuery,
    DealPage,
    DealRead,
    DealUpdate,
)
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.invoices.schemas import InvoiceRead
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
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import CUSTOM_QUERY_DESCRIPTION, parse_custom_filter

router = APIRouter(prefix="/api/deals", tags=["deals"], responses=PROBLEM_RESPONSES)

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


@router.get("", response_model=DealPage)
def list_deals(
    session: SessionDep,
    actor: ActorDep,
    # SafeStr: see the identical comment on list_customers (routers/customers.py)
    # -- these are ordinary query parameters, not Create/Update schema fields, so
    # the guard has to sit on the parameter itself for FastAPI's own validation to
    # catch it as a 422 before DealListQuery is hand-built below.
    search: Annotated[SafeStr | None, Query()] = None,
    customer_id: Annotated[UUID | None, Query()] = None,
    stage_id: Annotated[UUID | None, Query()] = None,
    azienda_id: Annotated[
        UUID | None, Query(description="Solo le righe di questa azienda; omesso, tutte")
    ] = None,
    custom: Annotated[list[SafeStr] | None, Query(description=CUSTOM_QUERY_DESCRIPTION)] = None,
    # The two drill-throughs of the operational dashboard's signal cards (§6.2). Each card
    # links here and nowhere else, and the rows returned are counted by the same predicate
    # function the card's `COUNT` uses -- so the count and the list cannot drift apart.
    fatturato_non_vinto: Annotated[bool, Query()] = False,
    da_fatturare: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    # `str`, not `UUID`, since slice 6 -- see the identical comment on list_customers
    # (routers/customers.py). The `cursor` on `list_deal_time_entries` further down
    # stays a `UUID`: `time_entries` has no sort whitelist and still pages by id.
    cursor: Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)] = None,
    # A plain string and not a `Literal` -- see the reasoning on list_customers
    # (routers/customers.py).
    sort: Annotated[SafeStr | None, Query(description="created_at | updated_at | nome")] = None,
    dir: Annotated[SortDirection, Query()] = "asc",
) -> DealPage:
    query = DealListQuery(
        search=search,
        customer_id=customer_id,
        stage_id=stage_id,
        azienda_id=azienda_id,
        custom=parse_custom_filter(custom),
        fatturato_non_vinto=fatturato_non_vinto,
        da_fatturare=da_fatturare,
        limit=limit,
        cursor=cursor,
        sort=sort,
        dir=dir,
    )
    return DealService(session).list(query, actor)


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


@router.get("/{deal_id}/timeline", response_model=list[ActivityRead])
def timeline(
    deal_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActivityRead]:
    return ActivityService(session).timeline("deal", deal_id, limit)


@router.get("/{deal_id}/time-entries", response_model=TimeEntryPage)
def deal_time_entries(
    deal_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    da: Annotated[date | None, Query()] = None,
    a: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> TimeEntryPage:
    return TimeEntryService(session).list(
        TimeEntryListQuery(deal_id=deal_id, da=da, a=a, limit=limit, cursor=cursor), actor
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
def time_report(
    deal_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    mese: Annotated[str, Query(description="Periodo nella forma AAAA-MM")],
    formato: Annotated[Literal["pdf", "xlsx"], Query()] = "pdf",
) -> Response:
    """Two recipients, two needs (§2.1): the PDF gets attached to the invoice, the XLSX
    gets filtered by whoever checks it. `formato` is a `Literal`, so an unsupported
    value is a 422 rather than a branch nobody wrote.

    The PDF is archived as a `document` and this returns its id, matching the slice 2
    rule that the bytes are fetched from the document download endpoint; the XLSX is
    streamed, because it is a working copy and not an artefact.
    """
    service = TimeReportService(session, storage)
    if formato == "xlsx":
        filename, content = service.build_xlsx(deal_id, mese, actor)
        return Response(
            content=content,
            media_type=XLSX_CONTENT_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    document = service.render_pdf(deal_id, mese, actor)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=jsonable_encoder(document))


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

    `storage` is passed rather than left to the service's own settings fallback: this is
    the one method in `AnalyticsService` that constructs an `InvoiceService`, whose
    backend is the same process-wide one every other document route resolves through
    `StorageDep`.
    """
    return AnalyticsService(session, storage).bind_time_to_invoice(deal_id, data, actor)
