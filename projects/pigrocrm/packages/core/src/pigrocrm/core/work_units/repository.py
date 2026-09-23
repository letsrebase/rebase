from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.work_units.models import Approval, WorkUnit, WorkUnitTransition


class WorkUnitRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, work_unit_id: UUID) -> WorkUnit | None:
        return self.session.get(WorkUnit, work_unit_id)

    def add(self, work_unit: WorkUnit) -> WorkUnit:
        self.session.add(work_unit)
        self.session.flush()
        return work_unit

    def unbilled_for_contract(
        self, contract_id: UUID, *, stato: str = "lavorato"
    ) -> list[WorkUnit]:
        """Every day in `stato` with no invoice line yet, oldest first -- the same
        shape `won_with_unbilled_hours_predicate` (`timetracking/repository.py`)
        already gives `TimeEntry` for its own unbilled figure (spec §9's citation)."""
        stmt = (
            select(WorkUnit)
            .where(
                WorkUnit.contract_id == contract_id,
                WorkUnit.stato == stato,
                WorkUnit.invoice_line_id.is_(None),
            )
            .order_by(WorkUnit.data.asc(), WorkUnit.id.asc())
        )
        return list(self.session.execute(stmt).scalars())

    # `list` stays the last method in this class -- the unconditional project rule
    # (see `ContractRepository`): a later `def list(...)` rebinds `list` in the class
    # namespace, and Python 3.13 evaluates annotations eagerly.
    def list_for_contract(self, contract_id: UUID) -> list[WorkUnit]:
        stmt = (
            select(WorkUnit)
            .where(WorkUnit.contract_id == contract_id)
            .order_by(WorkUnit.data.asc(), WorkUnit.id.asc())
        )
        return list(self.session.execute(stmt).scalars())


class WorkUnitTransitionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def for_work_unit(self, work_unit_id: UUID) -> list[WorkUnitTransition]:
        """Every transition of one day, in the real order they happened -- `seq`, not
        `created_at` (models.py's own reasoning: `clock_timestamp()` can tie within
        one statement, `nextval()` cannot)."""
        stmt = (
            select(WorkUnitTransition)
            .where(WorkUnitTransition.work_unit_id == work_unit_id)
            .order_by(WorkUnitTransition.seq.asc())
        )
        return list(self.session.execute(stmt).scalars())


class ApprovalRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, approval_id: UUID) -> Approval | None:
        return self.session.get(Approval, approval_id)

    def add(self, approval: Approval) -> Approval:
        self.session.add(approval)
        self.session.flush()
        return approval
