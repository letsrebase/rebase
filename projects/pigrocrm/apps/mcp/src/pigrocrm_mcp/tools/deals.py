from typing import Any
from uuid import UUID

from pigrocrm.core.deals.schemas import DealCreate, DealListQuery, DealUpdate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.changes import refuse_empty, report


def create(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        DealService(context.session)
        .create(DealCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def update(context: McpContext, deal_id: str, data: dict[str, Any]) -> dict[str, Any]:
    service = DealService(context.session)
    identifier = UUID(deal_id)
    changes = DealUpdate(**data)
    refuse_empty(data)
    before = service.get(identifier, context.actor)
    return report(before, service.update(identifier, changes, context.actor))


def get(context: McpContext, deal_id: str) -> dict[str, Any]:
    return DealService(context.session).get(UUID(deal_id), context.actor).model_dump(mode="json")


def search(context: McpContext, query: DealListQuery) -> dict[str, Any]:
    page = DealService(context.session).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        # Already a string since slice 6 (an opaque `(sort value, id)` cursor), so no
        # `str()`: keeping one here would suggest the value is still a UUID being
        # rendered, which is exactly the assumption this change removes.
        "next_cursor": page.next_cursor,
    }


def move(
    context: McpContext, deal_id: str, stage_id: str | None, pipeline_stage_id: str | None
) -> dict[str, Any]:
    """`stage_id` is the tool's name for the destination and `pipeline_stage_id` the deal's
    own field for it (REB-808): an agent that has just read a deal says the second."""
    given = {value for value in (stage_id, pipeline_stage_id) if value}
    if len(given) > 1:
        raise ValidationFailed(
            "input",
            "stage_id",
            "`stage_id` e `pipeline_stage_id` indicano due stati diversi",
            expected="uno solo dei due, o lo stesso identificativo in entrambi",
        )
    if not given:
        raise ValidationFailed(
            "input",
            "stage_id",
            "campo obbligatorio mancante",
            expected=(
                "l'identificativo dello stato di destinazione, in `stage_id` o "
                "`pipeline_stage_id`; gli stati sono in `list_pipeline_stages`"
            ),
        )
    (destination,) = given
    result = DealService(context.session).move_stage(
        UUID(deal_id), UUID(destination), context.actor
    )
    return result.model_dump(mode="json")


def archive(context: McpContext, deal_id: str) -> dict[str, str]:
    DealService(context.session).soft_delete(UUID(deal_id), context.actor)
    return {"status": "archiviato", "deal_id": deal_id}


def restore(context: McpContext, deal_id: str) -> dict[str, Any]:
    return (
        DealService(context.session).restore(UUID(deal_id), context.actor).model_dump(mode="json")
    )
