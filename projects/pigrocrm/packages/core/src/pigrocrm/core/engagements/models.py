"""The registry side of a signed engagement (spec 2026-09-25 § 2.2): what the hub's
door set up in this CRM for one match, keyed by the hub's own id so a retried call
does the same nothing twice rather than a second space, customer or deal.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db.base import PrimaryKeyMixin
from pigrocrm.core.tenants.models import TenantsBase


class RebaseEngagement(TenantsBase, PrimaryKeyMixin):
    """One row per hub match (spec 2026-09-25 § 2.2): which space, which customer and
    which deal the door set up for it. `match_id` is the hub's id and the idempotency
    key; `deal_id` stays NULL between step 3 and step 6 of `EngagementService.ensure`."""

    __tablename__ = "rebase_engagements"

    match_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    customer_id: Mapped[UUID | None] = mapped_column(default=None)
    deal_id: Mapped[UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
