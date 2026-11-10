"""The engagement: what rebase holds about a freelancer working for one of its clients
(collaborators design `2026-11-09-collaborators-in-the-crm-design.md` § 1 and § 3).

An engagement is the CRM's name for the hub's match. It names the freelancer (an
organization with the role `freelancer`), the client it serves, the fee, how much of the
week it takes (`days_per_week`, what availability is computed from) and the rule that says
when the freelancer is paid. The payment rule's own arithmetic, the letter and the signature
are later cards; this table stores what they will read.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

# The hub's own state names (design § 1): a draft is edited, `awaiting_signature` is out for
# the freelancer to sign, `active` is signed and counts towards availability, `concluded` and
# `cancelled` are final.
ENGAGEMENT_STATES: tuple[str, ...] = (
    "draft",
    "awaiting_signature",
    "active",
    "concluded",
    "cancelled",
)
# The states in which an engagement still binds the freelancer's time: what a new engagement
# is warned against. Only `active` is a commitment availability shows.
LIVE_STATES: tuple[str, ...] = ("draft", "awaiting_signature", "active")

FEE_UNITS: tuple[str, ...] = ("day", "hour", "flat")
LETTER_TYPES: tuple[str, ...] = ("ordinary", "occasional")
PAYMENT_RULES: tuple[str, ...] = ("fixed_term", "on_collection")

# `days_per_week`: from half a day to five, in steps of half a day, the range the hub's team
# proposals already use.
DAYS_PER_WEEK_MIN = Decimal("0.5")
DAYS_PER_WEEK_MAX = Decimal("5")

# Law 81/2017 art. 3 c. 1: a clause agreeing a payment term past 60 days from the client's
# receiving the invoice or request for payment is abusive and void (verified on normattiva.it,
# 2026-11-10; frame A5). 30 days counted from the end of the month never passes it either: the
# longest case, an invoice of 30 January, lands on 31 March, 60 days on.
PAYMENT_DAYS_LIMIT = 60
PAYMENT_DAYS_LIMIT_MONTH_END = 30
COLLECTION_DAYS_LIMIT = 60

ROLE_MAX_LENGTH = 200


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class Engagement(Base, PrimaryKeyMixin, TimestampMixin):
    """One freelancer working for one client.

    `days_per_week` is null only on an engagement the hub's match never carried it for (the
    import leaves it until an admin fills it): availability then answers «sconosciuta» for
    its weeks, never «libera». The client is optional because a match older than the
    2026-10-30 link has no deal in the CRM; `Engagement.missing` names what is not filled in.

    The payment rule's columns agree with its kind (the table says so): a fixed term has its
    days and no collection days; paid after the client has collection days, from 1 to 60, and
    **no fixed term at all**, since the printed clause of the letter names no floor.
    """

    __tablename__ = "engagements"
    __table_args__ = (
        Index("uq_engagements_hub_match_id", "hub_match_id", unique=True),
        Index("ix_engagements_freelancer_dates", "freelancer_id", "starts_on", "ends_on"),
        CheckConstraint(_in("state", ENGAGEMENT_STATES), name="ck_engagements_state"),
        CheckConstraint(_in("fee_unit", FEE_UNITS), name="ck_engagements_fee_unit"),
        CheckConstraint(_in("letter_type", LETTER_TYPES), name="ck_engagements_letter_type"),
        CheckConstraint(_in("payment_rule", PAYMENT_RULES), name="ck_engagements_payment_rule"),
        CheckConstraint("fee > 0", name="ck_engagements_fee_positive"),
        CheckConstraint(
            "expected_days IS NULL OR expected_days > 0", name="ck_engagements_expected_days"
        ),
        CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on", name="ck_engagements_dates_in_order"
        ),
        CheckConstraint(
            "days_per_week IS NULL OR (days_per_week BETWEEN 0.5 AND 5 "
            "AND mod(days_per_week * 2, 1) = 0)",
            name="ck_engagements_days_per_week",
        ),
        CheckConstraint(
            f"(payment_rule = 'fixed_term' AND payment_days BETWEEN 1 AND {PAYMENT_DAYS_LIMIT} "
            "AND collection_days IS NULL "
            f"AND (NOT payment_end_of_month OR payment_days <= {PAYMENT_DAYS_LIMIT_MONTH_END})) "
            "OR (payment_rule = 'on_collection' AND payment_days IS NULL "
            f"AND collection_days BETWEEN 1 AND {COLLECTION_DAYS_LIMIT} "
            "AND NOT payment_end_of_month)",
            name="ck_engagements_payment_rule_columns",
        ),
    )

    # The hub's match id, the import's idempotency key (design § 1, «Identity across the move»).
    hub_match_id: Mapped[UUID | None] = mapped_column(default=None)
    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    # The client, and the deal or contract it serves in this space. All three are optional:
    # see the class docstring.
    client_organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None, index=True
    )
    deal_id: Mapped[UUID | None] = mapped_column(ForeignKey("deals.id"), default=None, index=True)
    contract_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contracts.id"), default=None, index=True
    )
    role: Mapped[str] = mapped_column(String(ROLE_MAX_LENGTH), nullable=False)
    starts_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[dt.date | None] = mapped_column(Date, default=None)
    days_per_week: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), default=None)
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fee_unit: Mapped[str] = mapped_column(String(10), nullable=False)
    # For an hourly fee, the hours at the hub's 8 per day: the one place hours become days.
    expected_days: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), default=None)
    letter_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ordinary", server_default=text("'ordinary'")
    )
    payment_rule: Mapped[str] = mapped_column(String(20), nullable=False)
    payment_days: Mapped[int | None] = mapped_column(Integer, default=None)
    payment_end_of_month: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )
    collection_days: Mapped[int | None] = mapped_column(Integer, default=None)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=text("'draft'")
    )

    @property
    def missing(self) -> list[str]:
        """What an admin still has to fill in: `days_per_week` and the `client` link."""
        out: list[str] = []
        if self.days_per_week is None:
            out.append("days_per_week")
        if self.deal_id is None and self.contract_id is None:
            out.append("client")
        return out
