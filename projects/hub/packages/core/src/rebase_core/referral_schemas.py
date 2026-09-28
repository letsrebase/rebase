"""What the member area and the admin ledger read and write about referrals (P-REB-44).

Kept beside `contract_schemas.py`'s own split from `schemas.py`: a domain with more than
a couple of shapes gets its own file rather than growing the general one further.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.models import RATE_DIGITS, RATE_PLACES, REFERRAL_CODE_LENGTH

RATE_MAX = Decimal("1")


class MemberReferralItem(BaseModel):
    """One person or company the member's own code brought in: enough to recognise
    who it was and whether it has turned into anything yet, never the euro figures --
    those are rebase's own margin, an admin-only number nowhere in the member area."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    nome: str
    created_at: datetime
    stato: str | None


class MemberReferral(BaseModel):
    """`GET /api/hub/me/referral`: the member's own code, issued the first time this
    route is asked for one, and who it has brought in so far."""

    code: str
    referred: list[MemberReferralItem]


class ReferralLedgerItem(BaseModel):
    """One row of the admin's referral ledger: the referrer, who was referred, and the
    reward as it was computed or as an admin priced it by hand. `reward_id` is `None`
    until the referred party's first letter is signed -- a referral is on this ledger
    from the moment it is made, not only once a reward exists for it (P-REB-44:
    hiding a referral until money is owed makes the ledger say there are none when
    some are simply still open). `referral_id` is the row's stable identity;
    `reward_id` is what the state and price actions below key on, and is `None` when
    there is nothing yet to confirm or price."""

    model_config = ConfigDict(from_attributes=True)

    referral_id: UUID
    reward_id: UUID | None
    kind: str
    referrer_nome: str
    referrer_email: str
    referred_nome: str
    match_id: UUID | None
    rate: Decimal | None
    base_amount: Decimal | None
    reward_amount: Decimal | None
    stato: str | None
    note: str | None
    created_at: datetime
    confirmed_at: datetime | None
    paid_at: datetime | None


class ReferralLedgerList(BaseModel):
    items: list[ReferralLedgerItem]
    next_cursor: str | None


class ReferralRewardStateChange(BaseModel):
    """`POST /api/hub/admin/referrals/{id}/state`: the one-way ladder
    `da_confermare` -> `confermato` -> `pagato`, never backwards -- see
    `ReferralService.set_state`."""

    model_config = ConfigDict(extra="forbid")

    stato: str


class ReferralRewardPrice(BaseModel):
    """`POST /api/hub/admin/referrals/{id}/price`: what an admin fills in by hand for a
    reward `record_reward_if_signed` left with no figure (an `a corpo` letter with no
    `giorni_previsti` estimate, design record 2026-09-26)."""

    model_config = ConfigDict(extra="forbid")

    base_amount: Decimal = Field(max_digits=10, decimal_places=2, ge=0)
    reward_amount: Decimal = Field(max_digits=10, decimal_places=2, ge=0)


class ReferralSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rate_freelancer: Decimal
    rate_company: Decimal
    updated_at: datetime
    updated_by_nome: str | None


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
# the one constant a `rif` field's own validation already bounds itself to.
__all__ = [
    "MemberReferral",
    "MemberReferralItem",
    "REFERRAL_CODE_LENGTH",
    "ReferralLedgerItem",
    "ReferralLedgerList",
    "ReferralRewardPrice",
    "ReferralRewardStateChange",
    "ReferralSettingsRead",
    "ReferralSettingsUpdate",
]
