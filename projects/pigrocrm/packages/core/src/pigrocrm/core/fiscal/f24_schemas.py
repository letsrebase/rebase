"""The shapes of an F24 group and of a tax credit's origin (REB-1076, taxes design § 2).

Every amount is the server's: the group's total is the net of its lines, the sum of what is owed
less the credits, which is also what the one bank movement that pays it must equal. A group whose
total is zero or negative has no movement to match: a person files it (`F24GroupFile`).
"""

import datetime as dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pigrocrm.core.validation import SafeStr

CreditOrigin = Literal["iva_carry_forward", "withholding_suffered"]
F24GroupState = Literal["open", "settled", "dissolved"]
# What settled a group: the bank movement equal to its net, or a person marking it filed.
F24SettledBy = Literal["movement", "filing"]
F24CodeKind = Literal["tax_code", "inps_causale"]
# What a line says when the pack carries no code for it: never a guess (product copy).
CODE_TO_VERIFY = "codice da verificare"
GROUP_ITEMS_MAX = 40
NOTE_MAX_LENGTH = 255


class F24GroupCreate(BaseModel):
    """The due items to pay with one F24. They must fall on the same date, be owed to the tax
    office and be open with nothing paid on them; credits are among them."""

    model_config = ConfigDict(extra="forbid")

    due_item_ids: list[UUID] = Field(min_length=1, max_length=GROUP_ITEMS_MAX)
    note: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH)

    @field_validator("due_item_ids")
    @classmethod
    def _distinct(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("una scadenza e' indicata due volte")
        return value


class F24GroupFile(BaseModel):
    """A person marks an F24 with no amount to pay as filed: the day it was filed with the tax
    office's telematic services (not in the future) and, optionally, the receipt, an inbox item of
    kind `tax_document`."""

    model_config = ConfigDict(extra="forbid")

    filed_on: dt.date
    receipt_inbox_item_id: UUID | None = None


class F24ReceiptAttach(BaseModel):
    """An inbox item of kind `tax_document` that is a receipt of the group."""

    model_config = ConfigDict(extra="forbid")

    inbox_item_id: UUID


class TaxCreditOriginRead(BaseModel):
    """Where a credit comes from: `iva_carry_forward` names the IVA settlement that left it,
    `withholding_suffered` the income-tax year whose collected invoices carried the withholding."""

    origin: CreditOrigin
    tax: str
    tax_year: int
    iva_settlement_id: UUID | None


class F24LineRead(BaseModel):
    """One line of an F24. `amount` is signed like the due item (a credit is negative). The code
    is the pack's, frozen when the line was grouped; without one `code` is null and `code_label`
    reads «codice da verificare»: the pack does not carry it and none is guessed. `line` is the
    pack's name of the line when the item is one the pack codes. `used_amount` is, once the group
    is settled, what the F24 took of the line with its sign: all of a debit, and of a credit the
    part that covered the debits (a credit it left unspent in part or whole is `released`, its rest
    free for another F24)."""

    due_item_id: UUID
    source_kind: str
    tax: str | None
    description: str | None
    amount: Decimal
    is_credit: bool
    credit_origin: TaxCreditOriginRead | None
    line: str | None
    code: str | None
    code_kind: F24CodeKind | None
    code_label: str
    code_to_verify: bool
    reference_year: int | None
    reference_period: str | None
    released: bool
    used_amount: Decimal | None


class F24ReceiptRead(BaseModel):
    id: UUID
    inbox_item_id: UUID
    original_name: str | None
    attached_at: dt.datetime


class F24GroupRead(BaseModel):
    """An F24 group. `total` is the net the one movement must equal (`debit_total` less
    `credit_total`); `lines` are the live ones of an `open` or `settled` group and the released
    ones of a `dissolved` one. A settled group says by what (`settled_by`) and on which day
    (`settled_on`); one that was filed has no movement, and `credit_left` is what of its credits
    the F24 did not use, still available (null until the group is settled)."""

    id: UUID
    legal_entity_id: UUID
    payment_date: dt.date
    state: F24GroupState
    note: str | None
    # The header box «non coincidente con anno solare»: the legal entity's fiscal year is not the
    # calendar year, so whoever fills in the F24 ticks it and writes the first of the two
    # calendar years the period touches in «anno di riferimento» (Agenzia delle Entrate,
    # «Avvertenze per la compilazione del mod. F24»).
    fiscal_year_not_calendar_year: bool
    total: Decimal
    debit_total: Decimal
    credit_total: Decimal
    lines: list[F24LineRead]
    receipts: list[F24ReceiptRead]
    bank_movement_id: UUID | None
    settled_on: dt.date | None
    settled_by: F24SettledBy | None
    credit_left: Decimal | None
    created_by_type: str
    created_at: dt.datetime
    updated_at: dt.datetime


class F24GroupListQuery(BaseModel):
    """`state` omitted answers the open groups."""

    state: F24GroupState | None = None
    legal_entity_id: UUID | None = None
    due_from: dt.date | None = None
    due_to: dt.date | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class F24GroupPage(BaseModel):
    items: list[F24GroupRead]
    next_cursor: UUID | None


__all__ = [
    "CODE_TO_VERIFY",
    "CreditOrigin",
    "F24CodeKind",
    "F24GroupCreate",
    "F24GroupFile",
    "F24GroupListQuery",
    "F24GroupPage",
    "F24GroupRead",
    "F24GroupState",
    "F24LineRead",
    "F24ReceiptAttach",
    "F24ReceiptRead",
    "F24SettledBy",
    "TaxCreditOriginRead",
]
