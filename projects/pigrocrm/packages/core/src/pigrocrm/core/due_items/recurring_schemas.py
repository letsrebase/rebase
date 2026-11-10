"""The shapes of a recurring expense (spec 2026-11-09, due items, § 1.2)."""

import datetime as dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from pigrocrm.core.due_items.models import TEXT_MAX_LENGTH
from pigrocrm.core.due_items.schemas import AMOUNT_MAX_DIGITS, MONEY_SCALE
from pigrocrm.core.validation import SafeStr

Cadence = Literal["monthly", "bimonthly", "quarterly", "semiannual", "annual"]


class RecurringExpenseCreate(BaseModel):
    """A supplier that bills `amount` (the total a payment carries, VAT included, in euro) every
    `cadence`, the first time on `first_due_date`. `ends_on` is the last due date, when the
    contract has an end. With several companies `legal_entity_id` says whose expense it is."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUID
    category_id: UUID
    description: SafeStr = Field(min_length=1, max_length=TEXT_MAX_LENGTH)
    amount: Decimal = Field(gt=0, max_digits=AMOUNT_MAX_DIGITS, decimal_places=MONEY_SCALE)
    cadence: Cadence
    first_due_date: dt.date
    ends_on: dt.date | None = None
    legal_entity_id: UUID | None = None

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> "RecurringExpenseCreate":
        if self.ends_on is not None and self.ends_on < self.first_due_date:
            raise ValueError("l'ultima scadenza non puo' precedere la prima")
        return self


class RecurringExpenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    legal_entity_id: UUID
    supplier_id: UUID
    category_id: UUID
    description: str
    amount: Decimal
    cadence: Cadence
    first_due_date: dt.date
    ends_on: dt.date | None
    stopped_on: dt.date | None
    created_at: dt.datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        return self.stopped_on is None
