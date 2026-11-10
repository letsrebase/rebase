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
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin
from pigrocrm.core.fiscal.pack import PACK_IT_COMPANY, PACK_IT_FLAT_RATE, PACK_IT_STANDARD

KINDS: tuple[str, ...] = ("employee", "director")
# Employees are paid in 13 or 14 monthly payments (design § 1).
MONTHLY_PAYMENTS: tuple[int, ...] = (13, 14)
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
            "AND employer_inail_rate IS NULL)",
            name="ck_staff_members_director_columns",
        ),
        CheckConstraint(
            "gross_annual_pay IS NULL OR gross_annual_pay > 0", name="ck_staff_members_gross_pay"
        ),
        CheckConstraint("annual_fee IS NULL OR annual_fee > 0", name="ck_staff_members_fee"),
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
    gross_annual_pay: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    monthly_payments: Mapped[int | None] = mapped_column(Integer, default=None)
    # The share of a full-time contract, in percent.
    part_time_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    # The employer's rates in percent, typed per staff member: the sector and the risk class
    # are taxpayer facts the payroll consultant verifies. `NULL` is «not stated».
    employer_inps_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    employer_inail_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)

    # Director: the annual fee, how it is paid, the TFM accrued a year and the share of the
    # INPS «gestione separata» contribution the company bears, in percent (to verify).
    annual_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    fee_payment: Mapped[str | None] = mapped_column(String(10), default=None)
    accrues_tfm: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    tfm_annual_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    inps_company_share: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)


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
