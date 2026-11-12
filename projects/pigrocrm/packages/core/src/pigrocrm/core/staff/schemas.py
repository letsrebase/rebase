"""Every Pydantic shape of staff and directors (spec 2026-11-09, staff, payroll and
expenses, § 1).

A staff member is created as an `EmployeeCreate` or a `DirectorCreate`, told apart by `kind`
(`StaffMemberCreate`); an update names only what changes and cannot change the kind or the
legal entity. Percentages are percent (`part_time_percent` 80 is 80 %). A rate, a share
or a month left out is «not stated», never a default: the payroll consultant verifies them.
"""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_serializer, model_validator

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
# Literal mirrors `models.CONTRACT_TYPES`.
ContractType = Literal["permanent", "fixed_term", "seasonal", "apprentice", "training"]
PaySource = Literal["staff_member", "legal_entity"]

MONEY_MAX_DIGITS = 12
MONEY_SCALE = 2
PERCENT_MAX_DIGITS = 5
PERCENT_SCALE = 2
HOURS_MAX_PER_MONTH = 744
WORKED_YEAR_MIN = 1900
WORKED_YEAR_MAX = 2100


def _days_in_year_of(start: dt.date, end: dt.date | None, year: int) -> int:
    """The days of the contract that fall in `year` (an open contract runs to the year's end)."""
    first = max(start, dt.date(year, 1, 1))
    last = dt.date(year, 12, 31) if end is None else min(end, dt.date(year, 12, 31))
    return max((last - first).days + 1, 0)


class SeasonalContract(BaseModel):
    """One contract of a seasonal worker with the company (art. 11 c. 4-octies D.Lgs. 446/1997,
    REB-1090): its dates, and the days of effective work in each tax year (not the days of the
    contract). `end_date` is empty for a contract still running; a year left out is «not
    stated», never zero."""

    model_config = ConfigDict(extra="forbid")

    start_date: dt.date
    end_date: dt.date | None = None
    worked_days: dict[int, Annotated[int, Field(ge=0, le=366)]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("la fine del contratto non puo' precedere l'inizio")
        for year, days in self.worked_days.items():
            if not WORKED_YEAR_MIN <= year <= WORKED_YEAR_MAX:
                raise ValueError(f"l'anno {year} dei giorni lavorati non e' valido")
            available = _days_in_year_of(self.start_date, self.end_date, year)
            if days > available:
                raise ValueError(
                    f"{days} giorni lavorati nel {year} sono piu' dei {available} giorni "
                    "del contratto in quell'anno"
                )
        return self


def _check_no_overlap(contracts: list[SeasonalContract]) -> list[SeasonalContract]:
    """Contracts of one worker with the company follow each other: only the last may be open
    and none starts before the previous one ended."""
    ordered = sorted(contracts, key=lambda contract: contract.start_date)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if earlier.end_date is None or later.start_date <= earlier.end_date:
            raise ValueError("i contratti stagionali non si possono sovrapporre")
    return ordered


SeasonalContracts = Annotated[
    list[SeasonalContract], Field(max_length=40), AfterValidator(_check_no_overlap)
]


def _contracts_as_stored(value: list[SeasonalContract] | None) -> list[dict[str, Any]] | None:
    """The JSONB form of the history: dates as ISO text and the years as text keys."""
    return None if value is None else [contract.model_dump(mode="json") for contract in value]


_Money = Annotated[Decimal, Field(gt=0, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE)]
_Percent = Annotated[
    Decimal, Field(ge=0, le=100, max_digits=PERCENT_MAX_DIGITS, decimal_places=PERCENT_SCALE)
]
# The INAIL rate in percent: INAIL states it per mille with two decimals, so three decimals in
# percent hold it exactly (REB-1097, «Autoliquidazione 2025/2026»).
_InailRate = Annotated[Decimal, Field(ge=0, le=100, max_digits=6, decimal_places=3)]
_PartTime = Annotated[
    Decimal, Field(gt=0, le=100, max_digits=PERCENT_MAX_DIGITS, decimal_places=PERCENT_SCALE)
]
_Hours = Annotated[
    Decimal, Field(gt=0, le=HOURS_MAX_PER_MONTH, max_digits=6, decimal_places=MONEY_SCALE)
]
_PayDay = Annotated[int, Field(ge=1, le=LATEST_PAY_DAY)]
_Month = Annotated[int, Field(ge=1, le=12)]
_MonthOffset = Annotated[int, Field(ge=0, le=1)]


def _pay_day_pair(day: int | None, offset: int | None) -> None:
    if (day is None) != (offset is None):
        raise ValueError("il giorno di paga e il mese di paga vanno indicati insieme")


def _one_workplace(site_id: UUID | None, at_registered_office: bool | None) -> None:
    if site_id is not None and at_registered_office:
        raise ValueError("la persona lavora in una sede o nella sede legale, non in entrambe")


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
    site_id: UUID | None = Field(
        default=None,
        description=(
            "The site of the legal entity the person works at, for the IRAP split between "
            "regions; omitted with `at_registered_office` false means not stated"
        ),
    )
    at_registered_office: bool = Field(
        default=False, description="The person works at the registered office, not at a site"
    )

    @model_validator(mode="after")
    def _common(self) -> "_StaffCreate":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("la fine non puo' precedere l'inizio")
        _pay_day_pair(self.pay_day, self.pay_month_offset)
        _one_workplace(self.site_id, self.at_registered_office)
        return self


class EmployeeCreate(_StaffCreate):
    """An employee: the contract inputs. `part_time_percent` is the share of a full-time
    contract; the two employer rates are typed per person."""

    kind: Literal["employee"] = "employee"
    collective_agreement: SafeStr | None = Field(default=None, max_length=AGREEMENT_MAX_LENGTH)
    contract_level: SafeStr | None = Field(default=None, max_length=LEVEL_MAX_LENGTH)
    contract_type: ContractType | None = None
    disabled_worker: bool | None = None
    seasonal_contracts: SeasonalContracts | None = None
    gross_annual_pay: _Money
    monthly_payments: MonthlyPayments
    part_time_percent: _PartTime = Decimal("100")
    employer_inps_rate: _Percent | None = None
    employer_inail_rate: _InailRate | None = None
    employee_inps_rate: _Percent | None = None
    thirteenth_month: _Month | None = None
    fourteenth_month: _Month | None = None

    @field_serializer("seasonal_contracts")
    def _seasonal_contracts_as_stored(
        self, value: list[SeasonalContract] | None
    ) -> list[dict[str, Any]] | None:
        return _contracts_as_stored(value)

    @model_validator(mode="after")
    def _payments(self) -> "EmployeeCreate":
        if self.fourteenth_month is not None and self.monthly_payments != 14:
            raise ValueError("la quattordicesima c'e' solo con quattordici mensilita'")
        return self


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
    site_id: UUID | None = Field(
        default=None,
        description=(
            "The site the person works at; naming one takes the person off the registered "
            "office, and `null` takes them off any site"
        ),
    )
    at_registered_office: bool | None = Field(
        default=None,
        description=(
            "The person works at the registered office; `true` takes them off any site, `false` "
            "takes them off the registered office"
        ),
    )
    collective_agreement: SafeStr | None = Field(default=None, max_length=AGREEMENT_MAX_LENGTH)
    contract_level: SafeStr | None = Field(default=None, max_length=LEVEL_MAX_LENGTH)
    contract_type: ContractType | None = None
    disabled_worker: bool | None = None
    seasonal_contracts: SeasonalContracts | None = None
    gross_annual_pay: _Money | None = None
    monthly_payments: MonthlyPayments | None = None
    part_time_percent: _PartTime | None = None
    employer_inps_rate: _Percent | None = None
    employer_inail_rate: _InailRate | None = None
    employee_inps_rate: _Percent | None = None
    thirteenth_month: _Month | None = None
    fourteenth_month: _Month | None = None
    annual_fee: _Money | None = None
    fee_payment: FeePayment | None = None
    accrues_tfm: bool | None = None
    tfm_annual_amount: _Money | None = None
    inps_company_share: _Percent | None = None

    @field_serializer("seasonal_contracts")
    def _seasonal_contracts_as_stored(
        self, value: list[SeasonalContract] | None
    ) -> list[dict[str, Any]] | None:
        return _contracts_as_stored(value)

    @model_validator(mode="after")
    def _workplace(self) -> "StaffMemberUpdate":
        _one_workplace(self.site_id, self.at_registered_office)
        return self


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
    site_id: UUID | None
    at_registered_office: bool
    effective_pay_day: EffectivePayDay | None = None
    collective_agreement: str | None
    contract_level: str | None
    contract_type: ContractType | None
    disabled_worker: bool | None
    seasonal_contracts: list[SeasonalContract] | None
    gross_annual_pay: Decimal | None
    monthly_payments: int | None
    part_time_percent: Decimal | None
    employer_inps_rate: Decimal | None
    employer_inail_rate: Decimal | None
    employee_inps_rate: Decimal | None
    thirteenth_month: int | None
    fourteenth_month: int | None
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


class _PayslipAmounts(BaseModel):
    """What a payslip prints, in euro: the gross, the net pay and the income tax withheld."""

    model_config = ConfigDict(extra="forbid")

    gross: _Money
    net: Annotated[Decimal, Field(ge=0, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE)]
    withholding: Annotated[
        Decimal, Field(ge=0, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE)
    ]

    @model_validator(mode="after")
    def _parts(self) -> Self:
        if self.net + self.withholding > self.gross:
            raise ValueError("il netto e la ritenuta non possono superare il lordo")
        return self


class PayslipFigures(_PayslipAmounts):
    """The figures of one accepted payslip. `period` is any day of the month it pays; gross,
    net and the income-tax withheld are what the payslip prints, in euro."""

    period: dt.date


class PayslipCorrection(_PayslipAmounts):
    """The figures a recorded payslip is corrected to, all three at once. The month is not
    among them: a payslip of another month is another payslip."""


class ReadPayslipFields(BaseModel):
    """What the space's model read on a payslip (`ai/reader.py`), every field optional because a
    document may not state it: the name it carries, a day of the month it pays and the three
    figures. An amount is a decimal or absent, never a sentence. What is missing makes the
    reading no proposal: a person has nothing to decide on a figure that was not read."""

    model_config = ConfigDict(extra="forbid")

    employee_name: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    period: dt.date | None = None
    gross: Decimal | None = Field(
        default=None, gt=0, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE
    )
    net: Decimal | None = Field(
        default=None, ge=0, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE
    )
    withholding: Decimal | None = Field(
        default=None, ge=0, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE
    )


class PayslipProposalFields(PayslipFigures):
    """`proposed_fields` of a `payslip` proposal (REB-1045): the figures of `PayslipFigures`, for
    whom they are (`staff_member_id`) and, for the reviewer, the name the document itself carries
    (`employee_name`). Whoever reads a payslip (the space's model, or the user's agent) has
    matched the person already; a person accepts the proposal as it reads or not at all."""

    staff_member_id: UUID
    employee_name: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)


class PayslipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    staff_member_id: UUID
    legal_entity_id: UUID
    period: dt.date
    gross: Decimal
    net: Decimal
    withholding: Decimal
    # The inbox item the payslip was accepted from: its file is the payslip's PDF. `None` for
    # figures a person typed.
    inbox_item_id: UUID | None = None
    created_at: dt.datetime


class PayslipProposalRead(BaseModel):
    """A payslip the inbox read and nobody accepted yet: what makes a person's month «Ricevuto».
    The decision is the proposal's own (`POST /api/proposals/{proposal_id}/accept` and
    `/reject`), the same call wherever it is drawn."""

    proposal_id: UUID
    inbox_item_id: UUID | None
    gross: Decimal
    net: Decimal
    withholding: Decimal
    confidence: Decimal
    confidence_reason: str | None
    created_at: dt.datetime


class PayrollClosureRead(BaseModel):
    """A payroll month a person closed: when and by whom (the name as it is now)."""

    legal_entity_id: UUID
    month: dt.date
    closed_at: dt.datetime
    closed_by: str | None


class PayrollMonthClose(BaseModel):
    """What closes: any day of the month worked."""

    model_config = ConfigDict(extra="forbid")

    month: dt.date


CashKindName = Literal["net_pay", "extra_pay", "withholding", "inps_contributions", "inail_premium"]
EstimateSource = Literal["estimate", "payslip"]


class HourlyCostProposalRead(BaseModel):
    """What a staff member's hour costs, from what they cost (design § 5), and the hourly cost
    the linked user has today. Read-only: nothing here has been written."""

    staff_member_id: UUID
    user_id: UUID
    # The month the cost accrual was taken from.
    month: dt.date
    monthly_cost: Decimal
    hours_per_month: Decimal
    hourly_cost: Decimal
    current_hourly_cost: Decimal | None


class HourlyCostAdopt(BaseModel):
    """The figure the person saw and confirms: adopted only if it is still what the cost
    gives, so a change of pay between the screen and the click is never adopted unseen."""

    model_config = ConfigDict(extra="forbid")

    hourly_cost: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class PayrollCostRead(BaseModel):
    """A month's cost: the gross pay and the employer's contributions that could be estimated
    (`None` where the rate is not stated), and their total."""

    gross: Decimal
    employer_inps: Decimal | None
    employer_inail: Decimal | None
    total: Decimal


class PayrollCashItemRead(BaseModel):
    kind: CashKindName
    amount: Decimal
    due_date: dt.date
    payroll_month: dt.date


class MemberMonthRead(BaseModel):
    """One staff member's month: cost and cash apart, each cash item with its date, and what
    could not be estimated and why (`not_estimated`, one word each)."""

    staff_member_id: UUID
    name: str
    kind: Kind
    source: EstimateSource
    cost: PayrollCostRead | None
    cash: list[PayrollCashItemRead]
    not_estimated: list[str]
    # The payslip that fixed the month, with its id (what a correction names), or none.
    payslip: PayslipRead | None = None
    # A payslip read from the inbox that nobody accepted: the month reads «Ricevuto». Never in a
    # month a person closed.
    received: PayslipProposalRead | None = None


class PayrollMonthRead(BaseModel):
    legal_entity_id: UUID
    month: dt.date
    members: list[MemberMonthRead]
    cost_total: Decimal
    cash_total: Decimal
    # Set once a person closed the month: its figures are the ones that were closed.
    closed: PayrollClosureRead | None = None
