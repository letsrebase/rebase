"""The monthly «Rendiconto» of an engagement: an approval request, decided by a person or by the
tacit date passing (collaborators design § 4, framework agreement 6.1 to 6.2).

Recording that rebase received a month's report and approving it (the client's agreement
relayed, or its objection to a part) are a person's acts and no agent's: `approve_monthly_report`
and `record_monthly_report` are in `PERSON_ONLY_ACTIONS`, and there is no MCP tool for either. The
row copies no day; `days_report.py` reads the days live and asks the rows here which months
count.
"""

import datetime as dt
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.collaborators import rules
from pigrocrm.core.collaborators.models import REPORTING_STATES, Engagement, MonthlyReport
from pigrocrm.core.collaborators.repository import EngagementRepository
from pigrocrm.core.collaborators.rules import ENTITY
from pigrocrm.core.collaborators.schemas import (
    MonthlyReportApproval,
    MonthlyReportPage,
    MonthlyReportRead,
    MonthlyReportRequest,
)
from pigrocrm.core.config import Settings
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed

MONTHLY_REPORT_ENTITY = "monthly_report"


def decider_names(session: Session, rows: Iterable[MonthlyReport]) -> dict[UUID, str]:
    """The names of the people who decided these months, by user id: a screen says who approved
    a month, and a person who has since left the space still reads by name."""
    ids = {row.decided_by for row in rows if row.decided_by is not None}
    if not ids:
        return {}
    found = session.execute(select(User.id, User.name).where(User.id.in_(ids))).all()
    return {row[0]: row[1] for row in found}


def read_monthly_report(
    row: MonthlyReport, today: dt.date, decided_by_name: str | None = None
) -> MonthlyReportRead:
    effective = rules.effective_state(row.state, row.tacit_on, today)
    return MonthlyReportRead(
        id=row.id,
        engagement_id=row.engagement_id,
        month=row.month,
        label=rules.month_label(row.month),
        state=row.state,
        effective_state=effective,  # type: ignore[arg-type]
        counts_as_approved=rules.counts_as_approved(effective),
        received_on=row.received_on,
        tacit_on=row.tacit_on,
        decided_on=row.decided_on,
        decided_by_name=decided_by_name,
        objection=row.objection,
        held_dates=[dt.date.fromisoformat(value) for value in row.held_dates],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def monthly_reports_of(session: Session, engagement_id: UUID) -> list[MonthlyReport]:
    return list(
        session.scalars(
            select(MonthlyReport)
            .where(MonthlyReport.engagement_id == engagement_id)
            .order_by(MonthlyReport.month)
        )
    )


class MonthlyReportService:
    def __init__(
        self, session: Session, settings: Settings, *, space_slug: str | None = None
    ) -> None:
        self.session = session
        self.settings = settings
        self.space_slug = space_slug
        self.repo = EngagementRepository(session)
        self.activities = ActivityService(session)

    def for_engagement(self, engagement_id: UUID, actor: Actor) -> MonthlyReportPage:
        actor.require_admin("read_engagement_report")
        self._engagement(engagement_id)
        today = today_local(self.settings)
        rows = monthly_reports_of(self.session, engagement_id)
        names = decider_names(self.session, rows)
        return MonthlyReportPage(
            items=[
                read_monthly_report(
                    row, today, names.get(row.decided_by) if row.decided_by else None
                )
                for row in rows
            ]
        )

    def request(
        self, engagement_id: UUID, data: MonthlyReportRequest, actor: Actor
    ) -> MonthlyReportRead:
        """Records that the freelancer's report of `month` reached rebase, and starts the
        seven working days after which, with no reasoned objection, it counts as approved. The
        month must be over, inside the engagement's dates, and asked once."""
        actor.require_person("record_monthly_report")
        actor.require_admin("record_monthly_report")
        rules.require_leads_space(self.settings, self.space_slug)
        engagement = self._engagement(engagement_id, lock=True)
        month = rules.parse_month(data.month)
        today = today_local(self.settings)
        if engagement.state not in REPORTING_STATES:
            raise Conflict(
                ENTITY,
                "il rendiconto si registra per un incarico attivo o concluso",
                state=engagement.state,
            )
        last_day = rules.next_month(month) - dt.timedelta(days=1)
        if last_day >= today:
            raise ValidationFailed(
                MONTHLY_REPORT_ENTITY,
                "month",
                "il mese non e' ancora finito: non c'e' un rendiconto",
            )
        if last_day < engagement.starts_on or (
            engagement.ends_on is not None and engagement.ends_on < month
        ):
            raise ValidationFailed(
                MONTHLY_REPORT_ENTITY, "month", "il mese e' fuori dalle date dell'incarico"
            )
        received_on = data.received_on or today
        if received_on > today:
            raise ValidationFailed(
                MONTHLY_REPORT_ENTITY, "received_on", "il rendiconto non puo' arrivare nel futuro"
            )
        if received_on <= last_day:
            raise ValidationFailed(
                MONTHLY_REPORT_ENTITY,
                "received_on",
                "il rendiconto arriva dopo la fine del mese che riguarda",
                expected=f"> {last_day.isoformat()}",
            )
        row = MonthlyReport(
            engagement_id=engagement.id,
            freelancer_id=engagement.freelancer_id,
            month=month,
            state="requested",
            received_on=received_on,
            tacit_on=rules.tacit_date(received_on),
            held_dates=[],
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            if "uq_engagement_monthly_reports_month" not in str(exc.orig):
                raise
            raise Conflict(
                MONTHLY_REPORT_ENTITY,
                "il rendiconto di questo mese e' gia' registrato",
                month=data.month,
            ) from exc
        self.activities.record(
            ENTITY,
            engagement.id,
            "monthly_report_requested",
            actor,
            {"month": data.month, "tacit_on": row.tacit_on.isoformat()},
        )
        self.session.commit()
        return read_monthly_report(row, today)

    def approve(
        self, engagement_id: UUID, month_label: str, data: MonthlyReportApproval, actor: Actor
    ) -> MonthlyReportRead:
        """A person's decision: the month is approved and the days in `held_dates` are held,
        with the reason, while the rest is payable. Deciding again changes the holds and keeps
        the first decision's date and person. A day cannot be newly held once the tacit date has
        passed, whether or not the month was already approved: with no objection in time the
        report counts as approved (framework 6.2)."""
        actor.require_person("approve_monthly_report")
        actor.require_admin("approve_monthly_report")
        rules.require_leads_space(self.settings, self.space_slug)
        self._engagement(engagement_id, lock=True)
        month = rules.parse_month(month_label)
        row = self.session.scalar(
            select(MonthlyReport)
            .where(MonthlyReport.engagement_id == engagement_id, MonthlyReport.month == month)
            .with_for_update()
        )
        if row is None:
            raise NotFound(MONTHLY_REPORT_ENTITY, month_label)
        today = today_local(self.settings)
        held = sorted(set(data.held_dates))
        objection = data.objection.strip() if data.objection else None
        if held and not objection:
            raise ValidationFailed(
                MONTHLY_REPORT_ENTITY,
                "objection",
                "per trattenere dei giorni serve il motivo della contestazione",
            )
        if objection and not held:
            raise ValidationFailed(
                MONTHLY_REPORT_ENTITY,
                "held_dates",
                "una contestazione indica i giorni che tratteniamo",
            )
        end = rules.next_month(month)
        outside = [day.isoformat() for day in held if not month <= day < end]
        if outside:
            raise ValidationFailed(
                MONTHLY_REPORT_ENTITY,
                "held_dates",
                "i giorni trattenuti sono del mese del rendiconto",
                expected=f"da {month.isoformat()} a {(end - dt.timedelta(days=1)).isoformat()}: "
                f"{', '.join(outside)}",
            )
        added = set(held) - {dt.date.fromisoformat(value) for value in row.held_dates}
        if added and today > row.tacit_on:
            raise Conflict(
                MONTHLY_REPORT_ENTITY,
                "il termine per contestare e' scaduto: il rendiconto vale come approvato",
                tacit_on=row.tacit_on.isoformat(),
            )
        previous: dict[str, Any] = {"held": len(row.held_dates), "state": row.state}
        if row.decided_on is None:
            row.state = "approved"
            row.decided_on = today
            row.decided_by = actor.id
        row.objection = objection
        row.held_dates = [day.isoformat() for day in held]
        self.session.flush()
        self.activities.record(
            ENTITY,
            engagement_id,
            "monthly_report_approved",
            actor,
            {"month": month_label, "held": len(held), "before": previous},
        )
        self.session.commit()
        names = decider_names(self.session, [row])
        return read_monthly_report(
            row, today, names.get(row.decided_by) if row.decided_by else None
        )

    def _engagement(self, engagement_id: UUID, *, lock: bool = False) -> Engagement:
        found = self.repo.get(engagement_id, lock=lock)
        if found is None:
            raise NotFound(ENTITY, engagement_id)
        return found
