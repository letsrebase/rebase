"""What a contract's recurring fee shows and takes when one occurrence is billed (REB-1032)."""

import datetime as dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.invoices.schemas import InvoiceRead

# Where an occurrence stands: nothing bills it yet, a draft invoice or a proforma bills it
# (not issued, so it can still be deleted and billed again), or an issued invoice does.
FeeState = Literal["to_bill", "drafted", "issued"]


class FeeBillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_card_id: UUID = Field(description="The recurring fee rate card of the contract to bill")
    period_start: dt.date = Field(
        description="The occurrence to bill, as the date `GET .../fee-occurrences` lists it"
    )


class FeeOccurrenceInvoice(BaseModel):
    """The document that bills an occurrence."""

    id: UUID
    type: str
    status: str
    year: int | None
    number: int | None
    reference: str | None


class FeeOccurrence(BaseModel):
    rate_card_id: UUID
    period_start: dt.date = Field(description="The day the occurrence falls on")
    period_end: dt.date | None = Field(
        description="The last day the fee covers: the day before the next occurrence, or the end "
        "of the card or the contract when that comes first. Empty for a one-off fee"
    )
    delivery_period: str = Field(description="`monthly`, `quarterly`, `annual` or `one_off`")
    amount: Decimal = Field(description="The fee of the rate card, before VAT")
    state: FeeState
    invoice: FeeOccurrenceInvoice | None = Field(
        description="The draft, proforma or issued invoice that bills it; empty when `to_bill`"
    )


class FeeOccurrences(BaseModel):
    contract_id: UUID
    contract_status: str
    currency: str
    billable: bool = Field(
        description="Whether an occurrence of this contract can be billed from here at all"
    )
    block_reason: str | None = Field(description="Why not, when `billable` is false")
    date_from: dt.date
    date_to: dt.date
    occurrences: list[FeeOccurrence]


class FeeBilled(BaseModel):
    created: bool = Field(
        description="True when this call made the draft, false when the occurrence was already "
        "on a draft or proforma, which is the one answered"
    )
    invoice: InvoiceRead
