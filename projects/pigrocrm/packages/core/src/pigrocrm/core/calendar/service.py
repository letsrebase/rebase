from collections.abc import Set as AbstractSet
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.calendar.repository import CalendarRepository
from pigrocrm.core.calendar.schemas import (
    MONTH_RE,
    WITHOUT_DUE_DATE_LIMIT,
    CalendarDay,
    CalendarMonth,
    DayDealHours,
    DayWorkUnit,
    DueInvoice,
)
from pigrocrm.core.db import month_bounds as db_month_bounds
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.money import ZERO_HOURS, sum_hours
from pigrocrm.core.tasks.repository import TaskRepository
from pigrocrm.core.tasks.schemas import TaskRead

ENTITY = "calendar"


def month_bounds(month: str) -> tuple[date, date]:
    """`AAAA-MM` into the first and last day of that month.

    Server-side, so the client never has to agree with the server about how long
    February is -- and validated before it is parsed, so `2026-13` reads as «non è un
    mese» instead of arriving as a `ValueError` from the standard library.

    """
    if not MONTH_RE.fullmatch(month):
        raise ValidationFailed(
            ENTITY, "month", "non è un mese valido", expected="AAAA-MM, per esempio 2026-09"
        )
    year, number = (int(part) for part in month.split("-"))
    return db_month_bounds(year, number)


class CalendarService:
    """One month of the calendar, assembled from four reads: the hours, the activities,
    the invoices falling due and the contract days (REB-748).

    In `packages/core` and not in the router, because it is the same reading the web app
    and the agent both want: putting it in an adapter would mean either the agent does
    not have it or it exists twice. Slice 10 §6.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CalendarRepository(session)
        self.tasks = TaskRepository(session)

    def month(
        self,
        month: str,
        actor: Actor,
        *,
        user_id: UUID | None = None,
        legal_entity_id: UUID | None = None,
        modules: AbstractSet[str] | None = None,
    ) -> CalendarMonth:
        """Every day of `month` that has hours, activities, a contract day or an invoice falling
        due.

        `user_id` scopes the hours to one person. The default is `actor.id`, which is
        what «my calendar» means and what every caller wants; passing `None` explicitly
        is how an admin asks for the whole space, and passing another id is how they ask
        about somebody else -- a decision this method does not second-guess, because
        reading hours is not gated anywhere else in this product either.
        """
        date_from, date_to = month_bounds(month)
        today = today_local()

        # `modules` is what the space has on (`None`: everything): hours belong to `time` and
        # contract days to `contracts`, and a switched-off module's part is not drawn (spec
        # 2026-11-09, A2). Its records stay where they are.
        # `legal_entity_id` narrows both reads to one legal entity's deals and invoices (REB-623).
        hours = (
            self.repo.hours_by_day_and_deal(
                date_from, date_to, user_id=user_id, legal_entity_id=legal_entity_id
            )
            if modules is None or "time" in modules
            else []
        )
        labels = self.repo.deal_labels({deal_id for _, deal_id, _ in hours})

        days: dict[date, CalendarDay] = {}

        def day(when: date) -> CalendarDay:
            """The day's bucket, created on first mention. A `setdefault` over a dict
            rather than thirty-one pre-built objects: §6 says a day with nothing on it
            is not in the response, and pre-building them would make emptying one
            impossible to distinguish from never having filled it."""
            return days.setdefault(when, CalendarDay(day=when, hours=ZERO_HOURS))

        for when, deal_id, hours_of_deal in hours:
            name, customer = labels.get(deal_id, ("(deal archiviato)", None))
            bucket = day(when)
            bucket.per_deal.append(
                DayDealHours(
                    deal_id=deal_id, deal_name=name, customer=customer, hours=hours_of_deal
                )
            )

        # The day's total is the sum of its own rows, through `money.sum_hours`: adding
        # the per-deal figures here and separately in SQL would be two totals of the same
        # hours, which is how they begin to disagree.
        for bucket in days.values():
            bucket.hours = sum_hours(line.hours for line in bucket.per_deal)

        for row in self.tasks.in_range(date_from, date_to):
            # `in_range` filters on `scadenza IS NOT NULL`, so this is never None -- and
            # the guard is here rather than an `assert` because an assert that fires in
            # production is a traceback shown to somebody who can only read it as a
            # crash.
            due_date_task = row.due_date
            if due_date_task is None:  # pragma: no cover - excluded by the query
                continue
            day(due_date_task).tasks.append(TaskRead.model_validate(row))

        for invoice, customer in self.repo.invoices_due(
            date_from, date_to, legal_entity_id=legal_entity_id
        ):
            due_date = invoice.due_date
            if due_date is None:  # pragma: no cover - excluded by the query itself
                continue
            day(due_date).invoices.append(
                DueInvoice(
                    id=invoice.id,
                    number=_number(invoice.number, invoice.year),
                    customer=customer,
                    # What the customer owes, net of a withholding or a split payment, like
                    # every receivable (`net_receivable`).
                    total=invoice.net_receivable,
                    due_date=due_date,
                    # `<`, like `_overdue_predicate`: due today is due today.
                    overdue=due_date < today,
                )
            )

        for work_unit, contract_title, customer_name in (
            self.repo.work_units_in_window(date_from, date_to, legal_entity_id=legal_entity_id)
            if modules is None or "contracts" in modules
            else []
        ):
            day(work_unit.date).work_units.append(
                DayWorkUnit(
                    id=work_unit.id,
                    contract_id=work_unit.contract_id,
                    contract_title=contract_title,
                    customer_name=customer_name,
                    quantity=work_unit.quantity,
                    status=work_unit.status,
                )
            )

        return CalendarMonth(
            month=month,
            date_from=date_from,
            date_to=date_to,
            today=today,
            total_hours=sum_hours(bucket.hours for bucket in days.values()),
            days=[days[when] for when in sorted(days)],
            task_without_due_date=[
                TaskRead.model_validate(row)
                for row in self.tasks.without_due_date(limit=WITHOUT_DUE_DATE_LIMIT)
            ],
        )


def _number(number: int | None, year: int | None) -> str | None:
    """«3/2026», the way a person reads an invoice number, or `None` if it has none.

    Only an issued invoice reaches here and an issued one always has both halves, so the
    `None` is the type being honest rather than a case anybody expects.
    """
    if number is None or year is None:
        return None
    return f"{number}/{year}"
