"""Composition, and the three week helpers. Nothing here invents a figure.

Spec 2026-09-16 §3.2: `build` calls services and repositories that already own their
numbers and copies the answers into `WeeklyDigest`. The only sums in the whole report are
`InvoiceRepository`'s own -- `sum_issued_in_period` twice with two windows for the two
months -- and they are that repository's, not this module's. The one arithmetic left here
is `days_of_delay`, `(today - scadenza).days`, which is not a figure but the same
subtraction `PaymentRemindersService` prints on the same invoices. The discipline is the one
`core/dashboard/` is held to by an AST scan, and it matters more here than there: a mail
is read once, quickly, by somebody who will not go and check, so a figure that disagrees
with the screen is a figure that costs trust and is never noticed again.

**The order of `build` is load-bearing at exactly one point.**
`DashboardService.get_operational_dashboard` opens a read-only `REPEATABLE READ` snapshot
and *refuses* a session that already has a transaction in progress, so it has to be the
first read this method makes. Everything after it then runs inside that snapshot, which is
the property the report wants anyway: seven sections read at seven instants is how a mail
comes to say «2 fatture emesse» above a list of three.

The rest of the order is only the order of §3.1, so that reading this method and reading
the mail are the same walk.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.config import Settings
from pigrocrm.core.dashboard.service import DashboardService, signals_for
from pigrocrm.core.db import current_week, month_bounds, today_local
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.digest.schemas import (
    DigestDealMove,
    DigestInvoice,
    DigestOffer,
    DigestSignal,
    DigestStage,
    DigestTask,
    DigestTaskLine,
    WeeklyDigest,
)
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import number_complete
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.modules import enabled_modules
from pigrocrm.core.space_settings.service import SpaceSettingsService
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.tasks.repository import TaskRepository
from pigrocrm.core.tasks.schemas import DAYS_NEXT
from pigrocrm.core.timetracking.repository import TimeEntryRepository

logger = logging.getLogger(__name__)

# §3.1: «quelle in scadenza nei prossimi sette giorni», counted from the day *after* the
# closed week -- this constant is only the width of that window.
#
# It does not, on its own, keep the two halves of «Da incassare» apart. `overdue` is
# «due on or before today» and this window opens on `a + 1`, which for the Monday the
# cron runs *is* today: an invoice due that day belongs to both, and for a week
# recomputed with `--date` the whole window has since gone by and every invoice in it is
# overdue. So `build` subtracts the `overdue` by id below, and the overdue half wins --
# it carries the days of delay, which is the fact somebody acts on.
IN_DUE_DATE_DAYS = 7

# How many commitment lines each list of «Da fare» carries; the rest is a count.
_TASK_SHOWN = 10

# How many stage changes are read *inside* the week's window. Two hundred is far more than
# a freelancer's week and still one bounded query; a space that moved more than that in a
# week has a report whose «deal mossi» list is truncated, which is better than a mail that
# takes a table scan to build. Since the window is in the query, the rows dropped by this
# limit are the week's own oldest ones, never a newer week's.
_MOVEMENTS_READ = 200

# The same ceiling the commercial dashboard puts on the same list.
_OFFERS_SHOWN = 20

# `activities.stage_changed` writes `{"from": <nome>, "to": <nome>}` -- the stage *names*,
# not their ids (`deals/service.py::move_stage`). So the destination needs no second query
# to resolve, and a stage renamed after the fact keeps the name it had when the deal moved,
# which is the honest thing for a record of what happened.
_STAGE_CHANGED = "stage_changed"
_DESTINATION = "to"
_ENTITY_DEAL = "deal"


def iso_week(day: date) -> str:
    """`2026-W38`: the ISO week the day belongs to.

    The report's identity (§3.3): `digests.week` is unique on this string, so it is
    what makes a cron that runs twice send once.

    The year comes from `isocalendar()` and never from `day.year`. 31 December 2026 is in
    ISO week 53 *of 2026* and 1 January 2027 is in the same one -- `f"{day.year}-W..."`
    would file those two days under two different identities and send the same week twice
    across every new year.

    Two digits on the week, always: `2026-W7` and `2026-W38` do not sort against each
    other as strings, and this value is read back out of a database column.
    """
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_containing(day: date) -> tuple[date, date]:
    """Monday to Sunday, both ends included, containing `day`.

    `current_week`'s rule applied to a day the caller names rather than to today: the
    command's `--data YYYY-MM-DD` (§3.4), which is how a week is recomputed for a test or
    a resend. Monday-first and inclusive at both ends for the reasons `db/clock.py` writes
    down once; two week conventions in one product is how a day gets counted twice.
    """
    monday = day - timedelta(days=day.isoweekday() - 1)
    return monday, monday + timedelta(days=6)


def previous_week(settings: Settings) -> tuple[date, date]:
    """The week that has just closed, in the emitter's zone.

    `current_week(settings)` shifted back seven days, and not `week_containing(today - 7)`:
    they agree, and going through the clock's own function means there is one definition of
    "which week is it" and this is a shift of it.

    `settings` is required rather than optional, unlike on `current_week`: the digest runs
    per space from a cron, and each space's settings are resolved by the caller
    (`space_base_settings`). A default that quietly read the platform's `get_settings()`
    would be right in every test and wrong for exactly the installation whose timezone
    differs.
    """
    monday, sunday = current_week(settings)
    return monday - timedelta(days=7), sunday - timedelta(days=7)


class DigestService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def build(self, actor: Actor, week: tuple[date, date]) -> WeeklyDigest:
        """The week as one object (§3.2).

        `week` is passed in and never computed here, so the command, a resend and a
        test all build the same report for the same seven days. `actor` is the space's
        owner, resolved by the caller (§3.3), and the operational dashboard is the one read
        that asks for it; nothing below needs more than a read right.

        The dashboard call is first because it opens the snapshot the rest of the method
        reads inside -- see the module docstring. Everything after it is a copy.
        """
        date_from, date_to = week

        # 1. The snapshot, and with it the backlog and the signals (§3.1 sections 2, 7).
        operational = DashboardService(self.session).get_operational_dashboard(actor)

        # 2. The register: the four lists and the two month totals.
        #
        #    `overdue` is «every unpaid, issued invoice due on or before today», read from
        #    the register itself. It was `PaymentRemindersService.candidates` until the branch
        #    review: that list answers «which invoices may I legitimately chase», so it
        #    drops anything inside the seven-day grace period, anything chased in the last
        #    few days and anything already at the reminder ceiling -- and the invoices it
        #    dropped are the ones that fell due *inside the reported week*, which is the
        #    money the report exists to name.
        today = today_local(self.settings)
        invoices = InvoiceRepository(self.session)
        overdue_rows = invoices.list_overdue(today)
        issued_rows = invoices.list_issued_in_period(date_from, date_to)
        paid_rows = invoices.list_paid_in_period(date_from, date_to)
        in_due_date_rows = invoices.list_in_due_date(
            date_to + timedelta(days=1), date_to + timedelta(days=IN_DUE_DATE_DAYS)
        )
        # One invoice, one line. The two windows meet on the day the cron runs -- `a + 1`
        # is today -- and overlap entirely for a week recomputed with `--date`, whose
        # «in scadenza» window has since gone by. The overdue half wins: it is the same
        # row, and it carries the days of delay instead of «in scadenza».
        overdue_ids = {row.id for row in overdue_rows}
        in_due_date_rows = [row for row in in_due_date_rows if row.id not in overdue_ids]
        # One lookup for all four lists, after the rows are chosen: a label query per row
        # is the N+1 `customer_names` exists to prevent.
        names = invoices.customer_names(
            {
                row.customer_id
                for row in (*overdue_rows, *issued_rows, *paid_rows, *in_due_date_rows)
            }
        )
        # The lateness expression is `PaymentRemindersService.candidates`' own, copied rather than
        # imported so that a list of debts and a list of chaseable invoices stay free to
        # diverge: `(today - scadenza).days`, over the clock's day in the emitter's zone.
        # The `is not None` guard is `list_overdue`'s own `WHERE` restated -- the column is
        # nullable and `mypy` reads it as such.
        overdue = [
            line
            for row in overdue_rows
            if (due_date := row.due_date) is not None
            and (line := self._line(row, names, due_date, days_of_delay=(today - due_date).days))
            is not None
        ]
        month_from, month_to = month_bounds(date_to.year, date_to.month)
        # The last day of the previous month is the day before this one's first, which is
        # the only way to name it without a calendar branch on January.
        previous = month_from - timedelta(days=1)
        previous_from, previous_to = month_bounds(previous.year, previous.month)

        # 4. Le ore, for the closed week -- not the dashboard's `week`, which is the
        #    *current* one and would report the days since the report's own subject ended.
        # What a switched-off module owns is not reported (spec 2026-11-09, A2): its sections
        # come out empty, which the mail already drops. Read from the space's own rows, since a
        # cron hands this job the installation's settings; after the dashboard, which must be
        # the first statement of the snapshot.
        modules_on = enabled_modules(SpaceSettingsService(self.session, self.settings).effective())
        deals_on = "deals" in modules_on
        time_on = "time" in modules_on
        entries = TimeEntryRepository(self.session)
        week_hours = entries.week_hours(date_from, date_to) if time_on else None
        # Absent, not zero: §3.1 drops the section entirely rather than printing a flat
        # line, because "no hours" and "seven days of nothing" read differently.
        hours = week_hours if week_hours is not None and week_hours.total_hours > 0 else None

        # 5. «Da emettere»: the count of won deals with unbilled hours, and the backlog.
        #    The same `COUNT` the dashboard's `won_to_invoice` signal ran above, asked
        #    again rather than fished out of `operational.signals` by its code: the report
        #    would then depend on a *label* staying spelled that way, and the two cannot
        #    disagree anyway -- both statements read the same `REPEATABLE READ` snapshot.
        won_to_invoice = entries.count_won_deals_to_invoice() if deals_on and time_on else 0

        # 6. In pipeline: the stages, the movements, the offers.
        deals = DealRepository(self.session)
        pipeline = [
            DigestStage(stage_name=row.stage_name, number=row.number, total_value=row.total_value)
            for row in (deals.pipeline_summary() if deals_on else [])
            if row.number > 0
        ]
        deal_moves = self._movements(deals, date_from, date_to) if deals_on else []
        offers = DocumentRepository(self.session).pending_offers(_OFFERS_SHOWN) if deals_on else []

        # The arrears again, for the modules this space has on: the dashboard's own figure
        # counts both sources.
        backlog = AnalyticsService(self.session).unbilled_backlog(actor, modules=modules_on)
        return WeeklyDigest(
            week=iso_week(date_from),
            date_from=date_from,
            date_to=date_to,
            overdue=overdue,
            in_due_date=self._lines(in_due_date_rows, names, lambda row: row.due_date),
            won_to_invoice=won_to_invoice,
            unbilled_hours=backlog.billable_uninvoiced_hours or Decimal("0"),
            accrued_value=backlog.accrued_value,
            issued=self._lines(issued_rows, names, lambda row: row.issue_date),
            total_month_current=invoices.sum_issued_in_period(month_from, month_to),
            total_month_previous=invoices.sum_issued_in_period(previous_from, previous_to),
            paid=self._lines(paid_rows, names, lambda row: row.payment_date),
            hours=hours,
            pipeline=pipeline,
            deal_moves=deal_moves,
            offers_pending=[
                DigestOffer(
                    # `PendingOffer` carries its ids as strings, because the commercial
                    # dashboard serialises straight to JSON; this model keeps the `UUID`
                    # every other id in the report is, so the mail's link is built the
                    # same way for an offer as for an invoice.
                    document_id=UUID(offer.document_id),
                    title=offer.title,
                    # `PendingOffer.days` is `None` when the offer has no `status_since`,
                    # which for a *sent* offer only happens on a row migrated before that
                    # column existed. The mail has one column for the age, so it reads 0
                    # there; the honest `None` stays on the dashboard, which has room to
                    # render it as a dash.
                    days=offer.days if offer.days is not None else 0,
                )
                for offer in offers
            ],
            signals=[
                DigestSignal(
                    code=signal.code,
                    label=signal.label,
                    count=signal.count,
                    # Every signal has a drill-through today and
                    # `test_dashboard_operational.py` is what keeps it that way; a mail
                    # cannot print a link it does not have, so an absent one is an empty
                    # string the renderer skips rather than a crash on Monday morning.
                    link=signal.link or "",
                )
                for signal in signals_for(operational.signals, modules_on)
                # §3.1 section 7: the section exists only when at least one fired. A mail
                # that reports three zeroes every week is a mail nobody opens.
                if signal.count > 0
            ],
        )

    def task_of(self, user_id: UUID) -> DigestTask:
        """One person's week of commitments: their overdue ones and those due from today to
        seven days on (REB-726).

        Read from the same repository the Oggi panel reads, with the same windows, so the
        mail and the page cannot disagree about what is late. `today` is the emitter's
        day, like `overdue`'s, and the window closes at `oggi + 7` as the page's «prossimi
        sette giorni» does. Assigned to `user_id` only: a commitment nobody has taken is
        nobody's mail.
        """
        today = today_local(self.settings)
        repo = TaskRepository(self.session)

        def lines(rows: list[Task], *, delay: bool) -> list[DigestTaskLine]:
            # `due_date` is never `None` here: both windows are date predicates.
            return [
                DigestTaskLine(
                    task_id=row.id,
                    title=row.title,
                    due_date=row.due_date,
                    days_of_delay=(today - row.due_date).days if delay else None,
                )
                for row in rows
                if row.due_date is not None
            ]

        total_delay, overdue = repo.open_in_the_window(
            assigned_to=user_id, limit=_TASK_SHOWN, before_of=today
        )
        total_week, in_week = repo.open_in_the_window(
            assigned_to=user_id,
            limit=_TASK_SHOWN,
            date_from=today,
            date_to=today + timedelta(days=DAYS_NEXT),
        )
        return DigestTask(
            overdue=lines(overdue, delay=True),
            overdue_total=total_delay,
            in_week=lines(in_week, delay=False),
            in_week_total=total_week,
        )

    def _lines(
        self,
        rows: Sequence[Invoice],
        names: dict[UUID, str],
        date_of: Callable[[Invoice], date | None],
    ) -> list[DigestInvoice]:
        """`_line` over a list, leaving out the rows it declines to print."""
        return [line for row in rows if (line := self._line(row, names, date_of(row))) is not None]

    def _line(
        self,
        invoice: Invoice,
        names: dict[UUID, str],
        data: date | None,
        *,
        days_of_delay: int | None = None,
    ) -> DigestInvoice | None:
        """One register row as a mail line.

        `data` is passed in rather than picked here: which of three dates a section is
        about is the section's business, and a method that decided for itself would need to
        know which list it was building -- exactly the branch `DigestInvoice`'s single
        shape exists to avoid. `days_of_delay` is passed the same way and for the same
        reason, and only «Da incassare» passes it: zero days late is a thing an invoice can
        be, so `None` and `0` are two different statements about a row.

        The fallbacks are the columns' own nullability and not defensive padding:
        `year`/`number` are `NULL` on a draft and `issue_date` on anything unissued,
        and none of the three predicates above can return such a row. They are here because
        the model's types are not `| None` and a `mypy` cast would hide the same fact
        without stating it.

        A row with no date at all is the one case that gets no fallback. `date.min` would
        put «lun 1 gen» of the year 1 in a mail and go on being sent every Monday; the
        three predicates cannot produce it, so if it ever arrives the register holds
        something this report has no honest line for. That one row is left out and its id
        logged: one odd invoice must not cost the whole space its week. `None` says so.
        """
        day = data or invoice.issue_date or invoice.due_date
        if day is None:
            logger.warning("digest: invoice %s has no date to print, row skipped", invoice.id)
            return None
        return DigestInvoice(
            invoice_id=invoice.id,
            number=number_complete(invoice.year or 0, invoice.number or 0),
            customer=names.get(invoice.customer_id, ""),
            amount=invoice.total,
            date=day,
            status=invoice.status,
            payment_status=invoice.payment_status,
            # `transmitted_externally_at` is the day the document was deposited with the
            # user's own intermediary: the register's whole notion of «trasmessa», and a
            # date rather than a state because `status` already carries two state machines.
            # The mail prints the word, so it needs the fact and not the day.
            transmitted=invoice.transmitted_externally_at is not None,
            days_of_delay=days_of_delay,
        )

    def _movements(
        self, deals: DealRepository, date_from: date, date_to: date
    ) -> list[DigestDealMove]:
        """The deals that changed stage inside the week (§3.1, section 6).

        Read by kind **and by window**, never by kind alone: `by_kind` answers the newest
        `limit` rows of the whole table, so filtering them here in Python would hand this
        week whatever is left after everything written since. The cron never notices --
        it reports the week that has just closed -- and `pigrocrm digest --data` for an
        older week silently loses `deal_mossi` as soon as two hundred stage changes have
        happened since. The period belongs in the query (`by_kind_between`).

        `occurred_at` is an *instant*, and the week is seven **days in the emitter's zone**
        (`db/clock.py`). The bounds are converted here exactly as the rows used to be: the
        week is «from midnight on the Monday to midnight on the next one, in the emitter's
        zone», which `by_kind_between` reads half-open. A deal moved at 00:30 on that
        Monday is still Sunday in UTC, and a window written in UTC days would file the
        movement in the week before the one it belongs to -- which, on a Monday-first
        calendar, is the week that has already been reported.

        One query for every title, never one per row.
        """
        zone = ZoneInfo(self.settings.timezone)
        rows = ActivityRepository(self.session).by_kind_between(
            [_STAGE_CHANGED],
            datetime.combine(date_from, time.min, tzinfo=zone),
            datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone),
            limit=_MOVEMENTS_READ,
        )
        lines = [
            (row, row.occurred_at.astimezone(zone).date())
            for row in rows
            if row.entity_type == _ENTITY_DEAL
        ]
        titles = deals.names({row.entity_id for row, _ in lines})
        return [
            DigestDealMove(
                deal_id=row.entity_id,
                title=titles[row.entity_id],
                stage_name=str(row.payload[_DESTINATION]),
                when=day,
            )
            for row, day in lines
            # A deal that no longer exists at all: the activity outlived its row, so there
            # is no title to print and the line would say nothing. Soft-deleted deals are
            # still found by `names` and still appear -- an archived deal did move.
            if row.entity_id in titles and row.payload.get(_DESTINATION)
        ]
