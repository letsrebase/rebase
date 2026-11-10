"""What the cash schedule, the payables mirror and the phone summary return (spec 2026-11-09,
due items and the cash schedule, § 4).

Every balance, total, difference and count arrives already computed, and money is a `Decimal`
field the wire writes as a string with two decimals: the browser adds nothing. The constants
the screen needs (the horizon options, the stale grace) are returned in the answer rather than
repeated in a component.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.dashboard.schemas import BandDueDate
from pigrocrm.core.modules.registry import Profile

# The horizons the schedule offers, in weeks; the second is the default.
HORIZON_OPTIONS: tuple[int, ...] = (4, 13, 26)
DEFAULT_HORIZON_WEEKS = 13
# The one currency the figures of the schedule are in. An item or an account in another
# currency is left out of every sum (the exchange rate has no verified source yet).
SCHEDULE_CURRENCY = "EUR"

MONEY_DIGITS = 14

ScheduleProfile = Profile
AlertKind = Literal["below_zero", "missing_statement", "stale_balance"]
ItemState = Literal["forecast", "expected"]


class CashScheduleQuery(BaseModel):
    """`weeks` is one of `HORIZON_OPTIONS`; `legal_entity_id` omitted is every legal entity the
    caller sees."""

    model_config = ConfigDict(extra="forbid")

    weeks: int = DEFAULT_HORIZON_WEEKS
    legal_entity_id: UUID | None = None


class CashAccountCard(BaseModel):
    id: UUID
    legal_entity_id: UUID
    legal_entity_name: str
    name: str
    bank: str
    currency: str
    balance: Decimal | None = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    balance_as_of: dt.date | None
    stale_days: int | None
    is_stale: bool


class TotalBalance(BaseModel):
    """The EUR accounts added up, true as of the oldest of their balances. An account in
    another currency is not converted: it is named in `excluded_currencies`."""

    amount: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    currency: Literal["EUR"] = "EUR"
    as_of: dt.date | None
    excluded_currencies: list[str]


class LateFigure(BaseModel):
    """What is past its due date: how many items, how much, and how old the oldest is."""

    count: int
    total: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    oldest_days: int | None


class CashWeek(BaseModel):
    """One Monday-to-Sunday week. The current week is whole: today and the days after it count
    in it, the days of it before today are late. `balance_end` is one figure with the forecasts
    in and the late items out."""

    start: dt.date
    end: dt.date
    is_current: bool
    in_expected: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    in_forecast: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    out_expected: Decimal | None = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    out_forecast: Decimal | None = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    net: Decimal | None = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    balance_end: Decimal | None = Field(
        max_digits=MONEY_DIGITS,
        decimal_places=2,
        description="Null when `show_balance` is false; forecasts in, late items out",
    )
    below_zero: bool = Field(description="`balance_end` is below zero")
    item_count: int
    has_forecast: bool
    has_estimated_date: bool


class SoloTaxRow(BaseModel):
    """One of a solo space's own taxes. Listed apart and never taken out of any balance."""

    due_item_id: UUID
    tax: str | None
    description: str | None
    due_date: dt.date | None
    state: ItemState
    amount: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)


class CashAlert(BaseModel):
    """Something that needs a person. `days` is how long the statement has been missing
    (`missing_statement`) or the balance has gone without news (`stale_balance`)."""

    kind: AlertKind
    week_start: dt.date | None = None
    week_end: dt.date | None = None
    account_id: UUID | None = None
    account_name: str | None = None
    period_from: dt.date | None = None
    period_to: dt.date | None = None
    days: int | None = None


class CashSchedule(BaseModel):
    calculated_at: dt.datetime
    today: dt.date
    legal_entity_id: UUID | None
    horizon_weeks: int
    horizon_options: list[int]
    profile: ScheduleProfile | None
    cash_enabled: bool
    currency: Literal["EUR"] = "EUR"
    show_outgoing: bool = Field(
        description="False for a solo space: no outgoing column and no payables"
    )
    show_balance: bool = Field(
        description=(
            "True when the weeks carry a projected balance: the caller may read the balances, "
            "the module `cash` is on, the profile is not solo and an EUR balance exists"
        )
    )
    accounts: list[CashAccountCard] | None = Field(
        description=(
            "Null when the caller may not read the bank accounts or the module `cash` is off; "
            "an empty list when allowed and there is no account"
        )
    )
    total_balance: TotalBalance | None = Field(
        description="Null with `accounts`, or when no EUR account has a balance yet"
    )
    late_receivable: LateFigure
    late_payable: LateFigure | None = Field(description="Null for a solo space")
    weeks: list[CashWeek] = Field(description="Always `horizon_weeks` rows, the current week first")
    taxes: list[SoloTaxRow] | None = Field(
        description="A solo space's own taxes, for a caller who may read them; null otherwise"
    )
    alerts: list[CashAlert] = Field(description="Worst first; empty when nothing needs a person")
    has_open_items: bool = Field(
        description="False when nothing is open in any currency: nothing to collect or pay"
    )


class PayableRow(BaseModel):
    """One overdue payable. `counterparty` is the supplier, or the tax; none for a staff
    member, whose name is not on this card."""

    due_item_id: UUID
    counterparty: str | None
    description: str | None
    due_date: dt.date
    days_of_delay: int
    amount: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)


class PayablesDashboard(BaseModel):
    """The mirror of `ReceivablesDashboard` for money going out: the open `expected` items (a
    forecast is not owed yet), in the same six bands. The bands add up to `total`."""

    calculated_at: dt.datetime
    today: dt.date
    legal_entity_id: UUID | None = None
    total: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    bands: list[BandDueDate]
    overdue: list[PayableRow]
    total_overdue: int


class OwedFigure(BaseModel):
    total: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    late_total: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    late_count: int


class Next7(BaseModel):
    """The open items (expected and forecast) due from today through the sixth day after."""

    in_total: Decimal = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    out_total: Decimal | None = Field(max_digits=MONEY_DIGITS, decimal_places=2)


class CashSummary(BaseModel):
    """The phone Home's money figures."""

    calculated_at: dt.datetime
    today: dt.date
    cash_enabled: bool
    show_outgoing: bool
    balance_today: Decimal | None = Field(max_digits=MONEY_DIGITS, decimal_places=2)
    balance_as_of: dt.date | None
    receivable: OwedFigure
    payable: OwedFigure | None
    next_7_days: Next7
    alert: CashAlert | None
