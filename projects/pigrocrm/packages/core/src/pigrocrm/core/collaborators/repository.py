"""Reads and writes of `engagements`, for the collaborators services alone.

Every read joins the freelancer's organization: its row-level policy is what hides an
engagement from a scoped admin, since the table has no legal entity of its own.
"""

import datetime as dt
from uuid import UUID

from sqlalchemy import ColumnElement, and_, case, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators import letters
from pigrocrm.core.collaborators.models import (
    LIVE_STATES,
    REPORTING_STATES,
    Engagement,
    LetterCounter,
    MonthlyReport,
)
from pigrocrm.core.collaborators.schemas import EngagementListQuery
from pigrocrm.core.documents.models import Document
from pigrocrm.core.organizations.models import Organization

CLOSED_STATES = ("concluded", "cancelled")


def waiting_for_a_person(today: dt.date) -> ColumnElement[bool]:
    """What puts an engagement in «Da fare»: a draft (its letter is written and sent by a person),
    a live one still missing its `days_per_week` or client link, and an active or concluded one
    with a month received that nobody decided and whose tacit date has not passed (`today` is
    still a day an objection counts). The same rules `EngagementService` words per engagement."""
    undecided = exists().where(
        MonthlyReport.engagement_id == Engagement.id,
        MonthlyReport.state == "requested",
        MonthlyReport.tacit_on >= today,
    )
    unfilled = or_(
        Engagement.days_per_week.is_(None),
        and_(Engagement.deal_id.is_(None), Engagement.contract_id.is_(None)),
    )
    return or_(
        Engagement.state == "draft",
        and_(Engagement.state.in_(LIVE_STATES), unfilled),
        and_(Engagement.state.in_(REPORTING_STATES), undecided),
    )


def bucket_of(today: dt.date) -> ColumnElement[str]:
    """The list's three tabs, which never overlap: waits for a person, closed, the rest."""
    return case(
        (waiting_for_a_person(today), "todo"),
        (Engagement.state.in_(CLOSED_STATES), "closed"),
        else_="ongoing",
    )


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

    def next_letter_number(self, year: int) -> str:
        """The next letter number of `year`, `2026-014`: one upsert on the year's counter, so two
        engagements created together never share a number, and a number once given is never given
        again (a cancellation keeps its own)."""
        last = self.session.execute(
            pg_insert(LetterCounter)
            .values(year=year, last=1)
            .on_conflict_do_update(
                index_elements=[LetterCounter.year], set_={"last": LetterCounter.last + 1}
            )
            .returning(LetterCounter.last)
        ).scalar_one()
        return f"{year}-{last:03d}"

    def by_hub_match(self, hub_match_id: UUID) -> Engagement | None:
        return self.session.execute(
            select(Engagement).where(Engagement.hub_match_id == hub_match_id)
        ).scalar_one_or_none()

    def page(self, query: EngagementListQuery, today: dt.date) -> list[Engagement]:
        """Newest first by id (a UUIDv7 is creation order), one extra row to tell «more» from
        «that was everything»."""
        stmt = select(Engagement).join(Organization, Organization.id == Engagement.freelancer_id)
        if query.bucket is not None:
            stmt = stmt.where(bucket_of(today) == query.bucket)
        if query.freelancer_id is not None:
            stmt = stmt.where(Engagement.freelancer_id == query.freelancer_id)
        if query.hub_match_id is not None:
            stmt = stmt.where(Engagement.hub_match_id == query.hub_match_id)
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

    def bucket_counts(self, today: dt.date) -> dict[str, int]:
        """How many engagements each tab of the list holds, over what the session may see."""
        bucket = bucket_of(today)
        rows = self.session.execute(
            select(bucket, func.count())
            .select_from(Engagement)
            .join(Organization, Organization.id == Engagement.freelancer_id)
            .group_by(bucket)
        ).all()
        return {name: int(total) for name, total in rows}

    def organization_names(self, organization_ids: set[UUID]) -> dict[UUID, str]:
        """The legal names of the organizations the session may see: one it may not see is
        absent, which is how a hidden client is told from one that has no name."""
        if not organization_ids:
            return {}
        rows = self.session.execute(
            select(Organization.id, Organization.legal_name).where(
                Organization.id.in_(organization_ids)
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    def letter_documents(self, engagement_ids: list[UUID]) -> dict[UUID, UUID]:
        """The stored letter of each engagement that has one."""
        if not engagement_ids:
            return {}
        rows = self.session.execute(
            select(Document.engagement_id, Document.id).where(
                Document.engagement_id.in_(engagement_ids),
                Document.type == letters.ENGAGEMENT_LETTER,
                Document.deleted_at.is_(None),
            )
        ).all()
        return {row[0]: row[1] for row in rows if row[0] is not None}

    def undecided_months(
        self, engagement_ids: list[UUID], today: dt.date
    ) -> dict[UUID, list[MonthlyReport]]:
        """The months received and not decided, whose tacit date has not passed, by engagement,
        oldest month first."""
        out: dict[UUID, list[MonthlyReport]] = {}
        if not engagement_ids:
            return out
        rows = self.session.execute(
            select(MonthlyReport)
            .where(
                MonthlyReport.engagement_id.in_(engagement_ids),
                MonthlyReport.state == "requested",
                MonthlyReport.tacit_on >= today,
            )
            .order_by(MonthlyReport.month)
        ).scalars()
        for row in rows:
            out.setdefault(row.engagement_id, []).append(row)
        return out

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

    def visible_organization(self, organization_id: UUID) -> Organization | None:
        """The organization if this session may see it, archived or not: the row policy is the
        only judge."""
        return self.session.get(Organization, organization_id)

    def organization_by_hub_id(self, hub_freelancer_id: UUID) -> Organization | None:
        return self.session.execute(
            select(Organization).where(
                Organization.hub_freelancer_id == hub_freelancer_id,
                Organization.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
