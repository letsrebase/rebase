"""The period reports. Thin, like every other router here: resolve the Actor, call
`AnalyticsService`, serialise.

Every figure arrives already summed, and no endpoint in this file adds, divides or
rounds anything -- §6 forbids the frontend of this slice from computing an economic
total at all, and a router that computed one would be the same defect one layer down.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import Field

from pigrocrm.core.analytics.schemas import (
    BudgetPage,
    BudgetQuery,
    CashBase,
    CeilingHeadroom,
    CeilingSimulation,
    CeilingSimulationQuery,
    DaysAnalysis,
    EconomicOverview,
    FiscalEstimate,
    PeriodPnl,
    PeriodPnlQuery,
    RevenueBase,
    UnbilledBacklog,
    YearOverview,
)
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureRead, DeclaredTaxFigureSet
from pigrocrm.core.modules import enabled_modules, require_modules
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor, module_guard
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import KeysetParams

router = APIRouter(
    prefix="/api/analytics",
    tags=["analytics"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)

# `from` and `to` on the wire, as spec §11 writes them; `da`/`a` as the parameter names,
# because `from` is a Python keyword. The alias is what keeps both true, and it is
# recorded here rather than silently diverging from the spec.
FromDate = Annotated[date, Query(alias="from", description="Start of the period, YYYY-MM-DD")]
ToDate = Annotated[date, Query(alias="to", description="End of the period, YYYY-MM-DD")]
# The sidebar's legal entity on a sum (REB-630, spec 2026-10-03 §1.9): one more predicate,
# omitted for «tutte». On the estimate and the ceilings it is the legal entity the figure is
# computed for: resolved alone on a one-legal entity space, required by name on a space with
# several, since a forfettario's coefficients and an SRL's arithmetic do not add.
LegalEntityFilter = Annotated[
    UUID | None,
    Query(
        description=(
            "Limits the figures to one legal entity of the space; omitted, all legal entities "
            "summed. The response reports it in `legal_entity_id`."
        )
    ),
]
LegalEntityRequired = Annotated[
    UUID | None,
    Query(
        description=(
            "The legal entity to compute for: required when the space has more than one (a 422 "
            "response names `legal_entity_id`), otherwise it is the only one and can be omitted."
        )
    ),
]


@router.get("/pnl", response_model=PeriodPnl)
def period_pnl(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    date_from: FromDate,
    date_to: ToDate,
    customer_id: Annotated[UUID | None, Query()] = None,
    legal_entity_id: LegalEntityFilter = None,
    base: Annotated[
        RevenueBase,
        Query(
            description=(
                "Which date places an invoice's revenue in the period: 'issue' (the document "
                "date, the default) or 'accrual' (the accrual period declared on the invoice, "
                "with the issue date for invoices that do not declare one). Costs and hours stay "
                "attributed to their own date."
            )
        ),
    ] = "issue",
) -> PeriodPnl:
    """Two columns, closed deals and deals in progress. There is deliberately no combined
    total: adding a finished job's margin to a half-done one produces a figure that is
    neither. `base` picks which of the two readings of revenue the report gives
    (ORB-61); everything else about it is the same."""
    return AnalyticsService(session).period_pnl(
        PeriodPnlQuery(
            date_from=date_from,
            date_to=date_to,
            customer_id=customer_id,
            base=base,
            legal_entity_id=legal_entity_id,
        ),
        actor,
        enabled_modules(settings),
    )


class BudgetParams(KeysetParams):
    date_from: date = Field(alias="from", description="Start of the period, YYYY-MM-DD")
    date_to: date = Field(alias="to", description="End of the period, YYYY-MM-DD")
    customer_id: UUID | None = None


@router.get("/budget", response_model=BudgetPage, dependencies=[module_guard("deals", "time")])
def budget_vs_actual(
    session: SessionDep, actor: ActorDep, params: Annotated[BudgetParams, Query()]
) -> BudgetPage:
    """The period filter is mandatory and the list is paginated from the first commit
    (residual B3): a margins view is by its nature a list of deals, so unbounded growth
    stops being invisible here."""
    return AnalyticsService(session).budget_vs_actual(params.to_query(BudgetQuery), actor)


@router.get("/overview", response_model=EconomicOverview)
def economic_overview(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityFilter = None,
    base: Annotated[
        CashBase,
        Query(
            description=(
                "In which month each document's money falls: 'accrual' (the accrual period "
                "declared on the invoice or proforma, with the document date for those that do "
                "not declare one; the default) or 'collection' (the collection date for paid "
                "invoices, the due date for those still to be collected, the document or creation"
                " date for drafts and proformas). The fiscal estimate always stays on the year's "
                "collected amount."
            )
        ),
    ] = "accrual",
) -> EconomicOverview:
    """The dashboard's economic tab: the year as cash for everyone, plus the fiscal
    estimate on collected and projected revenue for an admin with a profile. No MCP
    tool, for the estimate's own reasons. `base` moves the charts and the cash cards
    between the two readings (ORB-133); the fiscal block stays on the money. In «tutte»
    on a space with several legal entities the fiscal block is empty and the page asks
    `/fiscal` once per legal entity (REB-630)."""
    return AnalyticsService(session).economic_overview(
        year, actor, base, legal_entity_id, enabled_modules(settings)
    )


@router.get("/fiscal", response_model=FiscalEstimate, dependencies=[module_guard("taxes")])
def fiscal_estimate(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityRequired = None,
) -> FiscalEstimate:
    """`admin` -- enforced by the service, not here. A router containing an authorisation
    `if` is a router the MCP adapter cannot reuse, and this figure has no MCP tool at all
    (§11's exclusion list), so the check has to live where both adapters share it."""
    return AnalyticsService(session).get_fiscal_estimate(
        year, actor, legal_entity_id=legal_entity_id
    )


@router.put(
    "/fiscal/declared",
    response_model=DeclaredTaxFigureRead,
    dependencies=[module_guard("taxes")],
)
def set_declared_tax_figure(
    data: DeclaredTaxFigureSet,
    session: SessionDep,
    actor: ActorDep,
    legal_entity_id: LegalEntityRequired = None,
) -> DeclaredTaxFigureRead:
    """The figures of the tax return as the accountant computed them, for one tax and year;
    typing them again replaces the three together. `admin`, enforced by the service. They are
    never computed and a newer estimate leaves them alone: `/fiscal` shows them beside the
    estimate and says the year's reading is declared."""
    return DeclaredTaxFigureService(session).set_declared_tax_figure(
        data, actor, legal_entity_id=legal_entity_id
    )


@router.get("/backlog", response_model=UnbilledBacklog)
def backlog(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> UnbilledBacklog:
    """No period parameter, deliberately: see `AnalyticsService.unbilled_backlog`.

    Placed beside the other analytics reads rather than under `/dashboard`, because the
    figure belongs to `AnalyticsService` and §3 forbids a dashboard module from owning
    one -- its euro value is a product of two columns.
    """
    return AnalyticsService(session).unbilled_backlog(actor, modules=enabled_modules(settings))


@router.get("/ceilings", response_model=CeilingHeadroom, dependencies=[module_guard("taxes")])
def ceiling_headroom(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityRequired = None,
) -> CeilingHeadroom:
    """How much room is left before each active threshold of the configured tax package
    (REB-352 §1.4), on the year's collected and actual revenue. Open to every role,
    unlike `/fiscal`: this is revenue, not the fiscal estimate that only
    `get_fiscal_estimate` protects. The thresholds are those of the flat-rate regime: a
    legal entity under another regime gets an empty `thresholds`."""
    return AnalyticsService(session).ceiling_headroom(year, actor, legal_entity_id)


@router.get(
    "/ceilings/simulate",
    response_model=CeilingSimulation,
    dependencies=[module_guard("taxes")],
)
def simulate_ceiling(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    budgeted_hours: Annotated[Decimal | None, Query(ge=0)] = None,
    budgeted_value: Annotated[Decimal | None, Query(ge=0)] = None,
    hourly_rate: Annotated[Decimal | None, Query(ge=0)] = None,
    deal_id: Annotated[
        UUID | None,
        Query(
            description=(
                "A saved deal (REB-931): the addition is its value lines' share in `year`,"
                " instead of the raw estimate, which is then refused. In the contract cap the"
                " addition is its share in the contract anniversary year."
            )
        ),
    ] = None,
    contract_id: Annotated[
        UUID | None,
        Query(
            description=(
                "A contract with a cap on the client's share (REB-761): the same addition is also"
                " read against its cap, over the contract anniversary year that contains "
                "`expected_date`, in `contract_cap`."
            )
        ),
    ] = None,
    expected_date: Annotated[
        date | None,
        Query(description="The day the revenue arrives, YYYY-MM-DD; omitted, today."),
    ] = None,
    legal_entity_id: LegalEntityRequired = None,
) -> CeilingSimulation:
    """The «ci sta?» (does it fit?) simulator (REB-352 §1.4): the estimate of a deal not yet won,
    added to the actual revenue and re-evaluated against every active threshold, without
    saving anything. Needs `budgeted_value`, or `budgeted_hours` together with
    `hourly_rate`, or a saved deal's `deal_id` (never both): the deal's value lines give the
    share that falls in `year`. With `contract_id` it also answers on that contract's cap.

    The raw figures are `taxes`' alone; a saved deal needs `deals` and a contract's cap needs
    `contracts`, and each is refused with the sentence naming the module only when asked for
    (REB-978)."""
    if deal_id is not None:
        require_modules(settings, "deals")
    if contract_id is not None:
        require_modules(settings, "contracts")
    return AnalyticsService(session).simulate_ceiling(
        year,
        CeilingSimulationQuery(
            budgeted_hours=budgeted_hours,
            budgeted_value=budgeted_value,
            hourly_rate=hourly_rate,
            deal_id=deal_id,
            contract_id=contract_id,
            expected_date=expected_date,
        ),
        actor,
        legal_entity_id,
    )


@router.get("/days", response_model=DaysAnalysis, dependencies=[module_guard("contracts")])
def days_analysis(
    session: SessionDep,
    actor: ActorDep,
    date_from: Annotated[
        date | None, Query(alias="from", description="Start of the period, YYYY-MM-DD")
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="End of the period, YYYY-MM-DD")
    ] = None,
    legal_entity_id: LegalEntityFilter = None,
) -> DaysAnalysis:
    """The workdays of a period (REB-756): by month and by status, the actual rate
    against the price list by client and by contract, and the trend of days worked
    without the approval the contract requires. Both dates or neither
    (the current month). Open to every role, like the workdays."""
    return AnalyticsService(session).days_analysis(actor, date_from, date_to, legal_entity_id)


@router.get("/year", response_model=YearOverview, dependencies=[module_guard("taxes")])
def year_overview(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityRequired = None,
) -> YearOverview:
    """A legal entity's year as the accountant reads it (REB-757): revenue by
    regime and by tax treatment, and the register of issued invoices, with stamp duty and
    withholding-tax surcharge apart. The same universe as `annual_revenue`: `totali.taxable_amount`
    is the year's revenue. Open to every role, like the register."""
    return AnalyticsService(session).year_overview(year, actor, legal_entity_id)


@router.get(
    "/year/csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "The year as CSV"}},
    dependencies=[module_guard("taxes")],
)
def year_csv(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityRequired = None,
) -> Response:
    """The year's CSV for the accountant (REB-757): one row per invoice line,
    then the register, with the two totals. UTF-8 with BOM, semicolon and
    decimal comma, the format Italian Excel opens without conversions."""
    filename, content = AnalyticsService(session).build_year_csv(year, actor, legal_entity_id)
    return attachment_response(content, "text/csv; charset=utf-8", filename)
