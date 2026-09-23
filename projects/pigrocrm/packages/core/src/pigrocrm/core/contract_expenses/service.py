"""The only writer of `contract_expenses` (and, through the trigger,
`rimborsabile` -- see `triggers.py`)."""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contract_expenses.repository import ContractExpenseRepository
from pigrocrm.core.contract_expenses.schemas import (
    ContractExpenseCreate,
    ContractExpenseRead,
    ContractExpenseUpdate,
)
from pigrocrm.core.contracts.repository import ContractRepository
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.timetracking.categories import CostCategoryService

ENTITY = "contract_expense"


def _check_riferimento_matches_pre_autorizzata(
    *, pre_autorizzata: bool, riferimento_autorizzazione: str | None
) -> None:
    """The friendlier pre-database validation ahead of
    `ck_contract_expenses_riferimento_matches_pre_autorizzata`'s own
    `IntegrityError` -- `ContractExpenseCreate` checks the same pair on the
    caller's own input; this is what `update` runs against the *merged* row,
    since a patch may touch either field alone."""
    if pre_autorizzata and not riferimento_autorizzazione:
        raise ValidationFailed(
            ENTITY,
            "riferimento_autorizzazione",
            "obbligatorio quando pre_autorizzata è vero",
            expected="un riferimento non vuoto",
        )
    if not pre_autorizzata and riferimento_autorizzazione:
        raise ValidationFailed(
            ENTITY,
            "riferimento_autorizzazione",
            "va lasciato vuoto quando pre_autorizzata è falso",
            expected="nessun valore",
        )


class ContractExpenseService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = ContractExpenseRepository(session)
        self.contracts = ContractRepository(session)
        self.categories = CostCategoryService(session)
        self.documents = DocumentRepository(session)
        self.activities = ActivityService(session)

    def _check_refs(self, document_id: UUID | None) -> None:
        if document_id is not None and self.documents.get(document_id) is None:
            raise NotFound("document", document_id)

    def create(
        self, contract_id: UUID, data: ContractExpenseCreate, actor: Actor
    ) -> ContractExpenseRead:
        actor.require_write("create_contract_expense")
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)
        self.categories.require_active(data.category_id)
        self._check_refs(data.document_id)

        expense = self.repo.add(
            ContractExpense(
                contract_id=contract_id,
                category_id=data.category_id,
                data=data.data,
                importo=data.importo,
                descrizione=data.descrizione,
                pre_autorizzata=data.pre_autorizzata,
                riferimento_autorizzazione=data.riferimento_autorizzazione,
                document_id=data.document_id,
            )
        )
        # `contract_expense_set_rimborsabile` writes `rimborsabile` in flight
        # (spec §7) -- `expire_on_commit=False` (`db/session.py`) means the ORM
        # object otherwise keeps showing the column's Python-side default rather
        # than what the trigger actually computed. A refresh is what makes that
        # computed value observable to a caller of this service, the same reason
        # `WorkUnitService.create` refreshes after its own trigger-rewritten insert.
        self.session.refresh(expense)
        self.activities.record(
            "contract",
            contract_id,
            "expense_added",
            actor,
            {"importo": str(expense.importo), "rimborsabile": expense.rimborsabile},
        )
        self.session.commit()
        return ContractExpenseRead.model_validate(expense)

    def update(
        self, contract_id: UUID, expense_id: UUID, data: ContractExpenseUpdate, actor: Actor
    ) -> ContractExpenseRead:
        actor.require_write("update_contract_expense")
        expense = self.repo.get(expense_id)
        if expense is None or expense.contract_id != contract_id:
            raise NotFound(ENTITY, expense_id)

        changes: dict[str, Any] = supplied_changes(data)
        reject_cleared_columns(ENTITY, ContractExpense, changes)
        if changes.get("category_id") is not None:
            self.categories.require_active(changes["category_id"])
        self._check_refs(changes.get("document_id"))

        merged_pre_autorizzata = changes.get("pre_autorizzata", expense.pre_autorizzata)
        merged_riferimento = changes.get(
            "riferimento_autorizzazione", expense.riferimento_autorizzazione
        )
        _check_riferimento_matches_pre_autorizzata(
            pre_autorizzata=merged_pre_autorizzata,
            riferimento_autorizzazione=merged_riferimento,
        )

        for key, value in changes.items():
            setattr(expense, key, value)
        self.session.flush()
        self.session.refresh(expense)  # see create()'s own comment
        self.activities.record(
            "contract", contract_id, "expense_updated", actor, {"changed": sorted(changes)}
        )
        self.session.commit()
        return ContractExpenseRead.model_validate(expense)

    # `list_for_contract` stays the last method in this class -- the unconditional
    # project rule (see `ContractRepository`): a `def list(...)` would rebind
    # `list` in the class namespace, and Python 3.13 evaluates annotations eagerly.
    def list_for_contract(self, contract_id: UUID, actor: Actor) -> list[ContractExpenseRead]:
        if self.contracts.get(contract_id) is None:
            raise NotFound("contract", contract_id)
        return [
            ContractExpenseRead.model_validate(expense)
            for expense in self.repo.list_for_contract(contract_id)
        ]


__all__ = ["ENTITY", "ContractExpenseService"]
