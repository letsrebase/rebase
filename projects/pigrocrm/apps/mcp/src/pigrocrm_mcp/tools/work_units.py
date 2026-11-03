"""Thin reads into `WorkUnitService`: the days a contract's ledger holds.

Reads only, and that is the whole of this module's surface. Recording a day, moving it
through its lifecycle and attaching an approval are a person's acts: an agent proposes a
day (`propose_day`) and a person confirms it (`accept_proposal`), and the four ledger
writes are refused to any agent credential (`FULL_ACCESS_ACTIONS`). What an assistant
may do is answer «which days did I work on this contract» and «what is the state of this
one», which is what these two do.
"""

from typing import Any
from uuid import UUID

from pigrocrm.core.work_units.schemas import WorkUnitListQuery, WorkUnitStatus
from pigrocrm.core.work_units.service import WorkUnitService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.params import iso_date, literal_arg


def get(context: McpContext, work_unit_id: str) -> dict[str, Any]:
    result = WorkUnitService(context.session).detail(UUID(work_unit_id), context.actor)
    return result.model_dump(mode="json")


def search(
    context: McpContext,
    *,
    contract_id: str | None,
    date_from: str | None,
    date_to: str | None,
    status: str | None,
    legal_entity_id: UUID | None,
    limit: int,
) -> dict[str, Any]:
    query = WorkUnitListQuery(
        contract_id=UUID(contract_id) if contract_id else None,
        legal_entity_id=legal_entity_id,
        date_from=iso_date(date_from, "date_from"),
        date_to=iso_date(date_to, "date_to"),
        status=literal_arg("status", status, WorkUnitStatus),
        limit=limit,
    )
    return WorkUnitService(context.session).list(query, context.actor).model_dump(mode="json")
