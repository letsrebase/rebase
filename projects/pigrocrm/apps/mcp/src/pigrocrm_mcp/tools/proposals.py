from typing import Any
from uuid import UUID

from pigrocrm.core.proposals.schemas import (
    ProposalCreate,
    ProposalListQuery,
)
from pigrocrm.core.proposals.service import ProposalService
from pigrocrm_mcp.context import McpContext


def propose(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        ProposalService(context.session)
        .create(ProposalCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def get(context: McpContext, proposal_id: str) -> dict[str, Any]:
    return (
        ProposalService(context.session)
        .get(UUID(proposal_id), context.actor)
        .model_dump(mode="json")
    )


def search(context: McpContext, query: ProposalListQuery) -> dict[str, Any]:
    page = ProposalService(context.session).list(query, context.actor)
    return {"items": [item.model_dump(mode="json") for item in page.items]}
