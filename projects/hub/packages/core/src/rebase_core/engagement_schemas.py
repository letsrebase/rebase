"""What the CRM sends the hub about its engagements, and what the hub answers (REB-1106,
collaborators design § 5). Request bodies are plain `BaseModel`s; the answers derive from
`ResponseModel`."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.schema_common import ResponseModel

NoticeKind = Literal["created", "updated", "sent", "signed", "cancelled", "closed", "notice_served"]
NoticeState = Literal["draft", "awaiting_signature", "active", "concluded", "cancelled"]
NoticeMode = Literal["day", "hour", "flat"]


class EngagementNotice(BaseModel):
    """One notice, the whole read model as it was at the change: the CRM's engagement id, the
    match id the hub minted for it when it did (`hub_match_id`), the hub's freelancer and company
    ids, the letter, `signed_at`, the fee and the dates. `sequence` is the engagement's own count
    from 1; a notice not newer than the one the hub holds is ignored. Extra keys are ignored so
    the CRM may grow its notice without breaking the hub."""

    model_config = ConfigDict(extra="ignore")

    sequence: int = Field(ge=1)
    kind: NoticeKind
    occurred_at: datetime | None = None
    engagement_id: UUID
    hub_match_id: UUID | None = None
    hub_freelancer_id: UUID
    hub_company_id: UUID | None = None
    client_name: str | None = Field(default=None, max_length=200)
    letter_id: UUID | None = None
    letter_number: str | None = Field(default=None, max_length=12)
    signed_at: datetime | None = None
    mode: NoticeMode
    fee: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    expected_days: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    role: str = Field(max_length=200)
    starts_on: date
    ends_on: date | None = None
    state: NoticeState


class NoticeAck(ResponseModel):
    """`applied` is false for a notice the hub already held (the same sequence or a newer one):
    a retry, which the CRM counts as delivered."""

    applied: bool


class ReferrerQuestion(BaseModel):
    """Who referred this freelancer and this company: either id may be absent."""

    model_config = ConfigDict(extra="ignore")

    hub_freelancer_id: UUID | None = None
    hub_company_id: UUID | None = None


class ReferrerAnswer(ResponseModel):
    """The name of each side's referrer, `None` for a side with no referrer and for one whose
    referral is not verified (`ReferralService.referrer_name`)."""

    freelancer: str | None
    company: str | None
