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

from fastapi import APIRouter, Query

from pigrocrm.core.analytics.schemas import (
    BudgetPage,
    BudgetQuery,
    CashBase,
    CeilingHeadroom,
    CeilingSimulation,
    CeilingSimulationQuery,
    EconomicOverview,
    FiscalEstimate,
    PeriodPnl,
    PeriodPnlQuery,
    RevenueBase,
    UnbilledBacklog,
)
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/analytics", tags=["analytics"], responses=PROBLEM_RESPONSES)

# `from` and `to` on the wire, as spec §11 writes them; `da`/`a` as the parameter names,
# because `from` is a Python keyword. The alias is what keeps both true, and it is
# recorded here rather than silently diverging from the spec.
FromDate = Annotated[date, Query(alias="from", description="Inizio del periodo, YYYY-MM-DD")]
ToDate = Annotated[date, Query(alias="to", description="Fine del periodo, YYYY-MM-DD")]
# The sidebar's azienda on a sum (REB-630, spec 2026-10-03 §1.9): one more predicate,
# omitted for «tutte». On the estimate and the ceilings it is the azienda the figure is
# computed for: resolved alone on a one-azienda space, required by name on a space with
# several, since a forfettario's coefficients and an SRL's arithmetic do not add.
AziendaFilter = Annotated[
    UUID | None,
    Query(
        description=(
            "Limita le cifre a un'azienda dello spazio; omesso, tutte le aziende sommate. "
            "La risposta lo riporta in `azienda_id`."
        )
    ),
]
AziendaRequired = Annotated[
    UUID | None,
    Query(
        description=(
            "L'azienda per cui calcolare: obbligatoria quando lo spazio ne ha più di una "
            "(una risposta 422 nomina `azienda_id`), altrimenti è l'unica e si può omettere."
        )
    ),
]


@router.get("/pnl", response_model=PeriodPnl)
def period_pnl(
    session: SessionDep,
    actor: ActorDep,
    da: FromDate,
    a: ToDate,
    customer_id: Annotated[UUID | None, Query()] = None,
    azienda_id: AziendaFilter = None,
    base: Annotated[
        RevenueBase,
        Query(
            description=(
                "Quale data colloca il ricavo di una fattura nel periodo: 'emissione' "
                "(la data del documento, predefinita) oppure 'competenza' (il periodo di "
                "competenza dichiarato sulla fattura, con la data di emissione per chi non "
                "lo dichiara). Costi e ore restano attribuiti alla propria data."
            )
        ),
    ] = "emissione",
) -> PeriodPnl:
    """Two columns, closed deals and deals in progress. There is deliberately no combined
    total: adding a finished job's margin to a half-done one produces a figure that is
    neither. `base` picks which of the two readings of revenue the report gives
    (ORB-61); everything else about it is the same."""
    return AnalyticsService(session).period_pnl(
        PeriodPnlQuery(da=da, a=a, customer_id=customer_id, base=base, azienda_id=azienda_id),
        actor,
    )


@router.get("/budget", response_model=BudgetPage)
def budget_vs_actual(
    session: SessionDep,
    actor: ActorDep,
    da: FromDate,
    a: ToDate,
    customer_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> BudgetPage:
    """The period filter is mandatory and the list is paginated from the first commit
    (residual B3): a margins view is by its nature a list of deals, so unbounded growth
    stops being invisible here."""
    return AnalyticsService(session).budget_vs_actual(
        BudgetQuery(da=da, a=a, customer_id=customer_id, limit=limit, cursor=cursor), actor
    )


@router.get("/overview", response_model=EconomicOverview)
def economic_overview(
    session: SessionDep,
    actor: ActorDep,
    anno: Annotated[int, Query(ge=2000, le=2200)],
    azienda_id: AziendaFilter = None,
    base: Annotated[
        CashBase,
        Query(
            description=(
                "In quale mese cade il denaro di ogni documento: 'competenza' (il periodo "
                "di competenza dichiarato sulla fattura o sulla proforma, con la data del "
                "documento per chi non lo dichiara; predefinito) oppure 'incasso' (la data "
                "di incasso per le fatture pagate, la scadenza per quelle da incassare, la "
                "data del documento, o di creazione, per bozze e proforma). La stima "
                "fiscale resta sempre "
                "sull'incassato dell'anno."
            )
        ),
    ] = "competenza",
) -> EconomicOverview:
    """The dashboard's economic tab: the year as cash for everyone, plus the fiscal
    estimate on collected and projected revenue for an admin with a profile. No MCP
    tool, for the estimate's own reasons. `base` moves the charts and the cash cards
    between the two readings (ORB-133); the fiscal block stays on the money. In «tutte»
    on a space with several aziende the fiscal block is empty and the page asks
    `/fiscal` once per azienda (REB-630)."""
    return AnalyticsService(session).economic_overview(anno, actor, base, azienda_id)


@router.get("/fiscal", response_model=FiscalEstimate)
def fiscal_estimate(
    session: SessionDep,
    actor: ActorDep,
    anno: Annotated[int, Query(ge=2000, le=2200)],
    azienda_id: AziendaRequired = None,
) -> FiscalEstimate:
    """`admin` -- enforced by the service, not here. A router containing an authorisation
    `if` is a router the MCP adapter cannot reuse, and this figure has no MCP tool at all
    (§11's exclusion list), so the check has to live where both adapters share it."""
    return AnalyticsService(session).get_fiscal_estimate(anno, actor, azienda_id=azienda_id)


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
    anno: Annotated[int, Query(ge=2000, le=2200)],
    azienda_id: AziendaRequired = None,
) -> CeilingHeadroom:
    """Quanto spazio resta prima di ciascuna soglia attiva del pacchetto fiscale
    configurato (REB-352 §1.4), sui ricavi incassati e reali dell'anno. Aperto a ogni
    ruolo, a differenza di `/fiscal`: è un ricavo, non la stima fiscale che protegge
    solo `get_fiscal_estimate`."""
    return AnalyticsService(session).ceiling_headroom(anno, actor, azienda_id)


@router.get("/ceilings/simulate", response_model=CeilingSimulation)
def simulate_ceiling(
    session: SessionDep,
    actor: ActorDep,
    anno: Annotated[int, Query(ge=2000, le=2200)],
    ore_preventivate: Annotated[Decimal | None, Query(ge=0)] = None,
    valore_preventivato: Annotated[Decimal | None, Query(ge=0)] = None,
    tariffa_oraria: Annotated[Decimal | None, Query(ge=0)] = None,
    azienda_id: AziendaRequired = None,
) -> CeilingSimulation:
    """Il simulatore "ci sta?" (REB-352 §1.4): la stima di un deal non ancora vinto,
    aggiunta ai ricavi reali e rivalutata su ogni soglia attiva, senza salvare
    nulla. Serve `valore_preventivato`, oppure `ore_preventivate` insieme a
    `tariffa_oraria`."""
    return AnalyticsService(session).simulate_ceiling(
        anno,
        CeilingSimulationQuery(
            ore_preventivate=ore_preventivate,
            valore_preventivato=valore_preventivato,
            tariffa_oraria=tariffa_oraria,
        ),
        actor,
        azienda_id,
    )
