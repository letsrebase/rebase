from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection, SortSpec, SortWhitelist
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.validation import SafeStr

# Mirror the column widths in models.py, for the reason `people/schemas.py` records at
# length: without them an over-length value sails past Pydantic, reaches `flush()` and
# comes back as a raw `DataError` that no handler catches and that poisons the session.
# `note` has none because it is `Text`.
TITLE_MAX_LENGTH = 200
RULE_MAX_LENGTH = 50

# `String(20)` plus a Literal, never a Postgres ENUM: a new value costs a constant and
# not an `ALTER TYPE` -- the rule slice 2 set for `DocumentType` and every state since.
TaskStatus = Literal["open", "completed", "cancelled"]
TaskOrigin = Literal["manual", "automation", "payment_reminder"]
# The four entities an activity may hang from, as the wire spells them. A tuple and not
# a set so the order is the one the check below reports in.
REFERENCES = ("customer_id", "person_id", "deal_id", "invoice_id")


class TaskCreate(BaseModel):
    title: SafeStr = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    note: SafeStr | None = None
    # Optional, and that is the whole point of the model: see `Task`'s docstring and
    # spec §3.2. A caller that has no date must be able to say so, and «today» is not a
    # sensible default for a commitment.
    due_date: date | None = None
    assigned_to: UUID | None = None
    customer_id: UUID | None = None
    person_id: UUID | None = None
    deal_id: UUID | None = None
    invoice_id: UUID | None = None
    custom_fields: dict[str, Any] = {}


class TaskUpdate(BaseModel):
    # `extra="forbid"`, like every other update schema here: a misspelt key is a 422 and
    # never a silently ignored change.
    model_config = ConfigDict(extra="forbid")

    title: SafeStr | None = Field(default=None, max_length=TITLE_MAX_LENGTH)
    note: SafeStr | None = None
    due_date: date | None = None
    assigned_to: UUID | None = None
    customer_id: UUID | None = None
    person_id: UUID | None = None
    deal_id: UUID | None = None
    invoice_id: UUID | None = None
    custom_fields: dict[str, Any] | None = None
    # The two fields that clear rather than set, and why they are needed at all: every
    # other nullable column here is set by naming it, and PATCH cannot tell «absent»
    # from «null» through `supplied_changes`. A commitment whose date turns out to be
    # wrong has to be able to lose it -- an activity with an invented date is exactly
    # what §3.2 refuses -- and one that was hung on the wrong record has to be able to
    # hang on none.
    due_date_to_remove: bool = False
    reference_to_remove: bool = False


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    note: str | None
    due_date: date | None
    status: TaskStatus
    completed_at: date | None
    assigned_to: UUID | None
    customer_id: UUID | None
    person_id: UUID | None
    deal_id: UUID | None
    invoice_id: UUID | None
    origin: TaskOrigin
    rule: str | None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TaskListQuery(BaseModel):
    # Filters an interface really asks for. `status` defaults to `None` -- «every state»
    # -- rather than to `open`: a list that silently hides what was completed is a
    # list somebody will use to conclude the work was never done.
    status: TaskStatus | None = None
    assigned_to: UUID | None = None
    # Only read together with `assigned_to`: «this person's, and nobody's».
    include_unassigned: bool = False
    customer_id: UUID | None = None
    person_id: UUID | None = None
    deal_id: UUID | None = None
    invoice_id: UUID | None = None
    # `due_within` is inclusive and, deliberately, excludes the activities with no
    # date: a filter by date cannot answer for rows that have none, and including them
    # would make «due within Friday» a list containing things that are not due at all.
    due_within: date | None = None
    # And the explicit way to ask for exactly those: `True` for the ones without a date,
    # `False` for the ones with one, `None` for both.
    without_due_date: bool | None = None
    # The archived ones, and only those: `False` (the default) is the live list and `True`
    # its complement, so a screen that offers «ripristina» can find what it restores. Two
    # lists and not a flag that mixes them, because an archived row in the live list is
    # exactly the thing archiving is for hiding.
    archived: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=CURSOR_MAX_LENGTH)
    sort: SafeStr | None = None
    dir: SortDirection = "asc"


class TaskPage(BaseModel):
    items: list[TaskRead]
    next_cursor: str | None


# How far «prossimi sette giorni» reaches past today, inclusive (spec 2026-09-03 §5). Here
# and not in the service so the weekly digest reads the same window without importing it.
DAYS_NEXT = 7


class TaskTodayQuery(BaseModel):
    """What the Oggi panel can be asked: whose attività (everybody's when `None`) and how
    many rows each section carries. Validated here and not only by the REST router, so an
    agent's `limit` of 0 or of «molti» is a sentence and not a database error."""

    assigned_to: UUID | None = None
    # Only read together with `assigned_to`: «this person's, and nobody's».
    include_unassigned: bool = False
    limit: int = Field(default=100, ge=1, le=200)


class TaskGroup(BaseModel):
    """One section of the Oggi panel: the first `limit` rows and how many there are in all."""

    total: int
    items: list[TaskRead]


class TaskToday(BaseModel):
    """The four sections of «Oggi» (spec 2026-09-03 §5), in the order the page shows them.

    `today` is the day the sections were cut on, the emitter's (`today_local`), so a client
    in another zone sees the same four lists the server counted. Every section is open
    commitments only; «prossimi sette giorni» opens the day after `today` and closes seven
    days on, both inclusive.
    """

    today: date
    overdue: TaskGroup
    today_due: TaskGroup
    next_seven_days: TaskGroup
    without_due_date: TaskGroup


# Residuo R9. Two keys and not three: `due_date`, because a list of commitments is read
# by date, and `created_at` as the default and the tiebreaker of last resort. `due_date`
# is nullable, so it is the spec for which `order_by` emits NULLS LAST in both
# directions and the reason `attivita` carries a second, descending index -- see
# `people/schemas.py::PERSON_SORTS` for the full argument.
TASK_SORTS = SortWhitelist(
    specs=(
        SortSpec(key="created_at", column=Task.created_at, kind="datetime", nullable=False),
        SortSpec(key="due_date", column=Task.due_date, kind="date", nullable=True),
    ),
    default_key="created_at",
)
