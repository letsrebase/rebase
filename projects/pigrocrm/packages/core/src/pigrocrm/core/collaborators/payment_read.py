"""What an engagement pays its freelancer, month by month, as the engagement's page draws it
(collaborators design § 4, «Due items»).

A read of the due items the engagement wrote (source kind `engagement`), with what a person
needs to judge each one and the web must not work out: the month it pays for, and the client's
invoice whose settlement the date still follows. Nothing here settles or changes anything: paying
is the due item's and the purchase's, an administrator's (`settle_outgoing_due_item`).

Four queries whatever the number of items: the items, what each has been paid, the items they
follow and the invoices those are.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import payments
from pigrocrm.core.collaborators.models import Engagement
from pigrocrm.core.collaborators.repository import EngagementRepository
from pigrocrm.core.collaborators.rules import ENTITY
from pigrocrm.core.collaborators.schemas import (
    EngagementPaymentPage,
    EngagementPaymentRead,
    PaymentState,
)
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.repository import DueItemRepository
from pigrocrm.core.due_items.service import SALES_INVOICE
from pigrocrm.core.errors import NotFound
from pigrocrm.core.invoices.models import Invoice


class EngagementPaymentService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = EngagementRepository(session)
        self.items = DueItemRepository(session)

    def for_engagement(self, engagement_id: UUID, actor: Actor) -> EngagementPaymentPage:
        """The engagement's live due items, oldest first. Engagement items disclose what a
        freelancer is paid, so reading them is an administrator's, and an agent's only with
        `full_access`, as for any fiscal due item."""
        actor.require_admin("read_engagements")
        actor.require_agent_allowed("list_due_items")
        actor.require_admin("read_fiscal_due_items")
        engagement = self.repo.get(engagement_id)
        if engagement is None:
            raise NotFound(ENTITY, engagement_id)
        rows = [
            row
            for row in self.items.items_of_source(payments.SOURCE_KIND, engagement.id)
            if row.state != "dropped"
        ]
        months = self._months(engagement)
        client = engagement.client_organization_id
        visible = client is None or self.repo.visible_organization(client) is not None
        settled = self._settled([row.id for row in rows])
        waiting = self._waiting_for(rows)
        return EngagementPaymentPage(
            engagement_id=engagement.id,
            items=[
                self._payment(row, months, visible, settled.get(row.id, Decimal(0)), waiting)
                for row in rows
            ],
        )

    @staticmethod
    def _months(engagement: Engagement) -> dict[UUID, dt.date]:
        """The month each billing point pays for, by the key its forecast was written under."""
        return {
            payments.forecast_key(
                engagement.id, billing.billing_date
            ): billing.billing_date.replace(day=1)
            for billing in payments.billings(engagement, today_local())
        }

    def _settled(self, item_ids: list[UUID]) -> dict[UUID, Decimal]:
        """What each item has been paid, reversed payments left out."""
        if not item_ids:
            return {}
        found = self.session.execute(
            select(DueItemSettlement.due_item_id, func.sum(DueItemSettlement.amount))
            .where(
                DueItemSettlement.due_item_id.in_(item_ids),
                DueItemSettlement.reversed_at.is_(None),
            )
            .group_by(DueItemSettlement.due_item_id)
        ).all()
        return {row[0]: Decimal(row[1]) for row in found}

    def _waiting_for(self, rows: list[DueItem]) -> dict[UUID, str]:
        """The client's invoice, `2026/71`, each item's date still follows, by item id: only while
        the item it depends on is unpaid and is a sales invoice's."""
        dependency_ids = {row.depends_on_id for row in rows if row.depends_on_id is not None}
        if not dependency_ids:
            return {}
        dependencies = {
            dep.id: dep
            for dep in self.session.scalars(select(DueItem).where(DueItem.id.in_(dependency_ids)))
            if dep.state != "settled" and dep.source_kind == SALES_INVOICE and dep.source_id
        }
        invoices = {
            row[0]: f"{row[1]}/{row[2]}"
            for row in self.session.execute(
                select(Invoice.id, Invoice.year, Invoice.number).where(
                    Invoice.id.in_({dep.source_id for dep in dependencies.values()}),
                    Invoice.year.is_not(None),
                    Invoice.number.is_not(None),
                )
            ).all()
        }
        out: dict[UUID, str] = {}
        for row in rows:
            dependency = dependencies.get(row.depends_on_id) if row.depends_on_id else None
            label = invoices.get(dependency.source_id) if dependency is not None else None
            if label is not None:
                out[row.id] = label
        return out

    @staticmethod
    def _payment(
        row: DueItem,
        months: dict[UUID, dt.date],
        visible: bool,
        settled: Decimal,
        waiting: dict[UUID, str],
    ) -> EngagementPaymentRead:
        state: PaymentState = row.state  # type: ignore[assignment]  # a dropped one is left out
        return EngagementPaymentRead(
            due_item_id=row.id,
            month=months.get(row.forecast_key) if row.forecast_key is not None else None,
            state=state,
            amount=row.amount if visible else None,
            settled=settled if visible else None,
            remaining=row.amount - settled if visible else None,
            currency=row.currency,
            due_date=row.due_date,
            due_date_estimated=row.due_date_estimated,
            waiting_for_invoice=waiting.get(row.id),
            description=row.description,
        )
