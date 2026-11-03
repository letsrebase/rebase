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
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
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
FromDate = Annotated[date, Query(alias="from", description="Inizio del periodo, YYYY-MM-DD")]
ToDate = Annotated[date, Query(alias="to", description="Fine del periodo, YYYY-MM-DD")]
# The sidebar's legal entity on a sum (REB-630, spec 2026-10-03 §1.9): one more predicate,
# omitted for «tutte». On the estimate and the ceilings it is the legal entity the figure is
# computed for: resolved alone on a one-legal entity space, required by name on a space with
# several, since a forfettario's coefficients and an SRL's arithmetic do not add.
LegalEntityFilter = Annotated[
    UUID | None,
    Query(
        description=(
            "Limita le cifre a un'azienda dello spazio; omesso, tutte le aziende sommate. "
            "La risposta lo riporta in `legal_entity_id`."
        )
    ),
]
LegalEntityRequired = Annotated[
    UUID | None,
    Query(
        description=(
            "L'azienda per cui calcolare: obbligatoria quando lo spazio ne ha più di una "
            "(una risposta 422 nomina `legal_entity_id`), altrimenti è l'unica e si può omettere."
        )
    ),
]


@router.get("/pnl", response_model=PeriodPnl)
def period_pnl(
    session: SessionDep,
    actor: ActorDep,
    date_from: FromDate,
    date_to: ToDate,
    customer_id: Annotated[UUID | None, Query()] = None,
    legal_entity_id: LegalEntityFilter = None,
    base: Annotated[
        RevenueBase,
        Query(
            description=(
                "Quale data colloca il ricavo di una fattura nel periodo: 'issue' "
                "(la data del documento, predefinita) oppure 'accrual' (il periodo di "
                "competenza dichiarato sulla fattura, con la data di emissione per chi non "
                "lo dichiara). Costi e ore restano attribuiti alla propria data."
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
    )


class BudgetParams(KeysetParams):
    date_from: date = Field(alias="from", description="Inizio del periodo, YYYY-MM-DD")
    date_to: date = Field(alias="to", description="Fine del periodo, YYYY-MM-DD")
    customer_id: UUID | None = None


@router.get("/budget", response_model=BudgetPage)
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
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityFilter = None,
    base: Annotated[
        CashBase,
        Query(
            description=(
                "In quale mese cade il denaro di ogni documento: 'accrual' (il periodo "
                "di competenza dichiarato sulla fattura o sulla proforma, con la data del "
                "documento per chi non lo dichiara; predefinito) oppure 'collection' (la data "
                "di incasso per le fatture pagate, la scadenza per quelle da incassare, la "
                "data del documento, o di creazione, per bozze e proforma). La stima "
                "fiscale resta sempre "
                "sull'incassato dell'anno."
            )
        ),
    ] = "accrual",
) -> EconomicOverview:
    """The dashboard's economic tab: the year as cash for everyone, plus the fiscal
    estimate on collected and projected revenue for an admin with a profile. No MCP
    tool, for the estimate's own reasons. `base` moves the charts and the cash cards
    between the two readings (ORB-133); the fiscal block stays on the money. In «tutte»
    on a space with several aziende the fiscal block is empty and the page asks
    `/fiscal` once per azienda (REB-630)."""
    return AnalyticsService(session).economic_overview(year, actor, base, legal_entity_id)


@router.get("/fiscal", response_model=FiscalEstimate)
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


@router.get("/backlog", response_model=UnbilledBacklog)
def backlog(session: SessionDep, actor: ActorDep) -> UnbilledBacklog:
    """No period parameter, deliberately: see `AnalyticsService.unbilled_backlog`.

    Placed beside the other analytics reads rather than under `/dashboard`, because the
    figure belongs to `AnalyticsService` and §3 forbids a dashboard module from owning
    one -- its euro value is a product of two columns.
    """
    return AnalyticsService(session).unbilled_backlog(actor)


@router.get("/ceilings", response_model=CeilingHeadroom)
def ceiling_headroom(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityRequired = None,
) -> CeilingHeadroom:
    """Quanto spazio resta prima di ciascuna soglia attiva del pacchetto fiscale
    configurato (REB-352 §1.4), sui ricavi incassati e reali dell'anno. Aperto a ogni
    ruolo, a differenza di `/fiscal`: è un ricavo, non la stima fiscale che protegge
    solo `get_fiscal_estimate`. Le soglie sono quelle del regime forfettario: un'azienda
    in un altro regime riceve `thresholds` vuoto."""
    return AnalyticsService(session).ceiling_headroom(year, actor, legal_entity_id)


@router.get("/ceilings/simulate", response_model=CeilingSimulation)
def simulate_ceiling(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    budgeted_hours: Annotated[Decimal | None, Query(ge=0)] = None,
    budgeted_value: Annotated[Decimal | None, Query(ge=0)] = None,
    hourly_rate: Annotated[Decimal | None, Query(ge=0)] = None,
    contract_id: Annotated[
        UUID | None,
        Query(
            description=(
                "Un contratto con un tetto sulla quota del cliente (REB-761): la stessa "
                "aggiunta è letta anche contro il suo tetto, sull'anno di anniversario "
                "del contratto che contiene `expected_date`, in `contract_cap`."
            )
        ),
    ] = None,
    expected_date: Annotated[
        date | None,
        Query(description="Il giorno in cui arriva il ricavo, YYYY-MM-DD; assente, oggi."),
    ] = None,
    legal_entity_id: LegalEntityRequired = None,
) -> CeilingSimulation:
    """Il simulatore "ci sta?" (REB-352 §1.4): la stima di un deal non ancora vinto,
    aggiunta ai ricavi reali e rivalutata su ogni soglia attiva, senza salvare
    nulla. Serve `budgeted_value`, oppure `budgeted_hours` insieme a
    `hourly_rate`. Con `contract_id` risponde anche sul tetto di quel contratto."""
    return AnalyticsService(session).simulate_ceiling(
        year,
        CeilingSimulationQuery(
            budgeted_hours=budgeted_hours,
            budgeted_value=budgeted_value,
            hourly_rate=hourly_rate,
            contract_id=contract_id,
            expected_date=expected_date,
        ),
        actor,
        legal_entity_id,
    )


@router.get("/days", response_model=DaysAnalysis)
def days_analysis(
    session: SessionDep,
    actor: ActorDep,
    date_from: Annotated[
        date | None, Query(alias="from", description="Inizio del periodo, YYYY-MM-DD")
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="Fine del periodo, YYYY-MM-DD")
    ] = None,
    legal_entity_id: LegalEntityFilter = None,
) -> DaysAnalysis:
    """Le giornate di un periodo (REB-756): per mese e per stato, la tariffa reale
    contro il listino per cliente e per contratto, e l'andamento delle giornate
    lavorate senza l'approvazione che il contratto chiede. Entrambe le date o nessuna
    (il mese corrente). Aperto a ogni ruolo, come le giornate."""
    return AnalyticsService(session).days_analysis(actor, date_from, date_to, legal_entity_id)


@router.get("/year", response_model=YearOverview)
def year_overview(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityRequired = None,
) -> YearOverview:
    """L'anno di un'azienda come lo legge il commercialista (REB-757): i ricavi per
    regime e per trattamento fiscale e il registro delle fatture emesse, con bollo e
    rivalsa a parte. Lo stesso universo di `annual_revenue`: `totali.taxable_amount` è il
    ricavo dell'anno. Aperto a ogni ruolo, come il registro."""
    return AnalyticsService(session).year_overview(year, actor, legal_entity_id)


@router.get(
    "/year/csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Il CSV dell'anno"}},
)
def year_csv(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: LegalEntityRequired = None,
) -> Response:
    """Il CSV dell'anno per il commercialista (REB-757): una riga per ogni riga di
    fattura e poi il registro, con i due totali. UTF-8 con BOM, punto e virgola e
    virgola decimale, il formato che Excel italiano apre senza conversioni."""
    filename, content = AnalyticsService(session).build_year_csv(year, actor, legal_entity_id)
    return attachment_response(content, "text/csv; charset=utf-8", filename)
