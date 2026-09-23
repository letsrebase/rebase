from typing import Any
from uuid import UUID

from pigrocrm.core.contract_expenses.schemas import ContractExpenseCreate, ContractExpenseUpdate
from pigrocrm.core.contract_expenses.service import ContractExpenseService
from pigrocrm_mcp.context import McpContext


def create(context: McpContext, contract_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return (
        ContractExpenseService(context.session)
        .create(UUID(contract_id), ContractExpenseCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def update(
    context: McpContext, contract_id: str, expense_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    return (
        ContractExpenseService(context.session)
        .update(UUID(contract_id), UUID(expense_id), ContractExpenseUpdate(**data), context.actor)
        .model_dump(mode="json")
    )


def list_for_contract(context: McpContext, contract_id: str) -> list[dict[str, Any]]:
    expenses = ContractExpenseService(context.session).list_for_contract(
        UUID(contract_id), context.actor
    )
    return [expense.model_dump(mode="json") for expense in expenses]
