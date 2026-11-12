"""Staff and directors of a legal entity, with their pay inputs (spec 2026-11-09, staff,
payroll and expenses, § 1).

A `StaffMember` is an **employee** or a **director** of one legal entity. The table holds
the inputs a later payroll estimate reads (the gross annual pay, the number of monthly
payments, the part-time share, the employer's INPS and INAIL rates; a director's annual
fee, how it is paid, the TFM and the INPS split) and nothing computed: PigroCRM plans and
the payroll consultant computes (decision 1). Every rate typed here is a taxpayer fact the
consultant verifies, so a rate that was not typed is `NULL` («not stated») and never a
default.

The kind decides which columns are used, and the table says so (`ck_staff_members_*`):
an employee has no fee columns and a director has no contract columns, so a row cannot
claim both.

`PayrollSettings` holds the pay day of a legal entity: the day of the month net pay leaves
and whether it is the month after the one worked (the 27th, or the 10th of the next month).
A staff member carries an override of the same two values.

Pay is the most personal data the product holds (§ 4): both tables carry the plain
`legal_entity_scope` policy and the finer rule, administrators only, is a check in
`StaffService`.
"""

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin
from pigrocrm.core.fiscal.pack import PACK_IT_COMPANY, PACK_IT_FLAT_RATE, PACK_IT_STANDARD

KINDS: tuple[str, ...] = ("employee", "director")
# Employees are paid in 13 or 14 monthly payments (design § 1).
MONTHLY_PAYMENTS: tuple[int, ...] = (13, 14)
# What an employee's contract is, for the deductions of IRAP (art. 11 c. 4-octies and
# 4-bis.1): `NULL` is «not stated», so nothing is deducted for the person and the estimate
# names them. A director is not an employee and has none.
CONTRACT_TYPES: tuple[str, ...] = ("permanent", "fixed_term", "seasonal", "apprentice", "training")
# A director's fee is paid monthly or otherwise; the otherwise has no further structure yet.
FEE_PAYMENTS: tuple[str, ...] = ("monthly", "other")

# The fiscal packs whose legal entities may have staff: a flat-rate taxpayer withholds on
# employees only (L. 190/2014, art. 1 c. 69), never `non-it`, which has no Italian payroll
# rules. Directors exist only on a company, the `it-company` pack (REB-1005).
EMPLOYEE_PACKS: tuple[str, ...] = (PACK_IT_FLAT_RATE, PACK_IT_STANDARD, PACK_IT_COMPANY)
DIRECTOR_PACKS: tuple[str, ...] = (PACK_IT_COMPANY,)

# The latest day of a month a pay day may name, so it exists in February too.
LATEST_PAY_DAY = 28

NAME_MAX_LENGTH = 120
AGREEMENT_MAX_LENGTH = 120
LEVEL_MAX_LENGTH = 40


class StaffMember(Base, PrimaryKeyMixin, TimestampMixin):
    """One employee or director. `user_id` links, optionally, the PigroCRM user the person
    is, at most one staff member per user, so labour is never counted twice (§ 2). The
    `pay_day` pair is the override of the legal entity's own."""

    __tablename__ = "staff_members"
    __table_args__ = (
        CheckConstraint(f"kind IN {KINDS!r}", name="ck_staff_members_kind"),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_staff_members_period"
        ),
        CheckConstraint(
            "hours_per_month IS NULL OR (hours_per_month > 0 AND hours_per_month <= 744)",
            name="ck_staff_members_hours",
        ),
        CheckConstraint(
            "(pay_day IS NULL) = (pay_month_offset IS NULL)", name="ck_staff_members_pay_day_pair"
        ),
        CheckConstraint(
            f"pay_day IS NULL OR pay_day BETWEEN 1 AND {LATEST_PAY_DAY}",
            name="ck_staff_members_pay_day_range",
        ),
        CheckConstraint(
            "pay_month_offset IS NULL OR pay_month_offset IN (0, 1)",
            name="ck_staff_members_pay_month_offset",
        ),
        # An employee: the contract columns, and nothing of a director's.
        CheckConstraint(
            "kind <> 'employee' OR (gross_annual_pay IS NOT NULL "
            "AND monthly_payments IS NOT NULL AND part_time_percent IS NOT NULL "
            "AND annual_fee IS NULL AND fee_payment IS NULL AND accrues_tfm = false "
            "AND tfm_annual_amount IS NULL AND inps_company_share IS NULL)",
            name="ck_staff_members_employee_columns",
        ),
        # A director: the fee columns, and nothing of a contract.
        CheckConstraint(
            "kind <> 'director' OR (annual_fee IS NOT NULL AND fee_payment IS NOT NULL "
            "AND collective_agreement IS NULL AND contract_level IS NULL "
            "AND gross_annual_pay IS NULL AND monthly_payments IS NULL "
            "AND part_time_percent IS NULL AND employer_inps_rate IS NULL "
            "AND employer_inail_rate IS NULL AND employee_inps_rate IS NULL "
            "AND contract_type IS NULL AND disabled_worker IS NULL "
            "AND seasonal_contracts IS NULL "
            "AND thirteenth_month IS NULL AND fourteenth_month IS NULL)",
            name="ck_staff_members_director_columns",
        ),
        CheckConstraint(
            "gross_annual_pay IS NULL OR gross_annual_pay > 0", name="ck_staff_members_gross_pay"
        ),
        CheckConstraint("annual_fee IS NULL OR annual_fee > 0", name="ck_staff_members_fee"),
        CheckConstraint(
            f"contract_type IS NULL OR contract_type IN {CONTRACT_TYPES!r}",
            name="ck_staff_members_contract_type",
        ),
        CheckConstraint(
            f"monthly_payments IS NULL OR monthly_payments IN {MONTHLY_PAYMENTS!r}",
            name="ck_staff_members_monthly_payments",
        ),
        CheckConstraint(
            "part_time_percent IS NULL OR (part_time_percent > 0 AND part_time_percent <= 100)",
            name="ck_staff_members_part_time",
        ),
        CheckConstraint(
            "employer_inps_rate IS NULL OR (employer_inps_rate >= 0 AND employer_inps_rate <= 100)",
            name="ck_staff_members_inps_rate",
        ),
        CheckConstraint(
            "employer_inail_rate IS NULL "
            "OR (employer_inail_rate >= 0 AND employer_inail_rate <= 100)",
            name="ck_staff_members_inail_rate",
        ),
        CheckConstraint(
            "employee_inps_rate IS NULL OR (employee_inps_rate >= 0 AND employee_inps_rate <= 100)",
            name="ck_staff_members_employee_inps_rate",
        ),
        CheckConstraint(
            "thirteenth_month IS NULL OR thirteenth_month BETWEEN 1 AND 12",
            name="ck_staff_members_thirteenth_month",
        ),
        CheckConstraint(
            "fourteenth_month IS NULL OR fourteenth_month BETWEEN 1 AND 12",
            name="ck_staff_members_fourteenth_month",
        ),
        # The 14th payment exists only on a 14-payment contract, and the 13th on a 13- or
        # 14-payment one; a month typed for a payment the contract does not have would be a
        # cash item for money never owed.
        CheckConstraint(
            "fourteenth_month IS NULL OR monthly_payments = 14", name="ck_staff_members_fourteenth"
        ),
        CheckConstraint(
            f"fee_payment IS NULL OR fee_payment IN {FEE_PAYMENTS!r}",
            name="ck_staff_members_fee_payment",
        ),
        # The TFM amount is the accrual a year: present exactly when the director accrues one.
        CheckConstraint(
            "(accrues_tfm AND tfm_annual_amount IS NOT NULL AND tfm_annual_amount > 0) "
            "OR (NOT accrues_tfm AND tfm_annual_amount IS NULL)",
            name="ck_staff_members_tfm",
        ),
        CheckConstraint(
            "inps_company_share IS NULL OR (inps_company_share >= 0 AND inps_company_share <= 100)",
            name="ck_staff_members_inps_share",
        ),
        # Where the person works (REB-1092): a site of the legal entity, or its registered office,
        # never both; neither is «not stated». The pair points at the site and its legal entity, so
        # a person cannot be assigned to another company's site.
        CheckConstraint(
            "NOT (at_registered_office AND site_id IS NOT NULL)",
            name="ck_staff_members_one_workplace",
        ),
        ForeignKeyConstraint(
            ["site_id", "legal_entity_id"],
            ["legal_entity_sites.id", "legal_entity_sites.legal_entity_id"],
            name="fk_staff_members_site_same_legal_entity",
        ),
        Index("ix_staff_members_legal_entity_id", "legal_entity_id"),
        # One staff member per user: a labour cost counted twice is the failure this stops.
        Index(
            "uq_staff_members_user_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    start_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    end_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # The workplace, for the IRAP split between regions (REB-1092): one of the legal entity's sites,
    # or its registered office; `NULL` and `false` is «not stated».
    site_id: Mapped[UUID | None] = mapped_column(default=None)
    at_registered_office: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Working hours a month, the divisor of an hourly cost (design § 5).
    hours_per_month: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), default=None)
    # The override of the legal entity's pay day: both or neither.
    pay_day: Mapped[int | None] = mapped_column(Integer, default=None)
    pay_month_offset: Mapped[int | None] = mapped_column(Integer, default=None)

    # Employee: the collective agreement and level (as the contract names them).
    collective_agreement: Mapped[str | None] = mapped_column(
        String(AGREEMENT_MAX_LENGTH), default=None
    )
    contract_level: Mapped[str | None] = mapped_column(String(LEVEL_MAX_LENGTH), default=None)
    # `permanent`, `fixed_term`, `seasonal`, `apprentice` or `training` (a contratto di
    # formazione lavoro); `NULL` is «not stated» (REB-1081, REB-1091).
    contract_type: Mapped[str | None] = mapped_column(String(10), default=None)
    # Whether the person is disabled in the sense of art. 1 L. 68/1999 or art. 5 L. 482/1968
    # (art. 11 c. 4-bis.1 D.Lgs. 446/1997); `NULL` is «not stated» (REB-1091).
    disabled_worker: Mapped[bool | None] = mapped_column(Boolean, default=None)
    # The contract history of a seasonal worker with the company, for the 70% deduction of
    # art. 11 c. 4-octies (REB-1090): a list of `{start_date, end_date, worked_days}`, the days
    # of effective work being by tax year. `NULL` is «not stated»; validated by
    # `schemas.SeasonalContract`.
    seasonal_contracts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, default=None)
    gross_annual_pay: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    monthly_payments: Mapped[int | None] = mapped_column(Integer, default=None)
    # The share of a full-time contract, in percent.
    part_time_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    # The employer's rates in percent, typed per staff member: the sector and the risk class
    # are taxpayer facts the payroll consultant verifies. `NULL` is «not stated».
    employer_inps_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    # INAIL's «tasso applicato» is stated per mille with two decimals (9.75 per mille), which is
    # a percent with three (0.975): the column keeps the third decimal (REB-1097).
    employer_inail_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), default=None)
    # The employee's own INPS share, in percent: the employer pays it with its own to the INPS
    # by F24, so the F24 line needs both. Typed, never a default: no primary source states
    # one rate for every employer (A5, REB-975).
    employee_inps_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    # The calendar months (1 to 12) the 13th and 14th payments are paid in. They come from the
    # collective agreement, not from the law, so they are typed: a payment with no month is
    # not scheduled and the estimate says so (A5, REB-975).
    thirteenth_month: Mapped[int | None] = mapped_column(Integer, default=None)
    fourteenth_month: Mapped[int | None] = mapped_column(Integer, default=None)

    # Director: the annual fee, how it is paid, the TFM accrued a year and the share of the
    # INPS «gestione separata» contribution the company bears, in percent (to verify).
    annual_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    fee_payment: Mapped[str | None] = mapped_column(String(10), default=None)
    accrues_tfm: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    tfm_annual_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    inps_company_share: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)


class StaffPayslip(Base, PrimaryKeyMixin, TimestampMixin):
    """The figures of one accepted payslip of one staff member, and nothing else of it.

    The estimate (`staff/estimate.py`) reads only two ratios from the last of them, by
    `period`: net pay to gross and the income-tax withholding to gross. A member with no row
    here has neither a net pay nor a withholding estimate (spec § 2). Whoever accepts a payslip
    (a person typing the figures, or later the inbox's proposal) writes the row; a row is
    accepted by existing, there is no draft state. `net` and `withholding` are in euro for the
    month `period` (its first day); `gross` is positive and neither may exceed it.
    """

    __tablename__ = "staff_payslips"
    __table_args__ = (
        UniqueConstraint("staff_member_id", "period", name="uq_staff_payslips_member_period"),
        CheckConstraint("gross > 0", name="ck_staff_payslips_gross"),
        CheckConstraint("net >= 0 AND net <= gross", name="ck_staff_payslips_net"),
        CheckConstraint(
            "withholding >= 0 AND withholding <= gross", name="ck_staff_payslips_withholding"
        ),
        CheckConstraint("net + withholding <= gross", name="ck_staff_payslips_parts"),
        CheckConstraint("extract(day FROM period) = 1", name="ck_staff_payslips_period"),
        Index("ix_staff_payslips_legal_entity_id", "legal_entity_id"),
    )

    staff_member_id: Mapped[UUID] = mapped_column(ForeignKey("staff_members.id"), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    period: Mapped[dt.date] = mapped_column(Date, nullable=False)
    gross: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    net: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    withholding: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # The inbox item the payslip was read from, when it was accepted from a proposal (REB-1045):
    # its file is the payslip's PDF. `NULL` for figures a person typed.
    inbox_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inbox_items.id"), default=None, index=True
    )


class PayrollSettings(Base, PrimaryKeyMixin, TimestampMixin):
    """The pay day of one legal entity: `pay_day` is the day of the month and
    `pay_month_offset` is 0 for the month worked and 1 for the month after."""

    __tablename__ = "payroll_settings"
    __table_args__ = (
        UniqueConstraint("legal_entity_id", name="uq_payroll_settings_legal_entity_id"),
        CheckConstraint(
            f"pay_day BETWEEN 1 AND {LATEST_PAY_DAY}", name="ck_payroll_settings_pay_day"
        ),
        CheckConstraint("pay_month_offset IN (0, 1)", name="ck_payroll_settings_pay_month_offset"),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    pay_day: Mapped[int] = mapped_column(Integer, nullable=False)
    pay_month_offset: Mapped[int] = mapped_column(Integer, nullable=False)


class PayrollMonthClosure(Base, PrimaryKeyMixin, TimestampMixin):
    """A payroll month a person closed (spec § 4, REB-1045): while the row exists no payslip of
    the legal entity is recorded or corrected for `month`, and the month's estimates are what
    `snapshot` says, however the staff's pay inputs change afterwards. `snapshot` is the month
    as `PayrollService.get_payroll_month` answered when it was closed (a `PayrollMonthRead`),
    so closing fixes what the page showed. `month` is the first day of the month worked.

    Nothing is deleted: a closed month stays closed, and reopening is not offered.
    """

    __tablename__ = "payroll_month_closures"
    __table_args__ = (
        UniqueConstraint("legal_entity_id", "month", name="uq_payroll_month_closures_month"),
        CheckConstraint("extract(day FROM month) = 1", name="ck_payroll_month_closures_month"),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, index=True
    )
    month: Mapped[dt.date] = mapped_column(Date, nullable=False)
    closed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
