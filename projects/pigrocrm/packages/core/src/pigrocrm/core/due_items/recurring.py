"""The recurrence rules of money going out: create, list and stop (spec 2026-11-09, due items,
§ 1.2).

A rule is owned by due items: a supplier, an amount, a cadence and the first due date, with the
cost category the purchase will have. It writes nothing of its own to the schedule. The
forecasts out it produces are `ForecastService.refresh_recurring_expense`'s: written in the
transaction of every change below, by the daily pass, and again whenever a purchase of the
supplier changes, so a purchase of the period replaces the forecast and never adds to it.

Stopping a rule drops the forecasts it has; the due items its past occurrences became are
purchases' now and stay. A rule is never deleted.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.forecast import ForecastService
from pigrocrm.core.due_items.models import RecurringExpense
from pigrocrm.core.due_items.recurring_schemas import (
    RecurringExpenseCreate,
    RecurringExpenseRead,
)
from pigrocrm.core.due_items.repository import DueItemRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.repository import OrganizationRepository
from pigrocrm.core.timetracking.categories import CostCategoryService

ENTITY = "recurring_expense"


class RecurringExpenseService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.legal_entities = LegalEntityService(session)
        self.organizations = OrganizationRepository(session)
        self.categories = CostCategoryService(session)
        self.due_items = DueItemRepository(session)
        self.forecasts = ForecastService(session)
        self.activities = ActivityService(session)

    def create_recurring_expense(
        self, data: RecurringExpenseCreate, actor: Actor
    ) -> RecurringExpenseRead:
        actor.require_write("create_recurring_expense")
        legal_entity = self.legal_entities.require_single(data.legal_entity_id, entity=ENTITY)
        if not legal_entity.active:
            raise ValidationFailed(
                ENTITY,
                "legal_entity_id",
                "l'azienda non e' attiva: una spesa si assegna a un'azienda attiva",
                expected="l'id di un'azienda attiva",
            )
        self._check_supplier(data.supplier_id, legal_entity.id)
        self.categories.require_active(data.category_id)
        rule = RecurringExpense(
            legal_entity_id=legal_entity.id,
            supplier_id=data.supplier_id,
            category_id=data.category_id,
            description=data.description,
            amount=data.amount,
            cadence=data.cadence,
            first_due_date=data.first_due_date,
            ends_on=data.ends_on,
        )
        self.session.add(rule)
        self.session.flush()
        self.forecasts.refresh_recurring_expense(rule.id)
        self.activities.record(
            ENTITY,
            rule.id,
            "created",
            actor,
            {
                "supplier_id": str(rule.supplier_id),
                "amount": str(rule.amount),
                "cadence": rule.cadence,
            },
        )
        self.session.commit()
        return RecurringExpenseRead.model_validate(rule)

    def stop_recurring_expense(self, rule_id: UUID, actor: Actor) -> RecurringExpenseRead:
        actor.require_write("stop_recurring_expense")
        rule = self.session.get(RecurringExpense, rule_id)
        if rule is None:
            raise NotFound(ENTITY, rule_id)
        if rule.stopped_on is not None:
            raise Conflict(ENTITY, "la spesa ricorrente e' gia' terminata", id=str(rule_id))
        rule.stopped_on = today_local()
        self.session.flush()
        self.forecasts.refresh_recurring_expense(rule.id)
        self.activities.record(ENTITY, rule.id, "stopped", actor)
        self.session.commit()
        return RecurringExpenseRead.model_validate(rule)

    def list_recurring_expenses(
        self,
        actor: Actor,
        *,
        include_stopped: bool = False,
        legal_entity_id: UUID | None = None,
    ) -> list[RecurringExpenseRead]:
        """The rules in the order they were written, the active ones unless `include_stopped`.
        Any member who may read the schedule reads them."""
        stmt = select(RecurringExpense).order_by(RecurringExpense.created_at, RecurringExpense.id)
        if not include_stopped:
            stmt = stmt.where(RecurringExpense.stopped_on.is_(None))
        if legal_entity_id is not None:
            stmt = stmt.where(RecurringExpense.legal_entity_id == legal_entity_id)
        return [RecurringExpenseRead.model_validate(row) for row in self.session.scalars(stmt)]

    def _check_supplier(self, supplier_id: UUID, legal_entity_id: UUID) -> None:
        supplier = self.organizations.get(supplier_id)
        if supplier is None:
            raise NotFound("organization", supplier_id)
        if "supplier" not in supplier.roles:
            raise ValidationFailed(
                ENTITY,
                "supplier_id",
                "l'anagrafica non ha il ruolo di fornitore",
                expected="un'anagrafica con il ruolo di fornitore",
            )
        row = self.due_items.organization_legal_entity(supplier_id)
        owner = row[0] if row is not None else None
        # A supplier shared across the companies is every company's (REB-970).
        if owner is not None and owner != legal_entity_id:
            raise ValidationFailed(
                ENTITY,
                "supplier_id",
                "il fornitore e' di un'altra azienda",
                expected="un fornitore della stessa azienda della spesa",
            )


__all__ = ["RecurringExpenseService"]
