from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.models.matches import LETTER_NUMBER_MAX_LENGTH
from rebase_core.models.profiles import COMPANY_MAX_LENGTH

# ---- the hub's read model of the CRM's engagements (REB-1106, collaborators design § 5) -------

# The CRM's own state names, and `handoff`: a row the hub opened at «Crea match» and the CRM has
# not told it about yet, which nothing reads.
ENGAGEMENT_STATES = ("handoff", "draft", "awaiting_signature", "active", "concluded", "cancelled")
# How the letter prices its fee: per day, per hour or a lump sum (the CRM's `fee_unit`).
ENGAGEMENT_MODES = ("day", "hour", "flat")
ENGAGEMENT_ROLE_MAX_LENGTH = 200


class HubEngagement(Base, PrimaryKeyMixin, TimestampMixin):
    """What the hub knows about an engagement the CRM holds, and nothing more: the CRM sends a
    notice at every change (`rebase_core.engagement_notices`) and this row is upserted from it.
    It exists so the hub's own readers (the referral reward and its ledger, the verification of a
    referral by a first signed letter, the card's engagement facts) need not open the CRM.

    `id` is the match id the hub minted at «Crea match» when the notice carries it (`hub_match_id`),
    otherwise the CRM's engagement id, and `crm_engagement_id` is the CRM's own. `sequence` is the
    notice's, and a notice that is not newer than it is ignored, so a retry or a late delivery
    cannot move the row back. `company_daily_budget` is the company's own day rate, snapshotted
    when the hub opened the row (never sent by the CRM, never on a letter): the reward at the first
    signature is computed from it, so a request edited later moves nothing. Every column but
    `id`, `freelancer_id`, `state` and `sequence` is empty on a `handoff` row."""

    __tablename__ = "hub_engagements"

    crm_engagement_id: Mapped[UUID | None] = mapped_column(default=None)
    freelancer_id: Mapped[UUID] = mapped_column(ForeignKey("freelancers.id"), nullable=False)
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"), default=None)
    # The client as the CRM names it, kept only to keep it out of the role a card prints.
    client_name: Mapped[str | None] = mapped_column(String(COMPANY_MAX_LENGTH), default=None)
    letter_id: Mapped[UUID | None] = mapped_column(default=None)
    letter_number: Mapped[str | None] = mapped_column(
        String(LETTER_NUMBER_MAX_LENGTH), default=None
    )
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    mode: Mapped[str | None] = mapped_column(String(5), default=None)
    fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), default=None)
    expected_days: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), default=None)
    role: Mapped[str | None] = mapped_column(String(ENGAGEMENT_ROLE_MAX_LENGTH), default=None)
    starts_on: Mapped[date | None] = mapped_column(Date, default=None)
    ends_on: Mapped[date | None] = mapped_column(Date, default=None)
    # The end the engagement was planned to have while it was live: a notice for a state other than
    # `concluded` sets it, so closing an engagement early (which the CRM records by moving `ends_on`
    # back to the day it closed) can still be told from one that ran to its end.
    planned_ends_on: Mapped[date | None] = mapped_column(Date, default=None)
    company_daily_budget: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="handoff")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("uq_hub_engagements_crm_engagement_id", "crm_engagement_id", unique=True),
        Index("ix_hub_engagements_freelancer", "freelancer_id", "starts_on"),
        Index("ix_hub_engagements_company", "company_id"),
        CheckConstraint(
            "state IN ('handoff', 'draft', 'awaiting_signature', 'active', 'concluded', "
            "'cancelled')",
            name="ck_hub_engagements_state",
        ),
        CheckConstraint(
            "mode IS NULL OR mode IN ('day', 'hour', 'flat')", name="ck_hub_engagements_mode"
        ),
        CheckConstraint("sequence >= 0", name="ck_hub_engagements_sequence"),
    )
