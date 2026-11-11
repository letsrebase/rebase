"""Every amount that falls due, in or out, and the payments that settle it (spec
2026-11-09, due items and the cash schedule, § 1).

A `DueItem` is one amount owed by or to a counterparty on a date; a `DueItemSettlement` is a
payment against it, possibly partial. Both are written by `DueItemService` and by nothing
else. The tables are core: they exist whatever the module state, because an invoice needs
them (§ Scope).

The **source kind** and the **counterparty type** are validated in code, `SOURCE_KINDS` and
`COUNTERPARTY_TYPES`, so that a later project (suppliers, taxes, payroll) adds a kind
without a migration; `test_due_items.py` lists every kind. Only what the table's own
invariants need is a CHECK.
"""

import datetime as dt
from decimal import Decimal
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
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

DIRECTIONS: tuple[str, ...] = ("in", "out")
# `forecast` is computed from a rule and has no document yet; `expected` is fixed by a
# document; `settled` is paid in full; `dropped` is no longer owed. A partly paid item stays
# `expected` with what remains.
STATES: tuple[str, ...] = ("forecast", "expected", "settled", "dropped")
OPEN_STATES: tuple[str, ...] = ("forecast", "expected")

# The counterparty is an organisation (a customer today; a supplier or a freelancer once
# they are roles of the same record, spec 2026-11-09 suppliers § 1), the tax office with the tax
# it is for, or a staff member (the net pay and the 13th and 14th payments, REB-1008).
COUNTERPARTY_TYPES: tuple[str, ...] = ("organization", "tax_office", "staff_member")

# Where an item comes from. The column is a plain string, never a CHECK: a later project adds
# its kind here and in `RESTRICTED_SOURCE_KINDS` when it needs the fiscal gate.
SOURCE_KINDS: tuple[str, ...] = (
    "sales_invoice",
    "purchase",
    "integration_vat",
    "iva_settlement",
    "withholding",
    "tax_instalment",
    "tax_balance",
    "tax_advance",
    "chamber_fee",
    "notice_plan",
    # A tax credit as a negative item (`fiscal/tax_credits.py`, REB-1076): a withholding suffered
    # on sales or an IVA credit carried forward, grouped with the day's F24.
    "tax_credit",
    "payroll",
    "contract_occurrence",
    "recurring_expense",
    "cash_basis_vat",
    "engagement",
    # A draft invoice, or a draft or confirmed proforma, before it has a number: a forecast
    # in, replaced by the issued invoice (§ 1.2).
    "sales_draft",
    # What an approved expense note owes the staff member who paid (`expense_notes/service.py`):
    # restricted, since it names a person and what they are paid back.
    "expense_note",
)
# The source of an income tax's balance and of its advances (`fiscal/tax_items.py`, REB-1074):
# restricted below, since a tax due item is the fiscal position.
TAX_BALANCE_SOURCE_KIND = "tax_balance"
TAX_ADVANCE_SOURCE_KIND = "tax_advance"
# The annual chamber of commerce fee of an srl (`fiscal/tax_items.py`, REB-1077): restricted too.
CHAMBER_FEE_SOURCE_KIND = "chamber_fee"
# The source of an instalment of a tax plan (`fiscal/instalment_plan.py`, REB-1075) and of an
# instalment of a notice from the tax office entered by hand (`fiscal/notice_plan.py`).
TAX_INSTALMENT_SOURCE_KIND = "tax_instalment"
NOTICE_PLAN_SOURCE_KIND = "notice_plan"
# The source of a tax credit (a negative item, taxes design § 2): restricted below like the taxes.
TAX_CREDIT_SOURCE_KIND = "tax_credit"
# What a tax item's description ends with while a difference carried onto it from an advance
# already paid waits for a person to confirm it (`fiscal/tax_items.py`, REB-1074).
CARRY_TO_CONFIRM_SUFFIX = "da confermare"
# The source of an approved expense note's one due item (`expense_notes/service.py`).
EXPENSE_NOTE_SOURCE_KIND = "expense_note"
# The source of what staff cost and when it leaves (`staff/payroll.py`).
PAYROLL_SOURCE_KIND = "payroll"
# The kinds only an administrator reads and settles, and an agent only with `full_access`:
# what the tax, IVA, withholding and payroll sources disclose is the fiscal position (spec §
# 5, which names the source's own permission), and an engagement's items disclose a
# freelancer's pay. Everything else is read by any member.
RESTRICTED_SOURCE_KINDS: frozenset[str] = frozenset(
    {
        "integration_vat",
        "iva_settlement",
        "withholding",
        TAX_INSTALMENT_SOURCE_KIND,
        NOTICE_PLAN_SOURCE_KIND,
        TAX_BALANCE_SOURCE_KIND,
        TAX_ADVANCE_SOURCE_KIND,
        CHAMBER_FEE_SOURCE_KIND,
        TAX_CREDIT_SOURCE_KIND,
        PAYROLL_SOURCE_KIND,
        "cash_basis_vat",
        # What an engagement owes a freelancer names them and what they are paid (collaborators
        # design § 4): an administrator's, and a `full_access` agent's.
        "engagement",
        # An approved expense note's reimbursement names the staff member and the amount: an
        # administrator's, and the staff member's own (`DueItemService.get` and `list`).
        EXPENSE_NOTE_SOURCE_KIND,
    }
)

# The four due rules (§ 1.3).
RULE_KINDS: tuple[str, ...] = (
    "fixed",
    "after_settlement",
    "month_after_settlement_on_day",
    "on_settlement",
)

# The cadences of a recurring expense, by the months between two of its due dates (§ 1.2).
CADENCE_MONTHS: dict[str, int] = {
    "monthly": 1,
    "bimonthly": 2,
    "quarterly": 3,
    "semiannual": 6,
    "annual": 12,
}
CADENCES: tuple[str, ...] = tuple(CADENCE_MONTHS)

CURRENCY_PATTERN = "^[A-Z]{3}$"
TEXT_MAX_LENGTH = 255
LATEST_DAY_OF_MONTH = 28


class DueItem(Base, PrimaryKeyMixin, TimestampMixin):
    """One amount that falls due.

    `amount` is in the document's currency and is never zero; it is negative only for a
    credit (a tax credit is a negative item, spec taxes § 2), and everything below reads the
    sign. `own_date` is the date the document states, or the floor of a dependent rule;
    `due_date` is the date the schedule reads, stored by the service so a list can filter and
    sort on it (`rules.py` computes it from the rule and the dependency, and the service
    refreshes it whenever the dependency moves). `due_date_estimated` says the date follows a
    dependency that is not settled yet, so the schedule draws it lighter.
    """

    __tablename__ = "due_items"
    __table_args__ = (
        # The target of the composite foreign keys below: a settlement and a dependent must
        # name the same legal entity as the item they hang on, enforced by the schema.
        UniqueConstraint("id", "legal_entity_id", name="uq_due_items_id_legal_entity_id"),
        ForeignKeyConstraint(
            ["depends_on_id", "legal_entity_id"],
            ["due_items.id", "due_items.legal_entity_id"],
            name="fk_due_items_depends_on_same_legal_entity",
        ),
        CheckConstraint(f"direction IN {DIRECTIONS!r}", name="ck_due_items_direction"),
        CheckConstraint(f"state IN {STATES!r}", name="ck_due_items_state"),
        CheckConstraint(f"rule_kind IN {RULE_KINDS!r}", name="ck_due_items_rule_kind"),
        CheckConstraint(
            f"counterparty_type IN {COUNTERPARTY_TYPES!r}", name="ck_due_items_counterparty_type"
        ),
        CheckConstraint(
            "(counterparty_type = 'organization' AND organization_id IS NOT NULL "
            "AND tax IS NULL AND staff_member_id IS NULL) OR "
            "(counterparty_type = 'tax_office' AND tax IS NOT NULL AND organization_id IS NULL "
            "AND staff_member_id IS NULL) OR "
            "(counterparty_type = 'staff_member' AND staff_member_id IS NOT NULL "
            "AND organization_id IS NULL AND tax IS NULL)",
            name="ck_due_items_counterparty_columns",
        ),
        CheckConstraint("amount <> 0", name="ck_due_items_amount_not_zero"),
        CheckConstraint(f"currency ~ '{CURRENCY_PATTERN}'", name="ck_due_items_currency"),
        # A forecast names the rule that produced it; the other states have a document.
        CheckConstraint(
            "state <> 'forecast' OR source_rule IS NOT NULL", name="ck_due_items_forecast_rule"
        ),
        CheckConstraint(
            "depends_on_id IS NULL OR depends_on_id <> id", name="ck_due_items_no_self"
        ),
        # The rule's own columns agree with its kind: a fixed date depends on nothing, every
        # other kind names its dependency (or, for `after_settlement`, waits for it with its
        # backstop and the offset it will date from), the day of the month belongs to one kind
        # and the offset to another.
        CheckConstraint(
            "(rule_kind = 'fixed' AND depends_on_id IS NULL AND rule_day_of_month IS NULL "
            "AND rule_not_after IS NULL AND rule_offset_days = 0) OR "
            "(rule_kind <> 'fixed' AND depends_on_id IS NOT NULL) OR "
            "(rule_kind = 'after_settlement' AND depends_on_id IS NULL "
            "AND rule_not_after IS NOT NULL AND rule_day_of_month IS NULL)",
            name="ck_due_items_rule_columns",
        ),
        CheckConstraint(
            "(rule_kind = 'month_after_settlement_on_day') = (rule_day_of_month IS NOT NULL)",
            name="ck_due_items_rule_day_of_month",
        ),
        CheckConstraint(
            "rule_kind = 'after_settlement' OR rule_offset_days = 0",
            name="ck_due_items_rule_offset",
        ),
        CheckConstraint("rule_offset_days >= 0", name="ck_due_items_rule_offset_not_negative"),
        CheckConstraint(
            f"rule_day_of_month IS NULL OR rule_day_of_month BETWEEN 1 AND {LATEST_DAY_OF_MONTH}",
            name="ck_due_items_rule_day_range",
        ),
        # The schedule reads open items by date, and a source finds its own items.
        Index("ix_due_items_schedule", "legal_entity_id", "state", "due_date"),
        Index("ix_due_items_source", "source_kind", "source_id"),
        Index("ix_due_items_depends_on_id", "depends_on_id"),
        # One live forecast per key: a race between two writers of the same source cannot
        # double it (spec 2026-11-09, due items, § 1.2).
        Index(
            "uq_due_items_forecast_key",
            "forecast_key",
            unique=True,
            postgresql_where=text("state = 'forecast' AND forecast_key IS NOT NULL"),
        ),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    counterparty_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # The organisation owed or owing. Named for what it is and not for the table it points at
    # today (`organizations`, which the organisations rename takes over): a supplier is a row of
    # the same table.
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None, index=True
    )
    # The staff member paid, for a net pay or a 13th or 14th payment.
    staff_member_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_members.id"), default=None, index=True
    )
    # Which tax, for the tax office (`vat`, `withholding`, ...): the counterparty of a tax
    # item is the office *and* the tax it is for.
    tax: Mapped[str | None] = mapped_column(String(30), default=None)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EUR", server_default="EUR"
    )
    description: Mapped[str | None] = mapped_column(String(TEXT_MAX_LENGTH), default=None)
    source_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[UUID | None] = mapped_column(default=None)
    # The rule that produced a forecast, by name (`contract_recurring_fee`, ...).
    source_rule: Mapped[str | None] = mapped_column(String(60), default=None)
    # The identity of a forecast: a uuid5 of its source kind, source and period, so the same
    # forecast is found again and never written twice.
    forecast_key: Mapped[UUID | None] = mapped_column(default=None)
    own_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    due_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    due_date_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    rule_kind: Mapped[str] = mapped_column(
        String(30), nullable=False, default="fixed", server_default="fixed"
    )
    depends_on_id: Mapped[UUID | None] = mapped_column(default=None)
    rule_offset_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    rule_day_of_month: Mapped[int | None] = mapped_column(Integer, default=None)
    rule_not_after: Mapped[dt.date | None] = mapped_column(Date, default=None)
    dropped_reason: Mapped[str | None] = mapped_column(String(TEXT_MAX_LENGTH), default=None)
    # When an administrator confirmed the difference carried onto this tax item (REB-1079). The
    # item is dropped and rewritten when the difference changes, which clears it.
    carry_confirmed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class DueItemSettlement(Base, PrimaryKeyMixin, TimestampMixin):
    """One payment against a due item.

    `amount` is in the item's currency, with the item's sign; `account_amount` and
    `account_currency` are what left or reached the account when it was another currency
    (both or neither). `settled_on` is the booking date and `value_date` the bank's value
    date when it gave one. Who recorded it is `recorded_by_id` and `recorded_by_type`, the
    pair the timeline stores (a reference, never a foreign key, so a user can be archived).

    A settlement is never deleted: **reversing** it stamps `reversed_at`, and only a live one
    (`reversed_at IS NULL`) counts toward what remains. A settlement is a cash fact: no month
    lock applies to it (spec § 3). `bank_movement_id` is the movement that proves it (REB-1042).
    """

    __tablename__ = "due_item_settlements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["due_item_id", "legal_entity_id"],
            ["due_items.id", "due_items.legal_entity_id"],
            name="fk_due_item_settlements_same_legal_entity",
        ),
        # The movement that proves the payment: the same legal entity as the item's.
        ForeignKeyConstraint(
            ["bank_movement_id", "legal_entity_id"],
            ["bank_movements.id", "bank_movements.legal_entity_id"],
            name="fk_due_item_settlements_movement_same_legal_entity",
        ),
        CheckConstraint("amount <> 0", name="ck_due_item_settlements_amount_not_zero"),
        CheckConstraint(
            "(account_amount IS NULL) = (account_currency IS NULL)",
            name="ck_due_item_settlements_account_pair",
        ),
        CheckConstraint(
            f"account_currency IS NULL OR account_currency ~ '{CURRENCY_PATTERN}'",
            name="ck_due_item_settlements_account_currency",
        ),
        CheckConstraint(
            "(reversed_at IS NULL) = (reversed_by_type IS NULL)",
            name="ck_due_item_settlements_reversal_pair",
        ),
        Index("ix_due_item_settlements_due_item_id", "due_item_id"),
        Index("ix_due_item_settlements_settled_on", "legal_entity_id", "settled_on"),
        Index("ix_due_item_settlements_bank_movement_id", "bank_movement_id"),
    )

    due_item_id: Mapped[UUID] = mapped_column(nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    account_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    account_currency: Mapped[str | None] = mapped_column(String(3), default=None)
    settled_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    value_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # The bank movement that proves the payment, when a match wrote or adopted it (REB-1042); null
    # for a payment marked by hand. Only `MovementMatchService` sets and clears it.
    bank_movement_id: Mapped[UUID | None] = mapped_column(default=None)
    note: Mapped[str | None] = mapped_column(String(TEXT_MAX_LENGTH), default=None)
    recorded_by_id: Mapped[UUID | None] = mapped_column(default=None)
    recorded_by_type: Mapped[str] = mapped_column(String(10), nullable=False)
    reversed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reversed_by_id: Mapped[UUID | None] = mapped_column(default=None)
    reversed_by_type: Mapped[str | None] = mapped_column(String(10), default=None)
    reversal_reason: Mapped[str | None] = mapped_column(String(TEXT_MAX_LENGTH), default=None)


class RecurringExpense(Base, PrimaryKeyMixin, TimestampMixin):
    """A recurrence rule owned by due items (spec 2026-11-09, § 1.2): a supplier that bills the
    same amount every `cadence`, from the due date `first_due_date`. The daily pass and every
    change to the rule write one **forecast out** (source kind `recurring_expense`) for each
    occurrence inside the horizon; the purchase of that period replaces it, never adds to it.

    `amount` is the total a payment carries, in euro, VAT included. A rule is stopped, never
    deleted: `stopped_on` is the day it stopped and the forecasts it had are dropped, while the
    due items its past occurrences became stay where they are. `ends_on` is the last due date a
    rule with an end produces.
    """

    __tablename__ = "recurring_expenses"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_recurring_expenses_amount_positive"),
        CheckConstraint(f"cadence IN {CADENCES!r}", name="ck_recurring_expenses_cadence"),
        CheckConstraint(
            "ends_on IS NULL OR ends_on >= first_due_date", name="ck_recurring_expenses_ends_on"
        ),
        Index("ix_recurring_expenses_supplier", "supplier_id"),
        Index("ix_recurring_expenses_active", "legal_entity_id", "stopped_on"),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, index=True
    )
    supplier_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    category_id: Mapped[UUID] = mapped_column(ForeignKey("cost_categories.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(TEXT_MAX_LENGTH), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cadence: Mapped[str] = mapped_column(String(12), nullable=False)
    first_due_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[dt.date | None] = mapped_column(Date, default=None)
    stopped_on: Mapped[dt.date | None] = mapped_column(Date, default=None)
