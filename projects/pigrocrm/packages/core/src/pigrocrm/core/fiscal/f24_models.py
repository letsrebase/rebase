"""The rows behind a tax credit and an F24 (REB-1076, taxes design § 2).

A **tax credit** (`TaxCredit`) is what a negative due item of source kind `tax_credit` points at:
it carries the origin of the credit, so the item says where it came from. **No code writes one
today** (REB-1100): a withholding suffered is netted inside the income-tax return (RN33 col. 4
into RN34) and an IVA credit is taken in by the next periodic settlement, so neither is a line of
an F24, and the two origins below are retired. The rows stay for the F24 credit-line machinery of
`F24GroupService`, which a future origin (a credit a return shows and a person uses in
compensation, art. 17 D.Lgs. 241/1997) reaches through a new origin and a migration, and for the
credit lines of an F24 already settled, which keep their history.

An **F24 group** (`F24Group`) is the due items of one date, credits included, that are paid with
one F24: its lines (`F24GroupItem`) keep the tax code each had when they were grouped, so a paid
F24 still reads what was paid whatever the pack says later, and its total is the net of the lines.
One bank movement equal to the net settles the group and every item in it
(`bank/matching.py`); a group whose net is zero or a credit has no movement to match and is settled
by a person marking it filed (`F24GroupService.file_f24`, REB-1093): `bank_movement_id` stays null,
`settled_on` is the day it was filed, and each line says how much of it the F24 took
(`used_amount`) and which payment wrote that (`settlement_id`). A group's **receipts**
(`F24GroupReceipt`) are inbox items of kind `tax_document`: the file lives in the inbox's document
storage, the group only points at it. Written only by `F24GroupService`.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
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

CREDIT_ORIGINS: tuple[str, ...] = ("iva_carry_forward", "withholding_suffered")
GROUP_STATES: tuple[str, ...] = ("open", "settled", "dissolved")
CODE_KINDS: tuple[str, ...] = ("tax_code", "inps_causale")
NOTE_MAX_LENGTH = 255


class TaxCredit(Base, PrimaryKeyMixin, TimestampMixin):
    """A credit the legal entity holds against the tax office, as a positive `amount` (its due
    item carries it negative). `origin` says where it comes from: `iva_carry_forward` was the
    credit a settled IVA period leaves to the next one (`iva_settlement_id` names that period),
    `withholding_suffered` what customers withheld on the invoices collected in `tax_year`, a tax
    year of the income tax (`tax` is `irpef`). Both origins are retired (migration 0116) and
    nothing writes a row of this table."""

    __tablename__ = "tax_credits"
    __table_args__ = (
        CheckConstraint(f"origin IN {CREDIT_ORIGINS!r}", name="ck_tax_credits_origin"),
        CheckConstraint("amount > 0", name="ck_tax_credits_amount_positive"),
        CheckConstraint(
            "(origin = 'iva_carry_forward') = (iva_settlement_id IS NOT NULL)",
            name="ck_tax_credits_iva_settlement",
        ),
        # One credit per IVA period, and one withholding credit per legal entity and tax year.
        Index(
            "uq_tax_credits_iva_settlement",
            "iva_settlement_id",
            unique=True,
            postgresql_where=text("iva_settlement_id IS NOT NULL"),
        ),
        Index(
            "uq_tax_credits_withholding_year",
            "legal_entity_id",
            "tax_year",
            unique=True,
            postgresql_where=text("origin = 'withholding_suffered'"),
        ),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, index=True
    )
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
    tax: Mapped[str] = mapped_column(String(30), nullable=False)
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    iva_settlement_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("iva_settlements.id"), default=None
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class F24Group(Base, PrimaryKeyMixin, TimestampMixin):
    """The due items of one date that are paid with one F24. `open` until the movement that pays
    its net is confirmed (`settled`, with the movement and the day) or, when its net is zero or a
    credit, until a person marks it filed (`settled`, the day it was filed and no movement),
    `dissolved` once a person ungroups it: its lines are released and the items are free again.
    Who grouped it is `created_by_id` and `created_by_type`, the pair the timeline stores."""

    __tablename__ = "f24_groups"
    __table_args__ = (
        UniqueConstraint("id", "legal_entity_id", name="uq_f24_groups_id_legal_entity_id"),
        ForeignKeyConstraint(
            ["bank_movement_id", "legal_entity_id"],
            ["bank_movements.id", "bank_movements.legal_entity_id"],
            name="fk_f24_groups_movement_same_legal_entity",
        ),
        CheckConstraint(f"state IN {GROUP_STATES!r}", name="ck_f24_groups_state"),
        # A settled group has its day; a movement only ever pays a settled one. A group settled
        # without a movement was marked filed (its net was zero or a credit).
        CheckConstraint(
            "(state = 'settled') = (settled_on IS NOT NULL) "
            "AND (bank_movement_id IS NULL OR state = 'settled')",
            name="ck_f24_groups_settled",
        ),
        Index("ix_f24_groups_schedule", "legal_entity_id", "state", "payment_date"),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, index=True
    )
    payment_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(
        String(10), nullable=False, default="open", server_default="open"
    )
    note: Mapped[str | None] = mapped_column(String(NOTE_MAX_LENGTH), default=None)
    bank_movement_id: Mapped[UUID | None] = mapped_column(default=None)
    settled_on: Mapped[dt.date | None] = mapped_column(Date, default=None)
    created_by_id: Mapped[UUID | None] = mapped_column(default=None)
    created_by_type: Mapped[str] = mapped_column(String(10), nullable=False)


class F24GroupItem(Base, PrimaryKeyMixin, TimestampMixin):
    """One line of an F24: a due item and the code it is paid under. `amount` is the item's own
    signed amount when it was grouped (negative for a credit). The code is the pack's
    (`code`, its `code_kind` and the issuing body's `code_label`) or absent, which the surfaces
    read as «codice da verificare»: a line whose code the pack does not carry is never given a
    guessed one. `reference_year` is the year of the `anno di riferimento` column when it is
    known; `reference_period` is the month a withholding line was operated in (`MM`, the
    `mese rif.` column) or the month of a payroll contribution (`MM/AAAA`, the INPS section's
    «periodo da»), when the line has one. `released_at` is set when the group is dissolved; a live
    line has none, and an item is in at most one live line.

    Once the group is settled `used_amount` is what the F24 took of the line, with the line's sign:
    all of it when a movement paid the group, and for a group marked filed all of a debit and the
    part of a credit that covered the debits. A credit the F24 did not use up is released, so the
    rest is free to go in another F24: `released_at` there says «what was left went back», not
    that the group was taken apart. `settlement_id` is the payment a filing wrote for the line
    (null for a movement's match and for an unused credit), which reopening reverses."""

    __tablename__ = "f24_group_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id", "legal_entity_id"],
            ["f24_groups.id", "f24_groups.legal_entity_id"],
            name="fk_f24_group_items_same_legal_entity",
        ),
        ForeignKeyConstraint(
            ["due_item_id", "legal_entity_id"],
            ["due_items.id", "due_items.legal_entity_id"],
            name="fk_f24_group_items_due_item_same_legal_entity",
        ),
        CheckConstraint("amount <> 0", name="ck_f24_group_items_amount_not_zero"),
        # What the F24 took has the line's sign and is at most the line.
        CheckConstraint(
            "used_amount IS NULL "
            "OR (used_amount * amount >= 0 AND abs(used_amount) <= abs(amount))",
            name="ck_f24_group_items_used_within_amount",
        ),
        CheckConstraint(
            f"code_kind IS NULL OR code_kind IN {CODE_KINDS!r}", name="ck_f24_group_items_code_kind"
        ),
        CheckConstraint(
            "(code IS NULL) = (code_kind IS NULL)", name="ck_f24_group_items_code_pair"
        ),
        Index("ix_f24_group_items_group_id", "group_id"),
        Index(
            "uq_f24_group_items_live",
            "due_item_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )

    group_id: Mapped[UUID] = mapped_column(nullable=False)
    due_item_id: Mapped[UUID] = mapped_column(nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line: Mapped[str | None] = mapped_column(String(40), default=None)
    code: Mapped[str | None] = mapped_column(String(10), default=None)
    code_kind: Mapped[str | None] = mapped_column(String(15), default=None)
    code_label: Mapped[str | None] = mapped_column(String(255), default=None)
    reference_year: Mapped[int | None] = mapped_column(Integer, default=None)
    reference_period: Mapped[str | None] = mapped_column(String(7), default=None)
    released_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    used_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    settlement_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("due_item_settlements.id"), default=None
    )


class F24GroupReceipt(Base, PrimaryKeyMixin, TimestampMixin):
    """An inbox item of kind `tax_document` that is the receipt of an F24 group: the file stays in
    the inbox's storage and the group points at it. A group has each item once."""

    __tablename__ = "f24_group_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["group_id", "legal_entity_id"],
            ["f24_groups.id", "f24_groups.legal_entity_id"],
            name="fk_f24_group_receipts_same_legal_entity",
        ),
        UniqueConstraint("group_id", "inbox_item_id", name="uq_f24_group_receipts_item"),
        Index("ix_f24_group_receipts_inbox_item_id", "inbox_item_id"),
    )

    group_id: Mapped[UUID] = mapped_column(nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=False, index=True
    )
    inbox_item_id: Mapped[UUID] = mapped_column(ForeignKey("inbox_items.id"), nullable=False)
    attached_by_id: Mapped[UUID | None] = mapped_column(default=None)
    attached_by_type: Mapped[str] = mapped_column(String(10), nullable=False)
