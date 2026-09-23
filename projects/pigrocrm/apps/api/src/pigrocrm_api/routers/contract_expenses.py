from uuid import UUID

from fastapi import APIRouter, status

from pigrocrm.core.contract_expenses.schemas import (
    ContractExpenseCreate,
    ContractExpenseRead,
    ContractExpenseUpdate,
)
from pigrocrm.core.contract_expenses.service import ContractExpenseService
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/contracts", tags=["contract-expenses"], responses=PROBLEM_RESPONSES)


@router.post(
    "/{contract_id}/expenses",
    response_model=ContractExpenseRead,
    status_code=status.HTTP_201_CREATED,
)
def create(
    contract_id: UUID, data: ContractExpenseCreate, session: SessionDep, actor: ActorDep
) -> ContractExpenseRead:
    return ContractExpenseService(session).create(contract_id, data, actor)


@router.get("/{contract_id}/expenses", response_model=list[ContractExpenseRead])
def list_expenses(
    contract_id: UUID, session: SessionDep, actor: ActorDep
) -> list[ContractExpenseRead]:
    return ContractExpenseService(session).list_for_contract(contract_id, actor)


@router.patch("/{contract_id}/expenses/{expense_id}", response_model=ContractExpenseRead)
def update(
    contract_id: UUID,
    expense_id: UUID,
    data: ContractExpenseUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> ContractExpenseRead:
    return ContractExpenseService(session).update(contract_id, expense_id, data, actor)
