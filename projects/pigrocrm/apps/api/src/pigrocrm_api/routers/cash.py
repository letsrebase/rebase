"""«Cassa»: the cash schedule, the payables mirror and the phone Home's money figures
(REB-1025, spec 2026-11-09, due items and the cash schedule, § 4).

Core, behind no module guard: the schedule and the payables answer in every space. With the
module `cash` off they answer without balances; who may not read the bank accounts gets none
either (`CashScheduleService` decides, the route holds no `if`). The space's modules and its
profile are the request's own settings.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from pigrocrm.core.due_items.schedule import CashScheduleService
from pigrocrm.core.due_items.schedule_schemas import (
    DEFAULT_HORIZON_WEEKS,
    CashSchedule,
    CashScheduleQuery,
    CashSummary,
    PayablesDashboard,
)
from pigrocrm.core.modules import enabled_modules
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import LEGAL_ENTITY_QUERY_DESCRIPTION

router = APIRouter(
    prefix="/api/cash",
    tags=["cash"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)

LegalEntityQuery = Annotated[UUID | None, Query(description=LEGAL_ENTITY_QUERY_DESCRIPTION)]


@router.get("/schedule", response_model=CashSchedule)
def schedule(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    weeks: Annotated[
        int,
        Query(
            description=(
                "How many weeks ahead, from the Monday of the current one: one of "
                "`horizon_options` of the answer (4, 13 or 26)"
            )
        ),
    ] = DEFAULT_HORIZON_WEEKS,
    legal_entity_id: LegalEntityQuery = None,
) -> CashSchedule:
    """When money moves: week by week what comes in and what goes out, forecast apart from
    expected, the projected balance at each week's end and what is late. Balances only for an
    administrator and with the `cash` module on."""
    return CashScheduleService(session).get_schedule(
        CashScheduleQuery(weeks=weeks, legal_entity_id=legal_entity_id),
        actor,
        modules=enabled_modules(settings),
        profile=settings.space_profile,
    )


@router.get("/payables", response_model=PayablesDashboard)
def payables(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    legal_entity_id: LegalEntityQuery = None,
) -> PayablesDashboard:
    """What the space owes, in the six bands of the receivables, with the late rows. Zeros for
    a solo space."""
    return CashScheduleService(session).get_payables(
        legal_entity_id,
        actor,
        modules=enabled_modules(settings),
        profile=settings.space_profile,
    )


@router.get("/summary", response_model=CashSummary)
def summary(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    legal_entity_id: LegalEntityQuery = None,
) -> CashSummary:
    """The phone Home's figures: today's balance, what is owed each way and how much of it is
    late, the next seven days and the worst alert."""
    return CashScheduleService(session).get_summary(
        legal_entity_id,
        actor,
        modules=enabled_modules(settings),
        profile=settings.space_profile,
    )
