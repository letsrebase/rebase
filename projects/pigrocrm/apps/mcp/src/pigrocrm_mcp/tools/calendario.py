"""Thin calls into `AttivitaService` and `CalendarService`, like every other tool
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

from pigrocrm.core.attivita.schemas import AttivitaCreate, AttivitaListQuery, AttivitaUpdate
from pigrocrm.core.attivita.service import AttivitaService
from pigrocrm.core.calendario.service import CalendarService
from pigrocrm_mcp.context import McpContext


def _attivita(context: McpContext) -> AttivitaService:
    return AttivitaService(context.session)


def create(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    result = _attivita(context).create(AttivitaCreate(**data), context.actor)
    return result.model_dump(mode="json")


def update(context: McpContext, attivita_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    result = _attivita(context).update(UUID(attivita_id), AttivitaUpdate(**changes), context.actor)
    return result.model_dump(mode="json")


def get(context: McpContext, attivita_id: str) -> dict[str, Any]:
    return _attivita(context).get(UUID(attivita_id), context.actor).model_dump(mode="json")


def complete(context: McpContext, attivita_id: str) -> dict[str, Any]:
    result = _attivita(context).complete(UUID(attivita_id), context.actor)
    return result.model_dump(mode="json")


def cancel(context: McpContext, attivita_id: str) -> dict[str, Any]:
    result = _attivita(context).cancel(UUID(attivita_id), context.actor)
    return result.model_dump(mode="json")


def reopen(context: McpContext, attivita_id: str) -> dict[str, Any]:
    result = _attivita(context).reopen(UUID(attivita_id), context.actor)
    return result.model_dump(mode="json")


def archive(context: McpContext, attivita_id: str) -> dict[str, str]:
    _attivita(context).soft_delete(UUID(attivita_id), context.actor)
    return {"status": "archiviata", "attivita_id": attivita_id}


def restore(context: McpContext, attivita_id: str) -> dict[str, Any]:
    result = _attivita(context).restore(UUID(attivita_id), context.actor)
    return result.model_dump(mode="json")


def month(
    context: McpContext, mese: str, tutti: bool, azienda_id: UUID | None = None
) -> dict[str, Any]:
    """One month, hours included. `tutti` reads the whole space instead of the token
    owner's own days -- the same switch the web route carries, and the same default: an
    `mcp` actor holds the id of the token's owner (slice 1 §9), so «my month» is that
    person's."""
    user_id = None if tutti else context.actor.id
    result = CalendarService(context.session).month(
        mese, context.actor, user_id=user_id, azienda_id=azienda_id
    )
    return result.model_dump(mode="json")


# `list` last in the module for the same habit the services keep, even though a module
# namespace does not have the class-body hazard: the reader who learned the rule in
# `people/service.py` should not have to work out that it does not apply here.
def search(context: McpContext, query: dict[str, Any]) -> dict[str, Any]:
    page = _attivita(context).list(AttivitaListQuery(**query), context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "next_cursor": page.next_cursor,
    }
