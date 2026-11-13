"""What the registry holds about one space's organizations (REB-1031).

`rebase_engagements.customer_id` is the id of the customer «rebase» in the freelancer's own
space. The CRM writes it with the deal (`leads.py`, `own.py`) and answers it to every retry of the
hub's call: the row's id, never the one the deal points at now, because a retry must answer what
the first call did. The column lives in the registry database, outside the space's, so the
foreign keys `OrganizationMerge` walks cannot reach it: folding two organizations of that space
would leave it on the archived one, and the next retry would hand the hub a customer that is
gone. This is the step that keeps it true, written on a registry session and never on the
space's: two databases, so no transaction spans both and the caller commits them in order.

No import of `service` here, as in this package's `__init__`: callers import this module by its
full path.
"""

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.tenants.models import Tenant


class EngagementRegistry:
    """The rows of one space on a registry session the caller owns and closes.

    `space_slug` is the space the request serves (`None` for the root installation, which the
    door never opens an engagement in: nothing to point there). Only that space's rows are
    read or written, so an id that happens to recur in another space is never touched."""

    def __init__(self, registry: Session, space_slug: str | None) -> None:
        self.registry = registry
        self.space_slug = space_slug

    def _space_id(self) -> UUID | None:
        if self.space_slug is None:
            return None
        return self.registry.scalar(select(Tenant.id).where(Tenant.slug == self.space_slug))

    def count(self, customer_id: UUID) -> int:
        """The rows that name `customer_id`: what a preview says it would point elsewhere."""
        tenant_id = self._space_id()
        if tenant_id is None:
            return 0
        return int(
            self.registry.scalar(
                select(func.count())
                .select_from(RebaseEngagement)
                .where(
                    RebaseEngagement.tenant_id == tenant_id,
                    RebaseEngagement.customer_id == customer_id,
                )
            )
            or 0
        )

    def repoint(self, loser_id: UUID, survivor_id: UUID) -> int:
        """Names `survivor_id` wherever the rows named `loser_id`, without committing."""
        tenant_id = self._space_id()
        if tenant_id is None:
            return 0
        result = self.registry.execute(
            update(RebaseEngagement)
            .where(
                RebaseEngagement.tenant_id == tenant_id,
                RebaseEngagement.customer_id == loser_id,
            )
            .values(customer_id=survivor_id)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    def commit(self) -> None:
        self.registry.commit()

    def rollback(self) -> None:
        self.registry.rollback()
