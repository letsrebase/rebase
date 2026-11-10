"""The freelancer's own view of an engagement (brief `2026-11-10-engagement-pages-brief.md`,
«The freelancer's own view»), built on rebase's side and handed over whole.

It is the one place that decides what a freelancer may read of the engagement rebase holds for
them: the letter, their own fee and dates, the payment rule, and for each month whether rebase has
the report and has approved it. It is written as a closed list of fields, never as a filter over
`EngagementRead`, so a field added to the engagement tomorrow (the client's name, a budget, a
forecast) cannot reach a freelancer by being added.
"""

import datetime as dt

from sqlalchemy.orm import Session

from pigrocrm.core.collaborators import rules
from pigrocrm.core.collaborators.models import Engagement, MonthlyReport
from pigrocrm.core.collaborators.monthly_reports import monthly_reports_of
from pigrocrm.core.collaborators.schemas import OwnEngagementMonth, OwnEngagementRead


def own_view(session: Session, engagement: Engagement, today: dt.date) -> OwnEngagementRead:
    """The engagement as its freelancer reads it. A month with no report received is
    `in_progress` (the running one and one whose report has not arrived), a month received and
    not yet decided is `received`, and a decided month, or one whose tacit date passed with no
    objection, is `approved`."""
    reports = {row.month: row for row in monthly_reports_of(session, engagement.id)}
    months: list[OwnEngagementMonth] = []
    month = rules.month_start(engagement.starts_on)
    last = rules.month_start(min(engagement.ends_on or today, today))
    while month <= last:
        months.append(_month(month, reports.get(month), today))
        month = rules.next_month(month)
    return OwnEngagementRead(
        letter_number=engagement.letter_number,
        state=engagement.state,
        role=engagement.role,
        starts_on=engagement.starts_on,
        ends_on=engagement.ends_on,
        fee=engagement.fee,
        fee_unit=engagement.fee_unit,
        days_per_week=engagement.days_per_week,
        payment_rule=engagement.payment_rule,
        payment_days=engagement.payment_days,
        payment_end_of_month=engagement.payment_end_of_month,
        collection_days=engagement.collection_days,
        months=months,
    )


def _month(month: dt.date, row: MonthlyReport | None, today: dt.date) -> OwnEngagementMonth:
    label = rules.month_label(month)
    if row is None:
        return OwnEngagementMonth(
            month=month,
            label=label,
            state="in_progress",
            received_on=None,
            answer_by=None,
            held_dates=[],
        )
    if rules.effective_state(row.state, row.tacit_on, today) == "requested":
        return OwnEngagementMonth(
            month=month,
            label=label,
            state="received",
            received_on=row.received_on,
            answer_by=row.tacit_on,
            held_dates=[],
        )
    return OwnEngagementMonth(
        month=month,
        label=label,
        state="approved",
        received_on=row.received_on,
        answer_by=None,
        held_dates=[dt.date.fromisoformat(value) for value in row.held_dates],
    )
