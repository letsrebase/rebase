"""Every Create/Update/Read/ListQuery/Page for the four tables of this slice.

`max_length` mirrors every `String(n)`; `max_digits`/`decimal_places` mirror every
`Numeric(p, s)`; `SafeStr` guards every user-supplied string. Without those an
over-long value, an over-capacity number or a NUL byte reaches `flush()` and comes
back as a raw `DataError` -- not an `IntegrityError`, so no handler catches it, and the
caller's session is poisoned. This project has closed that family of defects seven
times; it is not reopened on four new tables.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.money import FACTOR_SCALE, HOURS_SCALE
from pigrocrm.core.purchases.deductibility import DeductibilityKind
from pigrocrm.core.validation import SafeStr

# Mirror the columns in models.py.
DESCRIPTION_MAX_LENGTH = 2000  # `Text`, so no column width to mirror; bounded anyway
SUPPLIER_MAX_LENGTH = 200
CATEGORY_NAME_MAX_LENGTH = 60
HOURS_MAX_DIGITS = 8
MONEY_MAX_DIGITS = 12
FACTOR_MAX_DIGITS = 12

# `hours` is bounded here as well as by `ck_time_entries_ore_range`, not instead of it:
# the schema bound turns a 422 into a message naming the field before a statement is
# ever issued, and the CHECK covers every other write path including raw SQL. `gt=0`
# not `ge=0`: a zero-hour entry is not an entry, and the way to cancel a wrong one is
# a reversible soft delete (§2.2, last row), not a zero.
HOURS_MIN = Decimal("0.01")
HOURS_MAX = Decimal("24.00")

CATEGORY_POSITION_MIN = 0
CATEGORY_POSITION_MAX = 100_000
YEAR_MIN = 2000
YEAR_MAX = 2200

RateOrigin = Literal["manual", "deal", "user", "absent"]
# `cost_source` never takes `deal`: an hour's cost is a property of who works it,
# not of the client they work it for (§5.1). Declared as its own narrower Literal
# rather than reusing RateOrigin, so the impossible value is unrepresentable instead
# of merely unwritten.
CostOrigin = Literal["manual", "user", "absent"]


class TimeEntryCreate(BaseModel):
    deal_id: UUID
    user_id: UUID
    date: dt.date
    hours: Decimal = Field(
        max_digits=HOURS_MAX_DIGITS, decimal_places=HOURS_SCALE, ge=HOURS_MIN, le=HOURS_MAX
    )
    description: SafeStr = Field(max_length=DESCRIPTION_MAX_LENGTH)
    billable: bool = True
    # Level 1 of §5.1's resolution: an explicit value in the request wins, and the
    # frozen column is written with it and `origine = "manuale"`. Named identically to
    # the columns on purpose -- `native_fields("time_entry")` is derived from this
    # model, so a differently-named input would leave `applied_rate` outside the
    # A13 guard's reach and a custom field could still collide with it.
    applied_rate: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    applied_cost: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    internal_notes: SafeStr | None = None
    custom_fields: dict[str, Any] = {}


class TimeEntryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `deal_id` and `user_id` are both here: §4.3 lists `deal_id` among the fields
    # frozen once billed, which means it is mutable while it is not, and criterion 8
    # requires reassigning an existing entry to a user to be attempted and refused
    # when that user is deactivated -- an operation that needs the field to exist.
    deal_id: UUID | None = None
    user_id: UUID | None = None
    date: dt.date | None = None
    hours: Decimal | None = Field(
        default=None,
        max_digits=HOURS_MAX_DIGITS,
        decimal_places=HOURS_SCALE,
        ge=HOURS_MIN,
        le=HOURS_MAX,
    )
    description: SafeStr | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    billable: bool | None = None
    applied_rate: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    applied_cost: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    internal_notes: SafeStr | None = None
    custom_fields: dict[str, Any] | None = None


class TimeEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deal_id: UUID
    user_id: UUID
    date: dt.date
    hours: Decimal
    description: str
    billable: bool
    applied_rate: Decimal | None
    applied_cost: Decimal | None
    rate_source: RateOrigin
    cost_source: CostOrigin
    # Derived and returned already computed, because §6 forbids the browser from doing
    # any economic arithmetic. `None` when the corresponding factor is `None` -- never
    # `0.00`, which would say the work was free.
    line_value: Decimal | None = None
    line_cost: Decimal | None = None
    invoice_line_id: UUID | None
    internal_notes: str | None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TimeEntryListQuery(BaseModel):
    deal_id: UUID | None = None
    # Through the deal: an entry's legal entity is its deal's (REB-623).
    legal_entity_id: UUID | None = None
    user_id: UUID | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    billable: bool | None = None
    # Three states, not two: `True` = already on an invoice line, `False` = not yet,
    # `None` = do not filter. It is the "how much do I have to invoice?" query.
    invoiced: bool | None = None
    custom: dict[str, Any] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class TimeEntryPage(BaseModel):
    items: list[TimeEntryRead]
    next_cursor: UUID | None


class TimerStart(BaseModel):
    """What a timer knows when it starts. Everything optional but the intent: a deal
    can be chosen later, a description typed later, and `billable` defaults the way a
    freelancer's hour does."""

    deal_id: UUID | None = None
    description: SafeStr = Field(default="", max_length=DESCRIPTION_MAX_LENGTH)
    billable: bool = True


class TimerUpdate(BaseModel):
    """Changed while running: the person realises which deal this is, or what to call it."""

    deal_id: UUID | None = None
    description: SafeStr | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    billable: bool | None = None


class TimerStop(BaseModel):
    """What may still be decided at the moment of stopping. `data` is the calendar day the
    entry lands on; absent, it is today in the emitter's zone (`today_local`), never the
    UTC day the server happens to be in -- a timer stopped at 00:30 in Rome belongs to the
    day that has just begun there."""

    deal_id: UUID | None = None
    description: SafeStr | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    date: dt.date | None = None


class TimerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    deal_id: UUID | None
    description: str
    billable: bool
    started_at: datetime


class CostCategoryCreate(BaseModel):
    name: SafeStr = Field(max_length=CATEGORY_NAME_MAX_LENGTH)
    position: int = Field(default=0, ge=CATEGORY_POSITION_MIN, le=CATEGORY_POSITION_MAX)
    deductibility_kind: DeductibilityKind | None = None
    # `code` is never caller-supplied: it is the stable identity of a *seeded*
    # category, and letting a user claim one would let them collide with a future seed.
    # Absent from this schema on purpose, exactly as `code` is absent from
    # `PipelineStageUpdate`.


class CostCategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SafeStr | None = Field(default=None, max_length=CATEGORY_NAME_MAX_LENGTH)
    position: int | None = Field(default=None, ge=CATEGORY_POSITION_MIN, le=CATEGORY_POSITION_MAX)
    deductibility_kind: DeductibilityKind | None = None


class CostCategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    position: int
    code: str | None
    archived: bool
    # The sort of cost the law limits that every purchase of the category is, when a person has
    # said (`purchases/deductibility.py`, REB-1089); a purchase's own kind overrides it.
    deductibility_kind: DeductibilityKind | None
    created_at: datetime
    updated_at: datetime


class PeriodLockCreate(BaseModel):
    year: int = Field(ge=YEAR_MIN, le=YEAR_MAX)
    month: int = Field(ge=1, le=12)


class PeriodLockRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    year: int
    month: int
    closed_at: datetime
    closed_by: UUID | None


class RateDescription(BaseModel):
    """What `log_time` *would* freeze onto a new entry right now.

    Exists so an agent can read before it writes (slice 1 §8.4) without having to
    guess which of the three levels answers -- and so the UI can show it next to the
    hours field instead of leaving the user to discover it after saving.
    """

    deal_id: UUID
    user_id: UUID
    rate: Decimal | None
    rate_source: RateOrigin
    cost: Decimal | None
    cost_source: CostOrigin


class DealTimeSummary(BaseModel):
    """The hours half of a deal's economics, computed in 4A and readable with no
    invoices in the database.

    Deliberately **not** carrying `revenue` or `accrued_value`: revenue is the invoice
    (§3, decision 2) and there is no second notion of it. 4B's `DealPnl` adds `revenue`
    and defines `valore_maturato = ricavi + valore_ore_non_fatturate` on top of this,
    rather than 4A shipping a zero that would read as a real figure.
    """

    deal_id: UUID
    status: Literal["in_progress", "to_invoice", "closed"]
    total_hours: Decimal
    billable_uninvoiced_hours: Decimal
    unbilled_hours_value: Decimal
    work_cost: Decimal
    hours_without_rate: int
    entries: int


class RecalculateRatesRequest(BaseModel):
    date_from: dt.date
    date_to: dt.date


class UserRatesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_hourly_rate: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    default_hourly_cost: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )


class DealRateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hourly_rate: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
