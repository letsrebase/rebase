from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.contract_expenses.models import ContractExpense


class ContractExpenseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, expense_id: UUID) -> ContractExpense | None:
        return self.session.get(ContractExpense, expense_id)

    def add(self, expense: ContractExpense) -> ContractExpense:
        self.session.add(expense)
        self.session.flush()
        return expense

    def unbilled_for_contract(self, contract_id: UUID) -> list[ContractExpense]:
        """Every reimbursable expense of a contract with no invoice line yet, oldest
        first -- the same shape `WorkUnitRepository.unbilled_for_contract` gives a
        day, and `won_with_unbilled_hours_predicate` (`timetracking/repository.py`)
        gives an `TimeEntry` (spec §9's citation, mirrored by §7's own Done-when)."""
        stmt = (
            select(ContractExpense)
            .where(
                ContractExpense.contract_id == contract_id,
                ContractExpense.rimborsabile.is_(True),
                ContractExpense.invoice_line_id.is_(None),
            )
            .order_by(ContractExpense.data.asc(), ContractExpense.id.asc())
        )
        return list(self.session.execute(stmt).scalars())

    # `list` stays the last method in this class -- the unconditional project rule
    # (see `ContractRepository`): a later `def list(...)` rebinds `list` in the
    # class namespace, and Python 3.13 evaluates annotations eagerly.
    def list_for_contract(self, contract_id: UUID) -> list[ContractExpense]:
        stmt = (
            select(ContractExpense)
            .where(ContractExpense.contract_id == contract_id)
            .order_by(ContractExpense.data.asc(), ContractExpense.id.asc())
        )
        return list(self.session.execute(stmt).scalars())
