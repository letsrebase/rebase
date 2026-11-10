"""The recurrence rules of money going out (REB-1022, due items design § 1.2).

Each rule forecasts one due item out for every due date; the purchase of that period replaces
it. Authorisation and the forecasts are the service's; a router that holds an `if` for them is
one the MCP adapter cannot reuse.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pigrocrm.core.due_items.recurring import RecurringExpenseService
from pigrocrm.core.due_items.recurring_schemas import (
    RecurringExpenseCreate,
    RecurringExpenseRead,
)
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import LEGAL_ENTITY_QUERY_DESCRIPTION

router = APIRouter(
    prefix="/api/recurring-expenses",
    tags=["recurring-expenses"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("purchases")],
)


@router.get("", response_model=list[RecurringExpenseRead])
def list_recurring_expenses(
    session: SessionDep,
    actor: ActorDep,
    include_stopped: Annotated[bool, Query(description="Also the rules that were stopped")] = False,
    legal_entity_id: Annotated[
        UUID | None, Query(description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    ] = None,
) -> list[RecurringExpenseRead]:
    """The recurrence rules, the active ones unless `include_stopped`."""
    return RecurringExpenseService(session).list_recurring_expenses(
        actor, include_stopped=include_stopped, legal_entity_id=legal_entity_id
    )


@router.post("", response_model=RecurringExpenseRead, status_code=status.HTTP_201_CREATED)
def create_recurring_expense(
    data: RecurringExpenseCreate, session: SessionDep, actor: ActorDep
) -> RecurringExpenseRead:
    """A supplier that bills the same amount every cadence: one forecast out is written for
    each due date, and a purchase of that period from the supplier replaces it."""
    return RecurringExpenseService(session).create_recurring_expense(data, actor)


@router.post("/{rule_id}/stop", response_model=RecurringExpenseRead)
def stop_recurring_expense(
    rule_id: UUID, session: SessionDep, actor: ActorDep
) -> RecurringExpenseRead:
    """Ends the rule: its forecasts are dropped. Nothing is deleted."""
    return RecurringExpenseService(session).stop_recurring_expense(rule_id, actor)
