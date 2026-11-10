"""What an engagement pays its freelancer, month by month, as the engagement's page draws it
(collaborators design § 4, «Due items»).

A read of the due items the engagement wrote (source kind `engagement`), with what a person
needs to judge each one and the web must not work out: the month it pays for, and the client's
invoice whose settlement the date still follows. Nothing here settles or changes anything: paying
is the due item's and the purchase's, an administrator's (`settle_outgoing_due_item`).
"""

import datetime as dt
from uuid import UUID

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
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.repository import DueItemRepository
from pigrocrm.core.due_items.schemas import DueItemRead
from pigrocrm.core.due_items.service import SALES_INVOICE, DueItemService
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
        engagement = self.repo.get(engagement_id)
        if engagement is None:
            raise NotFound(ENTITY, engagement_id)
        reads = DueItemService(self.session).of_source(payments.SOURCE_KIND, engagement.id, actor)
        rows = {
            row.id: row for row in self.items.items_of_source(payments.SOURCE_KIND, engagement.id)
        }
        months = self._months(engagement)
        client = engagement.client_organization_id
        visible = client is None or self.repo.visible_organization(client) is not None
        return EngagementPaymentPage(
            engagement_id=engagement.id,
            items=[self._payment(read, rows[read.id], months, visible) for read in reads],
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

    def _payment(
        self, read: DueItemRead, row: DueItem, months: dict[UUID, dt.date], visible: bool
    ) -> EngagementPaymentRead:
        state: PaymentState = read.state  # type: ignore[assignment]  # a dropped one is left out
        return EngagementPaymentRead(
            due_item_id=read.id,
            month=months.get(row.forecast_key) if row.forecast_key is not None else None,
            state=state,
            amount=read.amount if visible else None,
            settled=read.settled if visible else None,
            remaining=read.remaining if visible else None,
            currency=read.currency,
            due_date=read.due_date,
            due_date_estimated=read.due_date_estimated,
            waiting_for_invoice=self._waiting_for(row),
            description=read.description,
        )

    def _waiting_for(self, row: DueItem) -> str | None:
        """The client's invoice this item's date follows, while that invoice is unpaid."""
        if row.depends_on_id is None:
            return None
        dependency = self.items.get(row.depends_on_id)
        if (
            dependency is None
            or dependency.state == "settled"
            or dependency.source_kind != SALES_INVOICE
            or dependency.source_id is None
        ):
            return None
        invoice = self.session.get(Invoice, dependency.source_id)
        if invoice is None or invoice.year is None or invoice.number is None:
            return None
        return f"{invoice.year}/{invoice.number}"
