"""Create, update, read and list shapes of a purchase.

`max_length` mirrors every `String(n)` and `max_digits`/`decimal_places` every `Numeric(p, s)`,
so an over-long value or an over-capacity number is a 422 naming the field and never a raw
`DataError` out of `flush()`.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from pigrocrm.core.money import MONEY_SCALE
from pigrocrm.core.validation import SafeStr

DESCRIPTION_MAX_LENGTH = 2000
MERCHANT_MAX_LENGTH = 200
NUMBER_MAX_LENGTH = 50
MONEY_MAX_DIGITS = 12
RATE_MAX_DIGITS = 5
RATE_SCALE = 2
EXCHANGE_RATE_MAX_DIGITS = 14
EXCHANGE_RATE_SCALE = 6

PurchaseKind = Literal["invoice", "credit_note", "receipt", "expense"]
PurchaseStatus = Literal["registered", "cancelled"]

# `Natura` of FatturaPA: `N1`, `N2.1`, `N3.4`, `N6.9`, `N7`. The shape is checked here; which
# nature fits which operation is the document's own business.
Nature = Annotated[str, StringConstraints(pattern=r"^N[1-7](\.[1-9])?$")]
# ISO 4217: three capital letters.
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]

Money = Annotated[Decimal, Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE)]
Percent = Annotated[
    Decimal, Field(max_digits=RATE_MAX_DIGITS, decimal_places=RATE_SCALE, ge=0, le=100)
]


class VatLine(BaseModel):
    """One VAT recap group: a rate, its nature when the rate is zero, the taxable amount, the
    VAT and the share of it that is deductible (100 unless a rule says otherwise)."""

    rate: Percent
    nature: Nature | None = None
    taxable_amount: Money
    vat_amount: Money
    deductible_share: Percent = Decimal("100.00")


class VatLineRead(VatLine):
    model_config = ConfigDict(from_attributes=True)

    position: int


class PurchaseCreate(BaseModel):
    # A key that is not a field is refused: the free-text `supplier` of the old cost is
    # `merchant_name` now, and a stale caller must hear it and not lose the value in silence.
    model_config = ConfigDict(extra="forbid")

    kind: PurchaseKind = "expense"
    deal_id: UUID | None = None
    # Read only for a purchase without a deal (REB-623, spec §1.7): one with a deal takes the
    # deal's legal entity and refuses a different one; without a deal, `None` is a shared
    # purchase («condivisa») and an id ties it to one legal entity.
    legal_entity_id: UUID | None = None
    category_id: UUID
    # The supplier, an organization with the `supplier` role: required on an invoice and a
    # credit note.
    supplier_id: UUID | None = None
    # A shop or a payee that is not a supplier of the space (a till receipt).
    merchant_name: SafeStr | None = Field(default=None, max_length=MERCHANT_MAX_LENGTH)
    number: SafeStr | None = Field(default=None, max_length=NUMBER_MAX_LENGTH)
    date: dt.date
    received_on: dt.date | None = None
    # A day inside the IVA period; an invoice or a credit note only, defaulting to the day of
    # receipt, else the document's date.
    iva_period: dt.date | None = None
    description: SafeStr = Field(max_length=DESCRIPTION_MAX_LENGTH)
    # The total as the document states it, in euro, VAT included: negative on a credit note and
    # on a refund. In another currency, the euro equivalent, with the foreign total and the
    # rate beside it.
    amount: Money
    currency: Currency = "EUR"
    currency_amount: Money | None = None
    exchange_rate: Annotated[
        Decimal | None,
        Field(max_digits=EXCHANGE_RATE_MAX_DIGITS, decimal_places=EXCHANGE_RATE_SCALE, gt=0),
    ] = None
    exchange_rate_date: dt.date | None = None
    # The VAT recap: required on an invoice and a credit note, refused on a receipt and an
    # expense.
    vat_lines: list[VatLine] = Field(default_factory=list, max_length=50)
    stamp_duty: Money | None = None
    withholding_rate: Percent | None = None
    withholding_base: Money | None = None
    withholding_amount: Money | None = None
    fund_rate: Percent | None = None
    fund_amount: Money | None = None
    split_payment: bool = False
    reverse_charge: bool = False
    # The invoice a credit note corrects.
    corrects_purchase_id: UUID | None = None
    inbox_item_id: UUID | None = None
    document_id: UUID | None = None
    custom_fields: dict[str, Any] = {}


class PurchaseUpdate(BaseModel):
    """A partial update. `vat_lines`, when sent, replaces the whole recap; the frozen cost is
    recomputed whenever the amount, the recap or a flag changes."""

    model_config = ConfigDict(extra="forbid")

    kind: PurchaseKind | None = None
    deal_id: UUID | None = None
    legal_entity_id: UUID | None = None
    category_id: UUID | None = None
    supplier_id: UUID | None = None
    merchant_name: SafeStr | None = Field(default=None, max_length=MERCHANT_MAX_LENGTH)
    number: SafeStr | None = Field(default=None, max_length=NUMBER_MAX_LENGTH)
    date: dt.date | None = None
    received_on: dt.date | None = None
    iva_period: dt.date | None = None
    description: SafeStr | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    amount: Money | None = None
    currency: Currency | None = None
    currency_amount: Money | None = None
    exchange_rate: Annotated[
        Decimal | None,
        Field(max_digits=EXCHANGE_RATE_MAX_DIGITS, decimal_places=EXCHANGE_RATE_SCALE, gt=0),
    ] = None
    exchange_rate_date: dt.date | None = None
    vat_lines: list[VatLine] | None = Field(default=None, max_length=50)
    stamp_duty: Money | None = None
    withholding_rate: Percent | None = None
    withholding_base: Money | None = None
    withholding_amount: Money | None = None
    fund_rate: Percent | None = None
    fund_amount: Money | None = None
    split_payment: bool | None = None
    reverse_charge: bool | None = None
    corrects_purchase_id: UUID | None = None
    inbox_item_id: UUID | None = None
    document_id: UUID | None = None
    custom_fields: dict[str, Any] | None = None


class PurchaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: PurchaseKind
    status: PurchaseStatus
    deal_id: UUID | None
    legal_entity_id: UUID | None
    category_id: UUID
    supplier_id: UUID | None
    merchant_name: str | None
    number: str | None
    date: dt.date
    received_on: dt.date | None
    iva_period: dt.date | None
    description: str
    amount: Decimal
    currency: str
    currency_amount: Decimal | None
    exchange_rate: Decimal | None
    exchange_rate_date: dt.date | None
    # What the purchase costs, frozen from the buyer's pack when it was registered: the figure
    # the profit and loss reads. `amount` is the document's total.
    cost_amount: Decimal
    vat_lines: list[VatLineRead]
    stamp_duty: Decimal | None
    withholding_rate: Decimal | None
    withholding_base: Decimal | None
    withholding_amount: Decimal | None
    fund_rate: Decimal | None
    fund_amount: Decimal | None
    split_payment: bool
    reverse_charge: bool
    corrects_purchase_id: UUID | None
    inbox_item_id: UUID | None
    document_id: UUID | None
    buyer_pack_id: str | None
    buyer_pack_version: str | None
    buyer_regime_code: str | None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PurchaseListQuery(BaseModel):
    deal_id: UUID | None = None
    # One legal entity's purchases (REB-623): a shared purchase, `NULL`, appears only when none
    # is asked for, which is the view over every legal entity.
    legal_entity_id: UUID | None = None
    # `True` selects only general expenses (`deal_id IS NULL`). Needed because `deal_id=None`
    # already means "do not filter", and §7.4 gives general expenses a row of their own that
    # has to be selectable.
    only_general: bool = False
    category_id: UUID | None = None
    supplier_id: UUID | None = None
    kind: PurchaseKind | None = None
    status: PurchaseStatus | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    custom: dict[str, Any] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class PurchasePage(BaseModel):
    items: list[PurchaseRead]
    next_cursor: UUID | None
