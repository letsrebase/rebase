"""What a contract's page reads to bill its days, and what it sends to do it (REB-751).

Kept apart from `schemas.py`, which is the day ledger's own: this is the invoicing side of
it, and the two grow for different reasons.
"""

from datetime import date
from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class BillableLine(BaseModel):
    """One line the draft would carry: the days of one rate card in one month, priced at
    that card, exactly as `unbilled_work_unit_lines` builds it."""

    month: str = Field(description="AAAA-MM")
    description: str
    work_units: int = Field(description="Quante giornate copre la riga")
    quantity: Decimal
    unit_of_measure: str
    unit_price: Decimal
    amount: Decimal


class BillableDays(BaseModel):
    """The days of a contract that are ready to invoice, as the lines they would make.

    `without_rate` lists the dates no rate card covers: the draft is refused while there
    is one (inventing a price is not this step's decision), and the page says which days to
    fix. `without_approval` counts the days worked without the approval the contract
    requires, which are never gathered: they are the reason a month can look short.
    """

    contract_id: UUID
    currency: str
    lines: list[BillableLine]
    months: list[str] = Field(description="I mesi AAAA-MM che hanno giornate pronte")
    work_units: int
    quantity: Decimal
    taxable_amount: Decimal
    without_rate: list[date]
    without_approval: int
    block_reason: str | None = Field(
        default=None,
        description="Perché la bozza non si può creare da qui, quando non è questione di tariffe",
    )
    billable: bool = Field(description="C'è almeno una riga e ogni giornata ha la sua tariffa")


class InvoiceDaysRequest(BaseModel):
    """Which days to put on the draft: every ready day, or those in `[da, a]`, both
    inclusive. A month is `da` the first and `a` the last."""

    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_to < self.date_from
        ):
            raise ValueError("a non può precedere da")
        return self
