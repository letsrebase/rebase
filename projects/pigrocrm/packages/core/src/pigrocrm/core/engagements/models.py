"""The registry side of a signed engagement (spec 2026-09-25 § 2.2): what the hub's
door set up in this CRM for one match, keyed by the hub's own id so a retried call
does the same nothing twice rather than a second space, customer or deal.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, func, text
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db.base import PrimaryKeyMixin
from pigrocrm.core.tenants.models import TenantsBase


class RebaseEngagement(TenantsBase, PrimaryKeyMixin):
    """One row per hub match (spec 2026-09-25 § 2.2): which space, which customer and
    which deal and contract the hub's old door set up for it (REB-1108 removed the door;
    the rows stay, read by the report and by `OwnEngagementService`). `match_id` is the
    hub's id and the idempotency key. `space_created` says the space was opened for this
    match (or its missing admin created for it)."""

    __tablename__ = "rebase_engagements"

    match_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    # The customer «rebase» in the space, which the door answers to every retry of the call. It
    # is outside the space's database, so a merge of that customer points it at the survivor
    # through `EngagementRegistry` (REB-1031) and nothing else writes it after step 6.
    customer_id: Mapped[UUID | None] = mapped_column(default=None)
    deal_id: Mapped[UUID | None] = mapped_column(default=None)
    # The contract the letter became in the freelancer's space (REB-1001), written with
    # `deal_id` in the call's last step. NULL on a row written before it existed: that
    # space keeps its deal and its hours, and no contract is written behind it.
    contract_id: Mapped[UUID | None] = mapped_column(default=None)
    space_created: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
