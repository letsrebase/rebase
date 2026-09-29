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
    who it was and whether it has turned into anything yet. Never a euro figure of a
    real engagement: rebase's own margin on it is an admin-only number."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    nome: str
    created_at: datetime
    stato: str | None


class MemberReferral(BaseModel):
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


class MatchReferral(BaseModel):
    """One referred side of a match, as the admin's «Match» list reads it (REB-609).
    `stato` is a reward's own state once one exists for this referral and its letter
    belongs to this match (`da_confermare`, `confermato`, `pagato`, with the real `rate`
    and `amount`); `previsto` while no reward exists yet, with the projection the current
    rate and the match's own numbers give (`amount` is `None` when there is nothing to
    project: no base, a cancelled match, one whose letter is already out of the
    running); `gia_maturato` when the referral already paid on another match, since a
    referral pays once -- no figure then. `referrer_nome` is the referrer's whole name,
    and `referrer_freelancer_id` his own card, when he has one, so a page can link to
    it."""

    kind: str
    referrer_nome: str
    referrer_freelancer_id: UUID | None
    rate: Decimal | None
    amount: Decimal | None
    stato: str
    reward_id: UUID | None


class ReferralLedgerItem(BaseModel):
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
    The `match_*` fields name the match that earned the reward or, for a referral with
    none yet, the referred side's newest match that is not cancelled -- `None` when
    there is neither. `projected_rate` and `projected_amount` are that match's
    projection for a referral without a reward: an estimate, never replacing a real
    figure, and `None` when there is nothing to project. `referrer_nome` is the
    referrer's whole name, first and last. `referred_deleted` and
    `match_freelancer_deleted` say the referred card or request, or the match's
    freelancer, was deleted by an admin: its page answers not found, so a page must not
    link to it."""

    model_config = ConfigDict(from_attributes=True)

    referral_id: UUID
    reward_id: UUID | None
    kind: str
    referred_id: UUID
    referrer_nome: str
    referrer_email: str
    referrer_freelancer_id: UUID | None
    referred_nome: str
    referred_deleted: bool
    match_id: UUID | None
    match_freelancer_id: UUID | None
    match_freelancer_nome: str | None
    match_freelancer_deleted: bool
    match_nome_azienda: str | None
    match_figura_richiesta: str | None
    projected_rate: Decimal | None
    projected_amount: Decimal | None
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
    "MatchReferral",
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
