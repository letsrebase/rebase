from datetime import date
from typing import Any
from uuid import UUID

from pigrocrm.core.contracts.schemas import (
    ContractCreate,
    ContractListQuery,
    ContractProjectionQuery,
    RateCardCreate,
    RenewalAssumptionUpsert,
)
from pigrocrm.core.contracts.service import (
    ContractProjectionService,
    ContractService,
    RateCardService,
    RenewalAssumptionService,
)
from pigrocrm_mcp.context import McpContext


def create(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return (
        ContractService(context.session)
        .create(ContractCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def get(context: McpContext, contract_id: str) -> dict[str, Any]:
    return (
        ContractService(context.session)
        .get(UUID(contract_id), context.actor)
        .model_dump(mode="json")
    )


def concentration_cap(
    context: McpContext, contract_id: str, as_of: date | None, soglia: float | None
) -> dict[str, Any]:
    return (
        ContractService(context.session)
        .concentration_cap(UUID(contract_id), context.actor, as_of, soglia)
        .model_dump(mode="json")
    )


def search(context: McpContext, query: ContractListQuery) -> dict[str, Any]:
    page = ContractService(context.session).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "next_cursor": page.next_cursor,
    }


def create_rate_card(context: McpContext, contract_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return (
        RateCardService(context.session)
        .create(UUID(contract_id), RateCardCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def list_rate_cards(context: McpContext, contract_id: str) -> list[dict[str, Any]]:
    cards = RateCardService(context.session).list_for_contract(UUID(contract_id), context.actor)
    return [card.model_dump(mode="json") for card in cards]


def set_renewal_assumption(
    context: McpContext, contract_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    return (
        RenewalAssumptionService(context.session)
        .upsert(UUID(contract_id), RenewalAssumptionUpsert(**data), context.actor)
        .model_dump(mode="json")
    )


def get_renewal_assumption(context: McpContext, contract_id: str) -> dict[str, Any]:
    return (
        RenewalAssumptionService(context.session)
        .get(UUID(contract_id), context.actor)
        .model_dump(mode="json")
    )


def project_revenue(
    context: McpContext, contract_id: str, query: ContractProjectionQuery
) -> dict[str, Any]:
    return (
        ContractProjectionService(context.session)
        .project(UUID(contract_id), query, context.actor)
        .model_dump(mode="json")
    )
