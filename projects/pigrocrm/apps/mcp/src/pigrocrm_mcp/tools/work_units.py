"""Thin reads into `WorkUnitService`: the days a contract's ledger holds.

Reads only, and that is the whole of this module's surface. Recording a day, moving it
through its lifecycle and attaching an approval are a person's acts: an agent proposes a
day (`propose_day`) and a person confirms it (`accept_proposal`), and the four ledger
writes are refused to any agent credential (`AGENT_FORBIDDEN_ACTIONS`). What an assistant
may do is answer «which days did I work on this contract» and «what is the state of this
one», which is what these two do.
"""

from datetime import date
from typing import Any
from uuid import UUID

from pigrocrm.core.work_units.schemas import WorkUnitListQuery
from pigrocrm.core.work_units.service import WorkUnitService
from pigrocrm_mcp.context import McpContext


def get(context: McpContext, work_unit_id: str) -> dict[str, Any]:
    result = WorkUnitService(context.session).detail(UUID(work_unit_id), context.actor)
    return result.model_dump(mode="json")


def search(
    context: McpContext,
    *,
    contract_id: str | None,
    da: str | None,
    a: str | None,
    stato: str | None,
    azienda_id: UUID | None,
    limit: int,
) -> dict[str, Any]:
    query = WorkUnitListQuery(
        contract_id=UUID(contract_id) if contract_id else None,
        azienda_id=azienda_id,
        da=date.fromisoformat(da) if da else None,
        a=date.fromisoformat(a) if a else None,
        stato=stato,  # type: ignore[arg-type]
        limit=limit,
    )
    return WorkUnitService(context.session).list(query, context.actor).model_dump(mode="json")
