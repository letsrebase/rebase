"""What the collaborators module reads and writes (design § 1 and § 3).

The values that come from a closed set (`state`, `fee_unit`, `letter_type`, `payment_rule`) are
plain strings here and checked by the service, which answers in Italian and names the field,
where a `Literal` would answer with pydantic's own error shape.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.collaborators.models import ROLE_MAX_LENGTH
from pigrocrm.core.validation import SafeStr

KIND_MAX_LENGTH = 20

WarningCode = Literal["overlap", "over_capacity"]
AvailabilityStatus = Literal["free", "committed", "unknown"]

# The longest window one availability read covers: two years of Mondays.
AVAILABILITY_MAX_WEEKS = 104
AVAILABILITY_DEFAULT_WEEKS = 12


class EngagementCreate(BaseModel):
    """A new engagement is a draft. `days_per_week` is required (the service says so in words);
    only an imported one may lack it. `letter_type` omitted follows the freelancer's fiscal kind:
    `occasional` for an occasional freelancer, `ordinary` otherwise."""

    model_config = ConfigDict(extra="forbid")

    freelancer_id: UUID
    hub_match_id: UUID | None = None
    client_organization_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    role: SafeStr = Field(max_length=ROLE_MAX_LENGTH)
    starts_on: dt.date
    ends_on: dt.date | None = None
    days_per_week: Decimal | None = Field(
        default=None, description="From 0.5 to 5 days, in steps of 0.5; required"
    )
    fee: Decimal
    fee_unit: SafeStr = Field(max_length=KIND_MAX_LENGTH, description="day | hour | flat")
    expected_days: Decimal | None = Field(
        default=None, description="For an hourly fee, the hours divided by 8"
    )
    letter_type: SafeStr | None = Field(
        default=None, max_length=KIND_MAX_LENGTH, description="ordinary | occasional"
    )
    payment_rule: SafeStr = Field(
        max_length=KIND_MAX_LENGTH,
        description="fixed_term (payment_days from the invoice) | on_collection (collection_days)",
    )
    payment_days: int | None = None
    payment_end_of_month: bool = False
    collection_days: int | None = None


class EngagementUpdate(BaseModel):
    """A draft can change any of these. Once it is out for signature or signed only
    `days_per_week` and the client, deal and contract links can change: they are what an
    imported engagement lacks, and none of them is printed on the letter."""

    model_config = ConfigDict(extra="forbid")

    client_organization_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    role: SafeStr | None = Field(default=None, max_length=ROLE_MAX_LENGTH)
    starts_on: dt.date | None = None
    ends_on: dt.date | None = None
    days_per_week: Decimal | None = None
    fee: Decimal | None = None
    fee_unit: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    expected_days: Decimal | None = None
    letter_type: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    payment_rule: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    payment_days: int | None = None
    payment_end_of_month: bool | None = None
    collection_days: int | None = None


class EngagementWarning(BaseModel):
    """Something to know, not a refusal: the engagement is saved either way."""

    code: WarningCode
    message: str
    engagement_ids: list[UUID]
    # The most days a week the freelancer is committed to in the overlap, this engagement
    # included, or `None` when one of the others has no `days_per_week`.
    peak_days_per_week: Decimal | None


class EngagementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    hub_match_id: UUID | None
    freelancer_id: UUID
    client_organization_id: UUID | None
    deal_id: UUID | None
    contract_id: UUID | None
    role: str
    starts_on: dt.date
    ends_on: dt.date | None
    days_per_week: Decimal | None
    fee: Decimal
    fee_unit: str
    expected_days: Decimal | None
    letter_type: str
    payment_rule: str
    payment_days: int | None
    payment_end_of_month: bool
    collection_days: int | None
    state: str
    # What an admin still has to fill in: `days_per_week` and `client`.
    missing: list[str]
    # Filled on a create, an update and a get of a live engagement.
    warnings: list[EngagementWarning] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EngagementListQuery(BaseModel):
    freelancer_id: UUID | None = None
    state: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    client_organization_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class EngagementPage(BaseModel):
    items: list[EngagementRead]
    next_cursor: UUID | None


class EngagementClose(BaseModel):
    """Closing an engagement ends it on `ended_on` (today when omitted): an open-ended or
    longer engagement stops there, so the weeks after no longer count as committed."""

    model_config = ConfigDict(extra="forbid")

    ended_on: dt.date | None = None


class EngagementStateChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: SafeStr = Field(max_length=KIND_MAX_LENGTH)
    ended_on: dt.date | None = None


class AvailabilityWeek(BaseModel):
    """One ISO week. `unknown` when an active engagement overlapping it has no
    `days_per_week`: never `free`. `committed_days` then counts only the ones that have it."""

    week: str = Field(description="ISO week, 2026-W46")
    starts_on: dt.date
    ends_on: dt.date
    status: AvailabilityStatus
    label: str
    committed_days: Decimal
    free_days: Decimal | None
    engagement_ids: list[UUID]


class FreelancerAvailability(BaseModel):
    freelancer_id: UUID
    hub_freelancer_id: UUID | None
    date_from: dt.date
    date_to: dt.date
    weeks: list[AvailabilityWeek]


class FreelancerTaxData(BaseModel):
    """The tax data the contracts print, and the fiscal kind (design § 1). Every field omitted
    is left as it is."""

    model_config = ConfigDict(extra="forbid")

    tax_code: SafeStr | None = None
    vat_number: SafeStr | None = None
    domicile: SafeStr | None = Field(default=None, description="Street address")
    certified_email: SafeStr | None = None
    fiscal_kind: SafeStr | None = Field(
        default=None,
        max_length=KIND_MAX_LENGTH,
        description="professional | flat_rate | occasional",
    )
