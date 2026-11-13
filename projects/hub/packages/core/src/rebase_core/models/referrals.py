from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.models.identity import REFERRAL_CODE_LENGTH

# ---- referrals: an existing member's link to who they brought in (P-REB-44) -----------

REFERRAL_KINDS = ("freelancer", "company")
# A referral is `to_verify` from signup until the referred person proves they hold the
# address it was posted with (REB-658), and only then `verified`; `verified_via` says
# which proof: `login` (a magic-link login after the referral was recorded), `letter`
# (a referred freelancer's own signature of a letter) or `history` (a referral that
# already counted before this state existed, migration 0030).
REFERRAL_STATES = ("to_verify", "verified")
REWARD_STATES = ("to_confirm", "confirmed", "paid")
# The base and the reward round to the cent, like every other amount this schema
# stores; a rate is a fraction, `0.10`/`0.30`, four places so a future rate like
# `12.5%` still fits exactly.
REWARD_AMOUNT_DIGITS, REWARD_AMOUNT_PLACES = 10, 2
RATE_DIGITS, RATE_PLACES = 5, 4


class Referral(Base, PrimaryKeyMixin, TimestampMixin):
    """One member's link to whichever freelancer card or company request their code
    brought in: `kind` says which, `entity_id` points at the `Freelancer` or `Company`
    row it names (no foreign key to either -- one column would have to point at two
    different tables). One referral per referred entity
    (`uq_referrals_kind_entity`): the second signup that ever arrives under the same
    code is that member's link working twice, not a second referral, and a company's
    later, additional request (`rebase_core.members.create_additional_request`) is not
    a fresh referral either -- rebase already knew that company. `referrer_user_id` is
    the code's owner read at signup time and kept even though a member's own code
    never changes, so a referral and its referrer can never drift apart under it.

    `status` is whether the referred person has proved they hold the address the
    referral was posted with (design record 2026-10-05, REB-658): the public routes do
    not verify it, so a referral is `to_verify` when it is made and counts (a
    reward is written, a ledger row is confirmable) only once `verified`.
    `verified_at` and `verified_via` are set together with it, and only then."""

    __tablename__ = "referrals"

    referrer_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(REFERRAL_CODE_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(
        String(14), nullable=False, default="to_verify", server_default="to_verify"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    verified_via: Mapped[str | None] = mapped_column(String(10), default=None)

    __table_args__ = (
        Index("uq_referrals_kind_entity", "kind", "entity_id", unique=True),
        Index("ix_referrals_referrer", "referrer_user_id", "created_at"),
        CheckConstraint("kind IN ('freelancer', 'company')", name="ck_referrals_kind"),
        CheckConstraint(
            "(status = 'to_verify' AND verified_at IS NULL AND verified_via IS NULL) "
            "OR (status = 'verified' AND verified_at IS NOT NULL "
            "AND verified_via IS NOT NULL "
            "AND verified_via IN ('login', 'letter', 'history'))",
            name="ck_referrals_verification",
        ),
    )


class ReferralReward(Base, PrimaryKeyMixin, TimestampMixin):
    """One reward, once per referral (`uq_referral_rewards_referral_id`): written the
    moment the referred entity's first `letter` is signed
    (`rebase_core.engagement_notices.EngagementNoticeService`, from the CRM's notice), with the
    rate and the base that letter's numbers printed at that moment, so a rate an admin edits later
    (`ReferralSettings`) never re-prices a reward already recorded -- the one-time
    maturity design record 2026-09-26 asks for. `base_amount` is rebase's own margin on
    that letter (`Company.daily_budget` against the letter's `fee`, never
    the freelancer's fee alone, which the contract flow never lets a freelancer's own
    document see either), projected over the engagement's expected days when the admin
    estimated one; `None` when there is no reliable figure to project (a `lump_sum`
    letter with no estimate), left for an admin to price by hand on the ledger.
    `reward_amount` is `None` exactly when `base_amount` is. `status` moves by hand
    only, `to_confirm` to `confirmed` to `paid`: nothing here pays anyone."""

    __tablename__ = "referral_rewards"

    referral_id: Mapped[UUID] = mapped_column(ForeignKey("referrals.id"), nullable=False)
    # The engagement whose first signed letter earned it (REB-1106): the hub's read model of the
    # CRM's engagement, upserted by its notices, not the hub's own letter any more.
    engagement_id: Mapped[UUID] = mapped_column(ForeignKey("hub_engagements.id"), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(RATE_DIGITS, RATE_PLACES), nullable=False)
    base_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(REWARD_AMOUNT_DIGITS, REWARD_AMOUNT_PLACES), default=None
    )
    reward_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(REWARD_AMOUNT_DIGITS, REWARD_AMOUNT_PLACES), default=None
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="to_confirm")
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        Index("uq_referral_rewards_referral_id", "referral_id", unique=True),
        CheckConstraint(
            "status IN ('to_confirm', 'confirmed', 'paid')",
            name="ck_referral_rewards_status",
        ),
        CheckConstraint(
            "(base_amount IS NULL) = (reward_amount IS NULL)",
            name="ck_referral_rewards_amount_together",
        ),
    )


class ReferralSettings(Base, PrimaryKeyMixin, TimestampMixin):
    """The two referral rates, one row (design record 2026-09-26: a database row an
    admin edits from the hub admin area, never an environment variable and a deploy).
    `id` is not constrained to a single value at the database, which has no clean way
    to say "exactly one row" short of a trigger; `ReferralService` always reads the
    oldest row and never inserts a second one, the same way this package leaves a
    handful of other "there is only ever one" facts to the service layer rather than
    to the schema."""

    __tablename__ = "referral_settings"

    rate_freelancer: Mapped[Decimal] = mapped_column(
        Numeric(RATE_DIGITS, RATE_PLACES), nullable=False, default=Decimal("0.10")
    )
    rate_company: Mapped[Decimal] = mapped_column(
        Numeric(RATE_DIGITS, RATE_PLACES), nullable=False, default=Decimal("0.30")
    )
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
