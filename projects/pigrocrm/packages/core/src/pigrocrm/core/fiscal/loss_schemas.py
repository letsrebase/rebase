"""The shapes of a carried tax loss (REB-1088): what an administrator types, what is stored, and
what an estimate says it did with the losses of earlier years (`fiscal/loss_carry.py`)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The taxes a loss is carried against: an srl's IRES (art. 84 TUIR) and an ordinary person's
# IRPEF on a business loss (art. 8 c. 3 TUIR). IRAP carries no loss, and neither does the
# flat-rate regime, whose income is the revenue times a coefficient.
LossTax = Literal["ires", "irpef"]
LOSS_TAXES: tuple[LossTax, ...] = get_args(LossTax)

# How a typed loss came to be; a loss read from a return and accepted by a person is a later card.
LossSource = Literal["typed"]


class TaxLossSet(BaseModel):
    """What an administrator types: the loss a tax period closed with, as the return states it.

    `amount` is the loss of `year` (positive). `remaining`, with `remaining_as_of_year`, is the
    part still to carry at the end of that later year, when the returns since have used some;
    leave both out for a loss not used yet, and the estimate rolls it forward year by year.
    `new_activity` and `activity_start_year` are the two facts art. 84 c. 2 TUIR needs for the
    100% limit of the first three periods: the year the company (or the activity) started and
    that the loss relates to a new productive activity. Typing the same tax and year again
    replaces the row whole."""

    model_config = ConfigDict(extra="forbid")

    tax: LossTax
    year: int = Field(ge=2000, le=2200)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    remaining: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    remaining_as_of_year: int | None = Field(default=None, ge=2000, le=2200)
    new_activity: bool = False
    activity_start_year: int | None = Field(default=None, ge=1900, le=2200)

    @model_validator(mode="after")
    def _consistent(self) -> TaxLossSet:
        if (self.remaining is None) != (self.remaining_as_of_year is None):
            raise ValueError("il residuo e l'anno a cui si riferisce vanno indicati insieme")
        if self.remaining is not None and self.remaining > self.amount:
            raise ValueError("il residuo non può superare la perdita")
        if self.remaining_as_of_year is not None and self.remaining_as_of_year < self.year:
            raise ValueError("il residuo si riferisce a un anno successivo alla perdita")
        if self.new_activity and self.activity_start_year is None:
            raise ValueError(
                "per i primi tre periodi di una nuova attività indica l'anno di costituzione"
            )
        if self.activity_start_year is not None and self.activity_start_year > self.year:
            raise ValueError("l'attività non può iniziare dopo l'anno della perdita")
        return self


class TaxLossRead(BaseModel):
    """A typed loss as stored. `remaining` is what was left at the end of `remaining_as_of_year`
    (the loss itself and its own year when nothing was used)."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    legal_entity_id: UUID
    tax: LossTax
    year: int = Field(validation_alias="origin_year")
    amount: Decimal
    remaining: Decimal
    remaining_as_of_year: int
    new_activity: bool
    activity_start_year: int | None
    source: str
    updated_at: datetime


class LossUse(BaseModel):
    """What one year's loss did in the estimated year: `available` was left to carry at its start,
    `used` reduced the income (within `limit_percent` of it) and `remaining` goes on to the next
    year. `estimated` is true when the loss is an earlier year's own estimate, not a typed one."""

    model_config = ConfigDict(frozen=True)

    origin_year: int
    estimated: bool
    limit_percent: Decimal
    available: Decimal
    used: Decimal
    remaining: Decimal


class LossOffset(BaseModel):
    """What the losses of earlier years did to one tax's income for one year. `income` is the
    year's income before them and `used` what they took off it, `remaining` what all of them still
    carry afterwards. `estimated` is true when any of them is an earlier year's estimate rather
    than a typed loss. When a figure the offset needs is not known `missing` says which, and the
    tax has no figure: the loss is never taken as used up or as untouched on a guess."""

    model_config = ConfigDict(frozen=True)

    tax: LossTax
    year: int
    income: Decimal
    used: Decimal
    remaining: Decimal
    estimated: bool
    origins: list[LossUse]
    missing: list[str]
    assumptions: list[str]
    sources: list[str]


__all__ = [
    "LOSS_TAXES",
    "LossOffset",
    "LossSource",
    "LossTax",
    "LossUse",
    "TaxLossRead",
    "TaxLossSet",
]
