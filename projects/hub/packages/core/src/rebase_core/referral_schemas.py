"""What the member area and the admin ledger read and write about referrals (P-REB-44).

Kept out of the family schema modules: a domain with more than a couple of shapes gets its
own file rather than growing the general one further.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.models import (
    RATE_DIGITS,
    RATE_PLACES,
    REFERRAL_CODE_LENGTH,
)
from rebase_core.schema_common import ResponseModel
from rebase_core.vocabulary import ReferralKind, ReferralState, ReferredStatus, RewardState, enum_of

RATE_MAX = Decimal("1")

# `verified_via` of a referral: the proof that made it `verified` (`models.referrals`).
VERIFIED_VIA = ("login", "letter", "history")
VerifiedVia = Annotated[str, Field(json_schema_extra=enum_of(VERIFIED_VIA))]


class MemberReferralItem(ResponseModel):
    """One person or company the member's own code brought in: enough to recognise
    who it was and whether it has turned into anything yet. Never a euro figure of a
    real engagement: rebase's own margin on it is an admin-only number."""

    model_config = ConfigDict(from_attributes=True)

    kind: ReferralKind
    first_name: str
    created_at: datetime
    status: ReferredStatus | None


class MemberReferral(ResponseModel):
    """`GET /api/hub/me/referral`: the member's own code, issued the first time this
    route is asked for one, who it has brought in so far, and the two rates a referral
    earns (REB-610). The rates are read from the admin's settings on every call, so the
    member area states what is in force now; they are percentages of rebase's margin,
    which the member area may show, unlike any euro figure of a real engagement, which
    it never does (the panel's own worked example uses invented numbers)."""

    code: str
    rate_freelancer: Decimal
    rate_company: Decimal
    referred: list[MemberReferralItem]


class PublicReferralRates(ResponseModel):
    """`GET /api/hub/referral-rates`: the two percentages of rebase's margin a referral
    earns, as the admin's settings row holds them right now, and nothing else. This is
    the one referral shape a stranger reads, so it is closed on purpose: no code, no
    count, no margin, no euro figure of any engagement. Both fields are `Decimal`, which
    JSON carries as a string with the column's four places (`"0.1000"` is 10%), exactly as
    `MemberReferral` does: the site multiplies by 100 and drops the trailing zeros."""

    rate_freelancer: Decimal
    rate_company: Decimal


class ReferralEvidence(ResponseModel):
    """What an admin needs to judge one attribution (REB-657), all read from rows that
    already exist (`rebase_core.referral_evidence`): `code` is the `ref` the signup was
    made with, `signed_up_at` when the referred card or request was made, `utm_source`
    where that signup came from when one was recorded, `same_email_domain` whether the
    referred and referring addresses share the domain of an organisation (a shared
    public provider such as `gmail.com` never counts), and `ever_logged_in` whether the
    referred person has ever entered through a magic link. Both flags are `None` when
    the referred card or request was hard-deleted: nothing can be said, which is not
    the same as no."""

    code: str
    signed_up_at: datetime
    utm_source: str | None
    same_email_domain: bool | None
    ever_logged_in: bool | None


class ReferralLedgerItem(ResponseModel):
    """One row of the admin's referral ledger: the referrer, who was referred, and the
    reward as it was computed or as an admin priced it by hand. `reward_id` is `None`
    until the referred party's first letter is signed -- a referral is on this ledger
    from the moment it is made, not only once a reward exists for it (P-REB-44:
    hiding a referral until money is owed makes the ledger say there are none when
    some are simply still open). `referral_id` is the row's stable identity;
    `reward_id` is what the state and price actions below key on, and is `None` when
    there is nothing yet to confirm or price.

    REB-609: `referred_id` is the freelancer card's or the company's id (by `kind`) and
    `referrer_freelancer_id` the referrer's own card when he has one, both for links.
    The `engagement_*` fields name the engagement that earned the reward or, for a referral
    with none yet, the referred side's newest engagement that is not signed or cancelled
    yet -- `None` when there is neither (REB-1106: the hub's read model of the CRM's
    engagements). `projected_rate` and `projected_amount` are that engagement's
    projection for a referral without a reward: an estimate, never replacing a real
    figure, and `None` when there is nothing to project. `referrer_name` is the
    referrer's whole name, first and last. `referred_deleted` and
    `engagement_freelancer_deleted` say the referred card or request, or the engagement's
    freelancer, was deleted by an admin: its page answers not found, so a page must not
    link to it.

    REB-658: `referral_status` is whether the referred person is verified
    (`to_verify`, `verified`), `verified_at` and `verified_via` (`accesso`,
    `letter`, `storico`) when and by what; they are `None` while it is pending. A
    pending referral has no reward and nothing to confirm (`reward_id` and `status` are
    `None`), and a pending company has no `engagement_*` and no projection either, since
    nothing it could sign would earn anything until its contact logs in."""

    model_config = ConfigDict(from_attributes=True)

    referral_id: UUID
    reward_id: UUID | None
    kind: ReferralKind
    referred_id: UUID
    referrer_name: str
    referrer_email: str
    referrer_freelancer_id: UUID | None
    referred_name: str
    referred_deleted: bool
    engagement_id: UUID | None
    engagement_freelancer_id: UUID | None
    engagement_freelancer_name: str | None
    engagement_freelancer_deleted: bool
    engagement_company_name: str | None
    engagement_role: str | None
    projected_rate: Decimal | None
    projected_amount: Decimal | None
    rate: Decimal | None
    base_amount: Decimal | None
    reward_amount: Decimal | None
    status: RewardState | None
    referral_status: ReferralState
    verified_at: datetime | None
    verified_via: VerifiedVia | None
    note: str | None
    created_at: datetime
    confirmed_at: datetime | None
    paid_at: datetime | None
    evidence: ReferralEvidence


class ReferralLedgerList(ResponseModel):
    items: list[ReferralLedgerItem]
    next_cursor: str | None


class ReferralRewardStateChange(BaseModel):
    """`POST /api/hub/admin/referrals/{id}/state`: the one-way ladder
    `to_confirm` -> `confirmed` -> `paid`, never backwards -- see
    `ReferralService.set_state`."""

    model_config = ConfigDict(extra="forbid")

    status: str


class ReferralRewardPrice(BaseModel):
    """`POST /api/hub/admin/referrals/{id}/price`: what an admin fills in by hand for a
    reward `record_reward_if_signed` left with no figure (a `lump_sum` letter with no
    `expected_days` estimate, design record 2026-09-26)."""

    model_config = ConfigDict(extra="forbid")

    base_amount: Decimal = Field(max_digits=10, decimal_places=2, ge=0)
    reward_amount: Decimal = Field(max_digits=10, decimal_places=2, ge=0)


class ReferralSettingsRead(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    rate_freelancer: Decimal
    rate_company: Decimal
    updated_at: datetime
    updated_by_name: str | None


class ReferralSettingsUpdate(BaseModel):
    """`PUT /api/hub/admin/referral-settings`: both rates together, since a ledger
    open on stale numbers is a worse mistake than asking for the pair every time."""

    model_config = ConfigDict(extra="forbid")

    rate_freelancer: Decimal = Field(
        max_digits=RATE_DIGITS, decimal_places=RATE_PLACES, ge=0, le=RATE_MAX
    )
    rate_company: Decimal = Field(
        max_digits=RATE_DIGITS, decimal_places=RATE_PLACES, ge=0, le=RATE_MAX
    )


# Re-exported so a caller of this module never also needs `rebase_core.models` just for
# the one constant a `ref` field's own validation already bounds itself to.
__all__ = [
    "MemberReferral",
    "MemberReferralItem",
    "PublicReferralRates",
    "REFERRAL_CODE_LENGTH",
    "ReferralEvidence",
    "ReferralLedgerItem",
    "ReferralLedgerList",
    "ReferralRewardPrice",
    "ReferralRewardStateChange",
    "ReferralSettingsRead",
    "ReferralSettingsUpdate",
]
