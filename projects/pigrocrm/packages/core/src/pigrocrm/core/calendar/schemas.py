import re
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from pigrocrm.core.tasks.schemas import TaskRead

# `AAAA-MM`, and validated as a *pattern* before anything is parsed: the month reaches
# `date(anno, mese, 1)`, and a caller who sends `2026-13` should read «non è un mese»
# rather than a `ValueError` from the standard library. The years are the same bounds
# `invoices/schemas.py` puts on `year`, for the same reason -- a calendar of the year
# 9999 is a typo, not a request.
MONTH_PATTERN = r"^\d{4}-(?:0[1-9]|1[0-2])$"
MONTH_RE = re.compile(MONTH_PATTERN)
# How many undated commitments travel with a month. See
# `TaskRepository.without_due_date` for why there is a cap at all.
WITHOUT_DUE_DATE_LIMIT = 50


class DayDealHours(BaseModel):
    """The hours of one deal on one day, with the labels a grid needs to draw them.

    `deal_name` and `customer` are resolved once for the whole month, not per row: a
    month with sixty entries costs two label queries and never sixty. `customer` is
    nullable because a deal's customer is nullable nowhere -- but a soft-deleted
    customer still answers with its name, deliberately: archiving a client did not
    unhappen the work.
    """

    deal_id: UUID
    deal_name: str
    customer: str | None = None
    hours: Decimal


class DueInvoice(BaseModel):
    """An issued, unpaid invoice falling due on a day of the month being read.

    Read-only in the calendar, and the spec says why (§2): the due date is a property of
    the invoice, decided when the document was issued or imported, and changing it from
    a view that shows neither the number nor the amount would be changing a fiscal
    document from the wrong place.

    `number` is the number as a person reads it -- «3/2026» -- assembled here because the
    two columns that make it are `year` and `number` and a client should not have to know
    the format. Nullable in the model, never in practice for these rows: only an issued
    invoice is here, and an issued invoice has a number.
    """

    id: UUID
    number: str | None = None
    customer: str | None = None
    total: Decimal
    due_date: date
    # `True` when the due date is strictly in the past, by the emitter's clock -- the
    # same `<` as `_overdue_predicate`: due today is due today, not late.
    overdue: bool


class DayWorkUnit(BaseModel):
    """One contract day on the calendar: which contract, how much of a day, and the state
    a cell marks it with. `status` is the ledger's own word (`worked_without_approval`
    among them), so the cell can say a day was worked without the approval its contract
    requires rather than drawing it like any other."""

    id: UUID
    contract_id: UUID
    contract_title: str
    customer_name: str
    quantity: Decimal
    status: str


class CalendarDay(BaseModel):
    """One day that has something on it.

    A day with nothing is **not** in the response (§6): the client knows how many days
    the month has and draws the empty ones, and thirty-one empty objects per request
    would be noise on every read.
    """

    day: date
    hours: Decimal
    per_deal: list[DayDealHours] = Field(default_factory=list)
    attivita: list[TaskRead] = Field(default_factory=list)
    invoices: list[DueInvoice] = Field(default_factory=list)
    work_units: list[DayWorkUnit] = Field(default_factory=list)


class CalendarMonth(BaseModel):
    """One month, in one read.

    `da` and `a` are the first and last day of the month, computed server-side so the
    client never has to agree with the server about how long February is. `today` travels
    too, because «today» is a day in the emitter's zone (`db/clock.py`) and a browser in
    another timezone would mark the wrong cell.

    `task_without_due_date` sits outside `days` and that is the whole point: an
    activity with no date cannot be drawn in a cell, is not late and is not for today.
    The section under the grid is where it belongs -- a NULL is not zero days.
    """

    month: str = Field(pattern=MONTH_PATTERN)
    da: date
    a: date
    today: date
    total_hours: Decimal
    days: list[CalendarDay] = Field(default_factory=list)
    task_without_due_date: list[TaskRead] = Field(default_factory=list)
