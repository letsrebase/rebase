from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.work_units.models import Approval, WorkUnit, WorkUnitTransition
from pigrocrm.core.work_units.schemas import WorkUnitListQuery


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

    def labelled(self, work_unit_id: UUID) -> tuple[WorkUnit, Contract, Customer] | None:
        """A day with its contract and customer, in one read, or `None`. No `deleted_at`
        filter on the customer, for the reason `CalendarRepository.deal_labels` records:
        an archived client is still who the work was for."""
        row = self.session.execute(
            select(WorkUnit, Contract, Customer)
            .join(Contract, Contract.id == WorkUnit.contract_id)
            .join(Customer, Customer.id == Contract.customer_id)
            .where(WorkUnit.id == work_unit_id)
        ).first()
        return None if row is None else (row[0], row[1], row[2])

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

    def list_labelled(self, query: WorkUnitListQuery) -> list[tuple[WorkUnit, Contract, Customer]]:
        """The newest `query.limit + 1` days matching `query`, newest first, each with its
        contract and customer. One row more than asked, so the caller can tell a list that
        was cut from one that merely ended."""
        stmt = (
            select(WorkUnit, Contract, Customer)
            .join(Contract, Contract.id == WorkUnit.contract_id)
            .join(Customer, Customer.id == Contract.customer_id)
        )
        if query.contract_id is not None:
            stmt = stmt.where(WorkUnit.contract_id == query.contract_id)
        if query.azienda_id is not None:
            stmt = stmt.where(Contract.azienda_id == query.azienda_id)
        if query.da is not None:
            stmt = stmt.where(WorkUnit.data >= query.da)
        if query.a is not None:
            stmt = stmt.where(WorkUnit.data <= query.a)
        if query.stato is not None:
            stmt = stmt.where(WorkUnit.stato == query.stato)
        stmt = stmt.order_by(WorkUnit.data.desc(), WorkUnit.id.desc()).limit(query.limit + 1)
        return [(row[0], row[1], row[2]) for row in self.session.execute(stmt).all()]


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

    def document_title(self, document_id: UUID) -> str | None:
        document = self.session.get(Document, document_id)
        return None if document is None else document.titolo
