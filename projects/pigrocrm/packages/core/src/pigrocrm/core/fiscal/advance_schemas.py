"""The shapes of the advance method and of the tax due items' report (REB-1074, taxes design
§ 2 and § 5)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AdvanceMethod = Literal["historical", "forecast"]
# Which payment of a tax year a line is about.
TaxItemPart = Literal["balance", "first_advance", "second_advance", "chamber_fee"]


class TaxAdvanceMethodSet(BaseModel):
    """What an administrator chooses: the method of the advances of `year` (the tax year they
    belong to). `historical` is the previous year's declared tax, or its estimate when none is
    declared; `forecast` is the current year's estimate."""

    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=2000, le=2200)
    method: AdvanceMethod


class TaxItemNotWritten(BaseModel):
    """A payment of a tax year the due items do not carry, and why. `reason` is English text
    for the reader of the report, not product copy."""

    model_config = ConfigDict(frozen=True)

    tax: str
    year: int
    part: TaxItemPart
    reason: str


class TaxItemsReport(BaseModel):
    """What one recomputation of a legal entity's tax due items did: how many it wrote, dropped
    and left as they were (an item a payment touched is never changed), and every payment it
    could not write, with the missing figure or parameter."""

    written: int = 0
    dropped: int = 0
    kept: int = 0
    not_written: list[TaxItemNotWritten] = Field(default_factory=list)


class TaxAdvanceMethodRead(BaseModel):
    """The choice as stored, and what moving the tax due items to it left unwritten."""

    legal_entity_id: UUID
    year: int
    method: AdvanceMethod
    updated_at: datetime
    items: TaxItemsReport


__all__ = [
    "AdvanceMethod",
    "TaxAdvanceMethodRead",
    "TaxAdvanceMethodSet",
    "TaxItemNotWritten",
    "TaxItemPart",
    "TaxItemsReport",
]
