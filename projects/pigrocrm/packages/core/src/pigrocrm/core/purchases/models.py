"""Everything the space pays for, with its document (spec 2026-11-09, suppliers, purchases and
the IVA cycle, § 2).

A **purchase** replaces the old cost: the same row, kept with its id, its `created_at`, its
custom fields, its soft delete and its history, now with a kind and the fields a supplier's
document carries. The kinds:

- `invoice` and `credit_note` come from a supplier and name it; their VAT is split into recap
  lines (`PurchaseVatLine`), one per rate and nature, each with the share that is deductible;
- `receipt` (a till receipt, an occasional worker's receipt, a ticket) and `expense` (anything
  else paid) carry the total only. VAT on a retail receipt is not deductible without an
  invoice (art. 19 c. 1 and art. 22 DPR 633/72, A5), so a receipt has no lines: the day the
  shop issues an invoice, that is a purchase of kind `invoice`;
- `expense_note` is what a staff member spent and the company pays back: only the approval of
  an expense note writes one (`expense_notes/service.py`), with the staff member who paid in
  `staff_member_id`, and it carries the total only, like a receipt.

A row that was a cost is an `expense` with the free-text supplier in `merchant_name`; nothing
about what it says or counts changed (`cost_amount = amount`).

`amount` is the **total, in euro**, VAT included, as the cost was: the number a person reads on
the document, signed (a refund is a negative expense, a credit note a negative `credit_note`).
`cost_amount` is what the purchase **costs**, frozen at registration from the buyer's pack
(`purchases/cost.py`); the profit and loss, the deal's profit and loss and the cash picture
read it, and nothing else, so one rule decides it. `buyer_pack_id` and `buyer_regime_code`
are frozen with it, as an issued invoice freezes its snapshot, since `FiscalProfile` is not
historicised.

`deal_id IS NULL` means a general expense: it enters the period profit and loss in a row of
its own and is never apportioned onto a deal (§ 7.4 of the slice 4 design).
"""

import datetime as dt
from decimal import Decimal
from typing import Any
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
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin
from pigrocrm.core.purchases.deductibility import ASSET_GROUPS, DEDUCTIBILITY_KINDS

KINDS: tuple[str, ...] = ("invoice", "credit_note", "receipt", "expense", "expense_note")
# The kinds whose VAT is split into recap lines, and that a supplier issues.
VAT_KINDS: tuple[str, ...] = ("invoice", "credit_note")
STATUSES: tuple[str, ...] = ("registered", "cancelled")
BASE_CURRENCY = "EUR"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _cost_defaults_to_total(context: DefaultExecutionContext) -> Decimal:
    """A row written without a cost costs its total: what every cost did, and what a row with
    no pack decision (a shared purchase, a receipt, an expense) costs. The service always
    writes the figure; this is for a row built by hand."""
    # SQLAlchemy ships `get_current_parameters` without annotations.
    amount: Decimal = context.get_current_parameters()["amount"]  # type: ignore[no-untyped-call]
    return amount


class Purchase(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "purchases"
    __table_args__ = (
        # Zero is refused because it is neither a purchase nor a correction; negative is a
        # refund or a credit note received (slice 4 §4.4).
        CheckConstraint("amount <> 0", name="ck_purchases_amount_not_zero"),
        CheckConstraint(_in("kind", KINDS), name="ck_purchases_kind"),
        CheckConstraint(_in("status", STATUSES), name="ck_purchases_status"),
        # An invoice is an amount owed and a credit note one that reduces it: the sign is the
        # kind, so a reader that sums `cost_amount` needs no case for either.
        CheckConstraint(
            "(kind <> 'invoice' OR amount > 0) AND (kind <> 'credit_note' OR amount < 0)",
            name="ck_purchases_amount_sign",
        ),
        # A supplier's document names the supplier; a receipt or an expense need not.
        CheckConstraint(
            "kind NOT IN ('invoice', 'credit_note') OR supplier_id IS NOT NULL",
            name="ck_purchases_supplier_required",
        ),
        CheckConstraint(
            "corrects_purchase_id IS NULL OR kind = 'credit_note'",
            name="ck_purchases_corrects_credit_note",
        ),
        # The currency and the euro amount beside it: a euro purchase carries neither a
        # foreign amount nor a rate, a foreign one carries all three.
        CheckConstraint(
            "(currency = 'EUR' AND currency_amount IS NULL AND exchange_rate IS NULL "
            "AND exchange_rate_date IS NULL) OR (currency <> 'EUR' AND currency_amount IS NOT "
            "NULL AND exchange_rate IS NOT NULL AND exchange_rate > 0 AND exchange_rate_date IS "
            "NOT NULL)",
            name="ck_purchases_currency",
        ),
        CheckConstraint(
            "(withholding_rate IS NULL AND withholding_base IS NULL AND withholding_amount IS "
            "NULL) OR (withholding_rate IS NOT NULL AND withholding_base IS NOT NULL AND "
            "withholding_amount IS NOT NULL)",
            name="ck_purchases_withholding_together",
        ),
        CheckConstraint(
            "(fund_rate IS NULL AND fund_amount IS NULL) OR (fund_rate IS NOT NULL AND "
            "fund_amount IS NOT NULL)",
            name="ck_purchases_fund_together",
        ),
        # An expense note names the staff member who paid and nothing else does.
        CheckConstraint(
            "(kind = 'expense_note') = (staff_member_id IS NOT NULL)",
            name="ck_purchases_expense_note_staff",
        ),
        # What only a supplier's invoice or credit note carries.
        CheckConstraint(
            "kind IN ('invoice', 'credit_note') OR (NOT split_payment AND NOT reverse_charge "
            "AND iva_period IS NULL AND fund_rate IS NULL)",
            name="ck_purchases_vat_kinds_only",
        ),
        # Split payment is for a buyer that is not the VAT debtor; under reverse charge it is
        # (art. 17-ter c. 1 DPR 633/1972, Circolare 14/E of 2015 § 4): never both (REB-1099).
        CheckConstraint(
            "NOT (split_payment AND reverse_charge)",
            name="ck_purchases_split_payment_not_reverse_charge",
        ),
        CheckConstraint(
            f"deductibility_kind IS NULL OR {_in('deductibility_kind', DEDUCTIBILITY_KINDS)}",
            name="ck_purchases_deductibility_kind",
        ),
        # An asset is a positive purchase that a supplier's invoice, a receipt or an expense
        # records: a credit note or a refund is not one.
        CheckConstraint(
            f"asset_group IS NULL OR ({_in('asset_group', ASSET_GROUPS)} AND amount > 0 "
            "AND kind IN ('invoice', 'receipt', 'expense'))",
            name="ck_purchases_asset_group",
        ),
        Index(
            "ix_purchases_deal_date", "deal_id", "date", postgresql_where=text("deleted_at IS NULL")
        ),
        # The period P&L reads purchases by date across every deal *and* the general
        # expenses, where `deal_id` is NULL and the two-column index above cannot help.
        Index("ix_purchases_date", "date", postgresql_where=text("deleted_at IS NULL")),
        Index("ix_purchases_custom_fields", "custom_fields", postgresql_using="gin"),
    )

    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="expense", server_default=text("'expense'")
    )
    # `registered` or `cancelled`: a cancelled purchase stays on the record and counts nowhere.
    # An archive (`deleted_at`) is the other way a row leaves the readers, and it is reversible.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="registered", server_default=text("'registered'")
    )
    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("deals.id"), default=None, index=True)
    # The deal's legal entity when the purchase has a deal, `NULL` when it has none: a shared
    # purchase («condivisa», REB-623, spec §1.7), which only the view over every legal entity
    # shows. No column default: `NULL` is a meaning here, not an omission.
    legal_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_entities.id"), default=None, index=True
    )
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("cost_categories.id"), nullable=False, index=True
    )
    # An organization with the `supplier` role. Required on an invoice and a credit note,
    # optional on a receipt, which names only a shop in `merchant_name`.
    supplier_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None, index=True
    )
    merchant_name: Mapped[str | None] = mapped_column(String(200), default=None)
    # The staff member an expense note pays back: set on a purchase of kind `expense_note` only.
    staff_member_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_members.id"), default=None, index=True
    )
    # The number on the supplier's document.
    number: Mapped[str | None] = mapped_column(String(50), default=None)
    # The document's date: the date every reader attributes the purchase to.
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    received_on: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # A day inside the IVA period the purchase is registered in; only an invoice or a credit
    # note has one (§ 6). A person chooses it; it defaults to the day of receipt.
    iva_period: Mapped[dt.date | None] = mapped_column(Date, default=None)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default=BASE_CURRENCY, server_default=text("'EUR'")
    )
    # The total in `currency`, with the euro rate and the day it is from; all `NULL` in euro.
    currency_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), default=None)
    exchange_rate_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # The total paid, in euro, VAT included. Under the flat-rate regime input VAT is not
    # deductible, so it is cost in every sense and recording the net would understate the cost
    # by 22% (slice 4 §4.4).
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    cost_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=_cost_defaults_to_total
    )
    stamp_duty: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    withholding_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    withholding_base: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    withholding_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    fund_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    fund_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    split_payment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    reverse_charge: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # How the law treats the cost, when a person has said (`purchases/deductibility.py`, REB-1089):
    # the kind overrides its category's, and `asset_group` flags a depreciable asset. `NULL` states
    # nothing: the purchase is deducted in full.
    deductibility_kind: Mapped[str | None] = mapped_column(String(20), default=None)
    asset_group: Mapped[str | None] = mapped_column(String(20), default=None)
    # The invoice a credit note corrects.
    corrects_purchase_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchases.id"), default=None, index=True
    )
    inbox_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inbox_items.id"), default=None, index=True
    )
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id"), default=None)
    # The buyer's pack and regime at registration (see the module docstring): `NULL` on a
    # shared purchase and on a row that was a cost, which had no pack decision.
    buyer_pack_id: Mapped[str | None] = mapped_column(String(40), default=None)
    buyer_pack_version: Mapped[str | None] = mapped_column(String(10), default=None)
    buyer_regime_code: Mapped[str | None] = mapped_column(String(4), default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    # The recap lines live and die with the purchase: replaced whole by `PurchaseService`, in
    # position order, loaded with the row so a page of purchases costs one more query.
    vat_lines: Mapped[list["PurchaseVatLine"]] = relationship(
        order_by="PurchaseVatLine.position",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PurchaseVatLine(Base, PrimaryKeyMixin):
    """One VAT recap group of a supplier's invoice or credit note: a rate (and a nature when
    the rate is zero), the taxable amount, the VAT, and the share of it that is deductible.

    A credit note carries negative amounts, like the purchase. Written and replaced only with
    its purchase (`PurchaseService`); it has no soft delete and no history of its own.
    """

    __tablename__ = "purchase_vat_lines"
    __table_args__ = (
        CheckConstraint("rate >= 0 AND rate <= 100", name="ck_purchase_vat_lines_rate"),
        CheckConstraint(
            "deductible_share >= 0 AND deductible_share <= 100",
            name="ck_purchase_vat_lines_share",
        ),
        # `Natura` is given exactly when the rate is zero, and then the VAT is zero too.
        CheckConstraint(
            "(rate = 0 AND nature IS NOT NULL AND vat_amount = 0) OR (rate > 0 AND nature IS NULL)",
            name="ck_purchase_vat_lines_nature",
        ),
        Index("ix_purchase_vat_lines_purchase", "purchase_id", "position"),
    )

    purchase_id: Mapped[UUID] = mapped_column(
        ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    nature: Mapped[str | None] = mapped_column(String(4), default=None)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    deductible_share: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("100.00"), server_default=text("100")
    )
