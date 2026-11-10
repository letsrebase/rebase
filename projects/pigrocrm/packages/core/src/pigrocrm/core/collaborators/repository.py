"""Reads and writes of `engagements`, for the collaborators services alone.

Every read joins the freelancer's organization: its row-level policy is what hides an
engagement from a scoped admin, since the table has no legal entity of its own.
"""

import datetime as dt
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.models import LIVE_STATES, Engagement
from pigrocrm.core.collaborators.schemas import EngagementListQuery
from pigrocrm.core.organizations.models import Organization


class EngagementRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, engagement_id: UUID, *, lock: bool = False) -> Engagement | None:
        stmt = (
            select(Engagement)
            .join(Organization, Organization.id == Engagement.freelancer_id)
            .where(Engagement.id == engagement_id)
        )
        if lock:
            # `of`: only the engagement is locked, not the freelancer's organization.
            stmt = stmt.with_for_update(of=Engagement).execution_options(populate_existing=True)
        return self.session.execute(stmt).scalar_one_or_none()

    def add(self, engagement: Engagement) -> Engagement:
        self.session.add(engagement)
        self.session.flush()
        return engagement

    def by_hub_match(self, hub_match_id: UUID) -> Engagement | None:
        return self.session.execute(
            select(Engagement).where(Engagement.hub_match_id == hub_match_id)
        ).scalar_one_or_none()

    def page(self, query: EngagementListQuery) -> list[Engagement]:
        """Newest first by id (a UUIDv7 is creation order), one extra row to tell «more» from
        «that was everything»."""
        stmt = select(Engagement).join(Organization, Organization.id == Engagement.freelancer_id)
        if query.freelancer_id is not None:
            stmt = stmt.where(Engagement.freelancer_id == query.freelancer_id)
        if query.state is not None:
            stmt = stmt.where(Engagement.state == query.state)
        if query.client_organization_id is not None:
            stmt = stmt.where(Engagement.client_organization_id == query.client_organization_id)
        if query.deal_id is not None:
            stmt = stmt.where(Engagement.deal_id == query.deal_id)
        if query.contract_id is not None:
            stmt = stmt.where(Engagement.contract_id == query.contract_id)
        if query.cursor is not None:
            stmt = stmt.where(Engagement.id < query.cursor)
        return list(
            self.session.execute(
                stmt.order_by(Engagement.id.desc()).limit(query.limit + 1)
            ).scalars()
        )

    def of_freelancer(
        self,
        freelancer_id: UUID,
        states: tuple[str, ...],
        *,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        excluding: UUID | None = None,
    ) -> list[Engagement]:
        """The freelancer's engagements in `states` that touch `date_from` to `date_to`
        (an open end on either side is open)."""
        stmt = select(Engagement).where(
            Engagement.freelancer_id == freelancer_id, Engagement.state.in_(states)
        )
        if excluding is not None:
            stmt = stmt.where(Engagement.id != excluding)
        if date_to is not None:
            stmt = stmt.where(Engagement.starts_on <= date_to)
        if date_from is not None:
            stmt = stmt.where(Engagement.ends_on.is_(None) | (Engagement.ends_on >= date_from))
        return list(
            self.session.execute(stmt.order_by(Engagement.starts_on, Engagement.id)).scalars()
        )

    def live_of_freelancer(
        self, freelancer_id: UUID, *, excluding: UUID | None = None
    ) -> list[Engagement]:
        return self.of_freelancer(freelancer_id, LIVE_STATES, excluding=excluding)

    def organization(self, organization_id: UUID) -> Organization | None:
        """A live organization the session may see."""
        found = self.session.get(Organization, organization_id)
        if found is None or found.deleted_at is not None:
            return None
        return found

    def organization_by_hub_id(self, hub_freelancer_id: UUID) -> Organization | None:
        return self.session.execute(
            select(Organization).where(
                Organization.hub_freelancer_id == hub_freelancer_id,
                Organization.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
