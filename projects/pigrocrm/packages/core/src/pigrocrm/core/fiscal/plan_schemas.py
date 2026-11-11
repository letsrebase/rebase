"""The shapes of the tax payment plans (REB-1075, taxes design § 2): the instalment plan an
administrator chooses for the June balance and first advance, and the plan of a notice from the tax
office entered by hand."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pigrocrm.core.fiscal.advance_schemas import TaxItemsReport
from pigrocrm.core.fiscal.law import DeclaredTax
from pigrocrm.core.fiscal.models import MAX_PLAN_INSTALMENTS
from pigrocrm.core.validation import SafeStr

# The taxes a June payment is split for: the income taxes, never INPS (its own dates and split).
IncomeTax = Literal["irpef", "substitute_tax", "ires", "irap"]
# A notice's amounts are euro with cents, up to the due item's own limit.
MAX_DIGITS = 12
MAX_NOTICE_INSTALMENTS = 120


class TaxInstalmentPlanSet(BaseModel):
    """What an administrator chooses: in how many monthly instalments the June balance of tax
    year `year` and the first advance of `year + 1` are paid (they fall on the same day). `tax`
    names one income tax, or leaves every income tax of the year's law together. One instalment
    is a single payment and removes the plan."""

    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=2000, le=2200)
    tax: IncomeTax | None = None
    instalments: int = Field(ge=1, le=MAX_PLAN_INSTALMENTS)


class TaxInstalmentPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    legal_entity_id: UUID
    tax: str
    tax_year: int
    instalments: int
    updated_at: datetime


class TaxInstalmentPlanResult(BaseModel):
    """The plans of the legal entity as they stand after the choice (the ones of the year the
    choice was about), and what moving the tax due items to them left unwritten."""

    legal_entity_id: UUID
    year: int
    plans: list[TaxInstalmentPlanRead]
    items: TaxItemsReport


class NoticeInstalmentSet(BaseModel):
    """One instalment as the notice states it: the day it is due and the interest it carries, in
    euro."""

    model_config = ConfigDict(extra="forbid")

    due_date: dt.date
    interest: Decimal = Field(
        default=Decimal("0.00"), ge=0, max_digits=MAX_DIGITS, decimal_places=2
    )


class TaxNoticePlanSet(BaseModel):
    """A notice from the tax office, as an administrator types it: what it is, the tax, the total
    it asks before interest and its instalments in order. The principal is split equally over
    them; each instalment adds its own interest."""

    model_config = ConfigDict(extra="forbid")

    label: SafeStr = Field(min_length=1, max_length=120)
    tax: DeclaredTax
    total: Decimal = Field(gt=0, max_digits=MAX_DIGITS, decimal_places=2)
    instalments: list[NoticeInstalmentSet] = Field(min_length=1, max_length=MAX_NOTICE_INSTALMENTS)

    @field_validator("label")
    @classmethod
    def _label_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("indica di che avviso si tratta")
        return value.strip()

    @model_validator(mode="after")
    def _dates_in_order(self) -> TaxNoticePlanSet:
        dates = [i.due_date for i in self.instalments]
        if any(later <= earlier for earlier, later in zip(dates, dates[1:], strict=False)):
            raise ValueError("le rate sono in ordine di scadenza, una per data")
        return self


class TaxNoticePlanUpdate(BaseModel):
    """Changes to a notice plan: only what is passed changes, and `instalments`, when passed,
    replaces the whole list."""

    model_config = ConfigDict(extra="forbid")

    label: SafeStr | None = Field(default=None, min_length=1, max_length=120)
    tax: DeclaredTax | None = None
    total: Decimal | None = Field(default=None, gt=0, max_digits=MAX_DIGITS, decimal_places=2)
    instalments: list[NoticeInstalmentSet] | None = Field(
        default=None, min_length=1, max_length=MAX_NOTICE_INSTALMENTS
    )

    @field_validator("label")
    @classmethod
    def _label_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("indica di che avviso si tratta")
        return value.strip()


class NoticeInstalmentRead(BaseModel):
    number: int
    due_date: dt.date
    principal: Decimal
    interest: Decimal
    amount: Decimal


class TaxNoticePlanRead(BaseModel):
    id: UUID
    legal_entity_id: UUID
    label: str
    tax: str
    total: Decimal
    instalments: list[NoticeInstalmentRead]
    created_at: datetime
    updated_at: datetime


class TaxPlansRead(BaseModel):
    """Every plan of one legal entity."""

    legal_entity_id: UUID
    instalment_plans: list[TaxInstalmentPlanRead]
    notice_plans: list[TaxNoticePlanRead]


__all__ = [
    "IncomeTax",
    "NoticeInstalmentRead",
    "NoticeInstalmentSet",
    "TaxInstalmentPlanRead",
    "TaxInstalmentPlanResult",
    "TaxInstalmentPlanSet",
    "TaxNoticePlanRead",
    "TaxNoticePlanSet",
    "TaxNoticePlanUpdate",
    "TaxPlansRead",
]
