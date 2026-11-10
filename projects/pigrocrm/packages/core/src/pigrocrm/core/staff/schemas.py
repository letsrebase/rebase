"""Every Pydantic shape of staff and directors (spec 2026-11-09, staff, payroll and
expenses, § 1).

A staff member is created as an `EmployeeCreate` or a `DirectorCreate`, told apart by `kind`
(`StaffMemberCreate`); an update names only what changes and cannot change the kind or the
legal entity. Percentages are percent (`part_time_percent` 80 is 80 %). A rate or a share
left out is «not stated», never a default: the payroll consultant verifies them.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pigrocrm.core.staff.models import (
    AGREEMENT_MAX_LENGTH,
    LATEST_PAY_DAY,
    LEVEL_MAX_LENGTH,
    NAME_MAX_LENGTH,
)
from pigrocrm.core.validation import SafeStr

Kind = Literal["employee", "director"]
FeePayment = Literal["monthly", "other"]
MonthlyPayments = Literal[13, 14]
PaySource = Literal["staff_member", "legal_entity"]

MONEY_MAX_DIGITS = 12
MONEY_SCALE = 2
PERCENT_MAX_DIGITS = 5
PERCENT_SCALE = 2
HOURS_MAX_PER_MONTH = 744

_Money = Annotated[Decimal, Field(gt=0, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE)]
_Percent = Annotated[
    Decimal, Field(ge=0, le=100, max_digits=PERCENT_MAX_DIGITS, decimal_places=PERCENT_SCALE)
]
_PartTime = Annotated[
    Decimal, Field(gt=0, le=100, max_digits=PERCENT_MAX_DIGITS, decimal_places=PERCENT_SCALE)
]
_Hours = Annotated[
    Decimal, Field(gt=0, le=HOURS_MAX_PER_MONTH, max_digits=6, decimal_places=MONEY_SCALE)
]
_PayDay = Annotated[int, Field(ge=1, le=LATEST_PAY_DAY)]
_MonthOffset = Annotated[int, Field(ge=0, le=1)]


def _pay_day_pair(day: int | None, offset: int | None) -> None:
    if (day is None) != (offset is None):
        raise ValueError("il giorno di paga e il mese di paga vanno indicati insieme")


class PayDayWrite(BaseModel):
    """The pay day of a legal entity: the day of the month (1 to 28) and whether it falls in
    the month worked (0) or the month after (1)."""

    model_config = ConfigDict(extra="forbid")

    pay_day: _PayDay
    pay_month_offset: _MonthOffset


class PayDayRead(BaseModel):
    """A legal entity's pay day; both fields are `None` until one is set."""

    legal_entity_id: UUID
    pay_day: int | None
    pay_month_offset: int | None


class EffectivePayDay(BaseModel):
    """The pay day that applies to a staff member and where it comes from: the member's own
    override, or the legal entity's."""

    pay_day: int
    pay_month_offset: int
    source: PaySource


class _StaffCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_entity_id: UUID
    name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    start_date: dt.date
    end_date: dt.date | None = None
    hours_per_month: _Hours | None = None
    user_id: UUID | None = None
    pay_day: _PayDay | None = None
    pay_month_offset: _MonthOffset | None = None

    @model_validator(mode="after")
    def _common(self) -> "_StaffCreate":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("la fine non puo' precedere l'inizio")
        _pay_day_pair(self.pay_day, self.pay_month_offset)
        return self


class EmployeeCreate(_StaffCreate):
    """An employee: the contract inputs. `part_time_percent` is the share of a full-time
    contract; the two employer rates are typed per person."""

    kind: Literal["employee"] = "employee"
    collective_agreement: SafeStr | None = Field(default=None, max_length=AGREEMENT_MAX_LENGTH)
    contract_level: SafeStr | None = Field(default=None, max_length=LEVEL_MAX_LENGTH)
    gross_annual_pay: _Money
    monthly_payments: MonthlyPayments
    part_time_percent: _PartTime = Decimal("100")
    employer_inps_rate: _Percent | None = None
    employer_inail_rate: _Percent | None = None


class DirectorCreate(_StaffCreate):
    """A director: the annual fee, how it is paid, the TFM accrued a year (set exactly when
    `accrues_tfm`) and the share of the INPS «gestione separata» the company bears."""

    kind: Literal["director"] = "director"
    annual_fee: _Money
    fee_payment: FeePayment = "monthly"
    accrues_tfm: bool = False
    tfm_annual_amount: _Money | None = None
    inps_company_share: _Percent | None = None

    @model_validator(mode="after")
    def _tfm(self) -> "DirectorCreate":
        if self.accrues_tfm != (self.tfm_annual_amount is not None):
            raise ValueError("l'importo annuo del TFM si indica se e solo se matura un TFM")
        return self


StaffMemberCreate = Annotated[EmployeeCreate | DirectorCreate, Field(discriminator="kind")]


class StaffMemberUpdate(BaseModel):
    """What changes. A field of the other kind is refused by the service, and `null` empties
    only the fields that may be empty (the optional ones)."""

    model_config = ConfigDict(extra="forbid")

    name: SafeStr | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    hours_per_month: _Hours | None = None
    user_id: UUID | None = None
    pay_day: _PayDay | None = None
    pay_month_offset: _MonthOffset | None = None
    collective_agreement: SafeStr | None = Field(default=None, max_length=AGREEMENT_MAX_LENGTH)
    contract_level: SafeStr | None = Field(default=None, max_length=LEVEL_MAX_LENGTH)
    gross_annual_pay: _Money | None = None
    monthly_payments: MonthlyPayments | None = None
    part_time_percent: _PartTime | None = None
    employer_inps_rate: _Percent | None = None
    employer_inail_rate: _Percent | None = None
    annual_fee: _Money | None = None
    fee_payment: FeePayment | None = None
    accrues_tfm: bool | None = None
    tfm_annual_amount: _Money | None = None
    inps_company_share: _Percent | None = None


class StaffMemberRead(BaseModel):
    """Every field of both kinds; the ones of the other kind are `None`. `effective_pay_day`
    is `None` until the member or the legal entity has a pay day."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    legal_entity_id: UUID
    kind: Kind
    name: str
    user_id: UUID | None
    start_date: dt.date
    end_date: dt.date | None
    hours_per_month: Decimal | None
    pay_day: int | None
    pay_month_offset: int | None
    effective_pay_day: EffectivePayDay | None = None
    collective_agreement: str | None
    contract_level: str | None
    gross_annual_pay: Decimal | None
    monthly_payments: int | None
    part_time_percent: Decimal | None
    employer_inps_rate: Decimal | None
    employer_inail_rate: Decimal | None
    annual_fee: Decimal | None
    fee_payment: str | None
    accrues_tfm: bool
    tfm_annual_amount: Decimal | None
    inps_company_share: Decimal | None
    created_at: dt.datetime
    updated_at: dt.datetime


class StaffListQuery(BaseModel):
    """`include_ended` adds the staff whose end date is past; omitted, only who is in
    service today or will be."""

    legal_entity_id: UUID | None = None
    kind: Kind | None = None
    include_ended: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class StaffPage(BaseModel):
    items: list[StaffMemberRead]
    next_cursor: UUID | None
