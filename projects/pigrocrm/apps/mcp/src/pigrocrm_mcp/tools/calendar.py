"""Thin calls into `TaskService` and `CalendarService`, like every other tool
module here.

Everything in this file is on the **default** surface: none of it consumes a number,
touches the fiscal register or rewrites a rate, so none of it belongs to the forbidden
operations of slice 3 §11. A commitment created in error is archived; one that stopped
mattering is cancelled, which is a different statement and the reason the state machine
has three states rather than two.

The calendar read is here rather than left to the web app for the reason the spec gives
(slice 10 §6): «what falls due this week» is exactly the question somebody asks an
agent, and without this tool the agent would have to rebuild the month from three
separate lists.
"""

from typing import Any
from uuid import UUID

from pigrocrm.core.calendar.service import CalendarService
from pigrocrm.core.tasks.schemas import (
    TaskCreate,
    TaskListQuery,
    TaskTodayQuery,
    TaskUpdate,
)
from pigrocrm.core.tasks.service import TaskService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.changes import refuse_empty, report


def _task(context: McpContext) -> TaskService:
    return TaskService(context.session)


NONE = "none"


def assignee_of_default(context: McpContext, assigned_to: str | None) -> UUID | None:
    """Who a new attività goes to: the id given, nobody for `"none"`, and, when the
    agent says nothing, the user who owns the token (`None` still for a token with no
    person behind it). Agents almost never pass an id, and an unassigned attività would
    be nobody's to-do."""
    if assigned_to is None or assigned_to == "":
        return context.actor.id
    if assigned_to == NONE:
        return None
    return UUID(assigned_to)


def create(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    result = _task(context).create(TaskCreate(**data), context.actor)
    return result.model_dump(mode="json")


def update(context: McpContext, task_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    service = _task(context)
    identifier = UUID(task_id)
    parsed = TaskUpdate(**changes)
    refuse_empty(changes)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, parsed, context.actor))


def get(context: McpContext, task_id: str) -> dict[str, Any]:
    return _task(context).get(UUID(task_id), context.actor).model_dump(mode="json")


def complete(context: McpContext, task_id: str) -> dict[str, Any]:
    result = _task(context).complete(UUID(task_id), context.actor)
    return result.model_dump(mode="json")


def cancel(context: McpContext, task_id: str) -> dict[str, Any]:
    result = _task(context).cancel(UUID(task_id), context.actor)
    return result.model_dump(mode="json")


def reopen(context: McpContext, task_id: str) -> dict[str, Any]:
    result = _task(context).reopen(UUID(task_id), context.actor)
    return result.model_dump(mode="json")


def archive(context: McpContext, task_id: str) -> dict[str, str]:
    _task(context).soft_delete(UUID(task_id), context.actor)
    return {"status": "archived", "task_id": task_id}


def restore(context: McpContext, task_id: str) -> dict[str, Any]:
    result = _task(context).restore(UUID(task_id), context.actor)
    return result.model_dump(mode="json")


def today(context: McpContext, query: dict[str, Any]) -> dict[str, Any]:
    """The Oggi panel: overdue, due today, the next seven days, and the undated."""
    result = _task(context).today(context.actor, TaskTodayQuery(**query))
    return result.model_dump(mode="json")


def month(
    context: McpContext, month: str, all: bool, legal_entity_id: UUID | None = None
) -> dict[str, Any]:
    """One month, hours included. `all` reads the whole space instead of the token
    owner's own days -- the same switch the web route carries, and the same default: an
    `mcp` actor holds the id of the token's owner (slice 1 §9), so «my month» is that
    person's."""
    user_id = None if all else context.actor.id
    result = CalendarService(context.session).month(
        month, context.actor, user_id=user_id, legal_entity_id=legal_entity_id
    )
    return result.model_dump(mode="json")


# `list` last in the module for the same habit the services keep, even though a module
# namespace does not have the class-body hazard: the reader who learned the rule in
# `people/service.py` should not have to work out that it does not apply here.
def search(context: McpContext, query: dict[str, Any]) -> dict[str, Any]:
    page = _task(context).list(TaskListQuery(**query), context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "next_cursor": page.next_cursor,
    }
