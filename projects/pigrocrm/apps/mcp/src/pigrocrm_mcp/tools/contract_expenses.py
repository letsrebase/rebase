from typing import Any
from uuid import UUID

from pigrocrm.core.contract_expenses.schemas import ContractExpenseCreate, ContractExpenseUpdate
from pigrocrm.core.contract_expenses.service import ContractExpenseService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.changes import pick, refuse_empty, report


def create(context: McpContext, contract_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return (
        ContractExpenseService(context.session)
        .create(UUID(contract_id), ContractExpenseCreate(**data), context.actor)
        .model_dump(mode="json")
    )


def update(
    context: McpContext, contract_id: str, expense_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    service = ContractExpenseService(context.session)
    contract, expense = UUID(contract_id), UUID(expense_id)
    changes = ContractExpenseUpdate(**data)
    refuse_empty(data)
    before = pick(service.list_for_contract(contract, context.actor), expense, "contract_expense")
    return report(before, service.update(contract, expense, changes, context.actor))


def list_for_contract(context: McpContext, contract_id: str) -> list[dict[str, Any]]:
    expenses = ContractExpenseService(context.session).list_for_contract(
        UUID(contract_id), context.actor
    )
    return [expense.model_dump(mode="json") for expense in expenses]
