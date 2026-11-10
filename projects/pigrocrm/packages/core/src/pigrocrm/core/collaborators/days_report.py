"""An engagement's days, read live from the freelancer's own space, and which of them are
payable (collaborators design § 3 «The report» and § 4 «What makes a day payable»).

Nothing is copied and no lock is taken: each read asks the engagements door
(`engagements.service.EngagementService.report`), in-process, for the period in windows of at
most `REPORT_MAX_DAYS` days (the hub's `report_windows`), and joins the answers. The door
reads the work units of the freelancer's contract and, for a space opened before contracts, its
time entries on the old deal; the report reads both.

A day is payable only in a month whose «Rendiconto» counts as approved (a person approved it, or
its tacit date passed with no objection) and only when it is not held. The freelancer's own
`worked` days alone make nothing payable. An hourly letter pays hours; any other letter pays days,
and an old time entry's hours are days at 8 a day, the one place hours become days.

Inside one installation this works only from the space `PIGROCRM_LEADS_SPACE` names, the one that
holds the engagements: another space has no cross-space read.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators import rules
from pigrocrm.core.collaborators.models import Engagement, MonthlyReport
from pigrocrm.core.collaborators.monthly_reports import monthly_reports_of, read_monthly_report
from pigrocrm.core.collaborators.repository import EngagementRepository
from pigrocrm.core.collaborators.rules import CENT, COUNTING_STATUSES, ENTITY
from pigrocrm.core.collaborators.schemas import (
    EngagementDaysReport,
    ReportDay,
    ReportMonth,
    ReportUnit,
)
from pigrocrm.core.config import Settings
from pigrocrm.core.db import today_local
from pigrocrm.core.engagements.schemas import HOURS_PER_DAY, REPORT_MAX_DAYS, EngagementReport
from pigrocrm.core.engagements.service import EngagementService, SpaceUnreachable
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.tenants.database import tenants_database_url

NOT_SET_UP = (
    "Lo spazio del freelance non e' ancora pronto: si prepara quando l'incarico diventa attivo."
)


def report_windows(date_from: dt.date, date_to: dt.date) -> list[tuple[dt.date, dt.date]]:
    """`[date_from, date_to]` in consecutive windows the door accepts: `[date_from, date_from +
    800]`, then from the next day on, the last one ending on `date_to` (the hub's
    `report_windows`, so a retried or longer read asks for the same pieces)."""
    windows: list[tuple[dt.date, dt.date]] = []
    start = date_from
    while True:
        end = min(start + dt.timedelta(days=REPORT_MAX_DAYS), date_to)
        windows.append((start, end))
        if end >= date_to:
            return windows
        start = end + dt.timedelta(days=1)


def is_leads_space(settings: Settings, space_slug: str | None) -> bool:
    """Whether the space named `space_slug` (`None` for the root without a prefix) is the one
    `PIGROCRM_LEADS_SPACE` names."""
    leads = settings.leads_space.strip()
    return bool(leads) and (space_slug or settings.root_slug) == leads


class DaysReportService:
    """On the session of rebase's own space; `registry_engine` is the registry's, borrowed from
    the request when there is one, otherwise one is opened for the call and disposed."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        space_slug: str | None = None,
        registry_engine: Engine | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.space_slug = space_slug
        self.registry_engine = registry_engine
        self.repo = EngagementRepository(session)

    def report(
        self,
        engagement_id: UUID,
        actor: Actor,
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
    ) -> EngagementDaysReport:
        """The engagement's days from `date_from` (default its start) to `date_to` (default the
        end of the engagement, or today), with the «Rendiconto» of each month and what is
        payable. The report is empty and `available` false until the freelancer's space exists."""
        actor.require_admin("read_engagement_report")
        engagement = self.repo.get(engagement_id)
        if engagement is None:
            raise NotFound(ENTITY, engagement_id)
        today = today_local(self.settings)
        first = date_from or engagement.starts_on
        last = date_to or min(engagement.ends_on or today, today)
        if date_from is not None and date_to is not None and last < first:
            raise ValidationFailed(
                ENTITY,
                "date_to",
                "la fine non puo' essere prima dell'inizio",
                expected=">= date_from",
            )
        if not is_leads_space(self.settings, self.space_slug):
            raise Conflict(
                ENTITY,
                "i giorni dei freelance si leggono dallo spazio dei lead "
                "(PIGROCRM_LEADS_SPACE): altrove non c'e' lettura fra spazi",
            )
        door: EngagementReport | None = None
        note: str | None = None
        if first <= last:
            door, note = self._door(engagement, first, last)
        return self._assemble(engagement, first, last, door, note, today)

    # ---- the door -------------------------------------------------------------------------

    def _door(
        self, engagement: Engagement, first: dt.date, last: dt.date
    ) -> tuple[EngagementReport | None, str | None]:
        match_id = engagement.hub_match_id or engagement.id
        windows: list[EngagementReport] = []
        try:
            with self._engine() as engine:
                service = EngagementService(engine, self.settings)
                for start, end in report_windows(first, last):
                    windows.append(service.report(match_id, start, end))
        except NotFound:
            return None, NOT_SET_UP
        except SpaceUnreachable as exc:
            raise Conflict(
                ENTITY, "lo spazio del freelance non e' raggiungibile ora: riprova piu' tardi"
            ) from exc
        except ValidationFailed as exc:
            if exc.details.get("field") != "public_url":
                raise
            raise Conflict(ENTITY, "PIGROCRM_PUBLIC_URL non e' configurato") from exc
        except SQLAlchemyError as exc:
            raise Conflict(
                ENTITY, "lo spazio del freelance non e' raggiungibile ora: riprova piu' tardi"
            ) from exc
        head = windows[0]
        return (
            head.model_copy(
                update={
                    "days": [day for window in windows for day in window.days],
                    "work_units": [unit for window in windows for unit in window.work_units],
                }
            ),
            None,
        )

    @contextmanager
    def _engine(self) -> Iterator[Engine]:
        if self.registry_engine is not None:
            yield self.registry_engine
            return
        engine = create_engine(tenants_database_url(self.settings), future=True)
        try:
            yield engine
        finally:
            engine.dispose()

    # ---- the figures ----------------------------------------------------------------------

    def _assemble(
        self,
        engagement: Engagement,
        first: dt.date,
        last: dt.date,
        door: EngagementReport | None,
        note: str | None,
        today: dt.date,
    ) -> EngagementDaysReport:
        unit: ReportUnit = "hour" if engagement.fee_unit == "hour" else "day"
        monthly_reports = {
            row.month: row for row in monthly_reports_of(self.session, engagement.id)
        }
        counts_by_month = {
            month: rules.counts_as_approved(rules.effective_state(row.state, row.tacit_on, today))
            for month, row in monthly_reports.items()
        }
        held_by_month = {
            month: {dt.date.fromisoformat(value) for value in row.held_dates}
            for month, row in monthly_reports.items()
        }

        days: list[ReportDay] = []
        if door is not None:
            for unit_day in door.work_units:
                days.append(
                    self._day(
                        engagement,
                        counts_by_month,
                        held_by_month,
                        date=unit_day.date,
                        quantity=unit_day.quantity,
                        source="work_unit",
                        status=unit_day.status,
                        description=unit_day.description,
                        counts=unit_day.status in COUNTING_STATUSES,
                    )
                )
            for entry in door.days:
                quantity = entry.hours if unit == "hour" else _to_days(entry.hours)
                days.append(
                    self._day(
                        engagement,
                        counts_by_month,
                        held_by_month,
                        date=entry.date,
                        quantity=quantity,
                        source="time_entry",
                        status=None,
                        description=entry.description,
                        counts=entry.billable,
                    )
                )
        days.sort(key=lambda day: (day.date, day.source))

        visible = self._fee_visible(engagement)
        months: list[ReportMonth] = []
        month = rules.month_start(first)
        while month <= last:
            months.append(self._month(engagement, month, days, monthly_reports, today, visible))
            month = rules.next_month(month)

        payable = sum((m.payable_quantity for m in months), Decimal(0))
        held = sum((m.held_quantity for m in months), Decimal(0))
        return EngagementDaysReport(
            engagement_id=engagement.id,
            unit=unit,
            fee_unit=engagement.fee_unit,
            date_from=first,
            date_to=last,
            available=door is not None,
            note=note,
            days=days,
            months=months,
            total_quantity=sum((m.quantity for m in months), Decimal(0)),
            payable_quantity=payable,
            held_quantity=held,
            payable_amount=self._amount(engagement, payable, visible),
        )

    @staticmethod
    def _day(
        engagement: Engagement,
        approved: dict[dt.date, bool],
        held: dict[dt.date, set[dt.date]],
        *,
        date: dt.date,
        quantity: Decimal,
        source: str,
        status: str | None,
        description: str,
        counts: bool,
    ) -> ReportDay:
        month = rules.month_start(date)
        in_term = date >= engagement.starts_on and (
            engagement.ends_on is None or date <= engagement.ends_on
        )
        is_held = date in held.get(month, set())
        return ReportDay(
            date=date,
            quantity=quantity,
            source=source,  # type: ignore[arg-type]
            status=status,
            description=description,
            counts=counts,
            in_term=in_term,
            held=is_held,
            payable=counts and in_term and approved.get(month, False) and not is_held,
        )

    def _month(
        self,
        engagement: Engagement,
        month: dt.date,
        days: list[ReportDay],
        monthly_reports: dict[dt.date, MonthlyReport],
        today: dt.date,
        visible: bool,
    ) -> ReportMonth:
        mine = [d for d in days if rules.month_start(d.date) == month and d.counts and d.in_term]
        row = monthly_reports.get(month)
        payable = sum((d.quantity for d in mine if d.payable), Decimal(0))
        return ReportMonth(
            month=month,
            label=rules.month_label(month),
            monthly_report=read_monthly_report(row, today) if row is not None else None,
            quantity=sum((d.quantity for d in mine), Decimal(0)),
            held_quantity=sum((d.quantity for d in mine if d.held), Decimal(0)),
            payable_quantity=payable,
            payable_amount=self._amount(engagement, payable, visible),
        )

    @staticmethod
    def _amount(engagement: Engagement, quantity: Decimal, visible: bool) -> Decimal | None:
        """The fee times what is payable. None for a flat fee, which no quantity prices, and
        when the engagement's client is not in this administrator's view (the fee is the
        client's business, as `EngagementRead.redacted` says)."""
        if engagement.fee_unit == "flat" or not visible:
            return None
        return (quantity * engagement.fee).quantize(CENT)

    def _fee_visible(self, engagement: Engagement) -> bool:
        client = engagement.client_organization_id
        return client is None or self.repo.visible_organization(client) is not None


def _to_days(hours: Decimal) -> Decimal:
    """Hours at 8 a day, exact: an hour is 0.125 of a day, and only the amount is rounded."""
    return hours / HOURS_PER_DAY
