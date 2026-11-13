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
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
# is warned against, and what availability counts, so a strip and the warning never disagree.
LIVE_STATES: tuple[str, ...] = ("draft", "awaiting_signature", "active")
# The states in which a month's report is received and decided: nothing is reported on a draft
# or on one that never began.
REPORTING_STATES: tuple[str, ...] = ("active", "concluded")

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
LETTER_NUMBER_LENGTH = 10


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
        Index("uq_engagements_letter_number", "letter_number", unique=True),
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
    # `YYYY-NNN`, drawn from `letter_counters` when the engagement is created and never given
    # back by a cancellation (the hub's rule): null only on an engagement written before the
    # letters moved here.
    letter_number: Mapped[str | None] = mapped_column(String(LETTER_NUMBER_LENGTH), default=None)

    @property
    def missing(self) -> list[str]:
        """What an admin still has to fill in: `days_per_week` and the `client` link."""
        out: list[str] = []
        if self.days_per_week is None:
            out.append("days_per_week")
        if self.deal_id is None and self.contract_id is None:
            out.append("client")
        return out


class LetterCounter(Base):
    """The last letter number given in a year: `2026-014` is the fourteenth letter of 2026. One
    row per year, advanced by an upsert so two engagements created together never share a number;
    a cancelled engagement keeps its number (design § 6, `create_engagement` draws it)."""

    __tablename__ = "letter_counters"
    __table_args__ = (CheckConstraint("last >= 1", name="ck_letter_counters_positive"),)

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last: Mapped[int] = mapped_column(Integer, nullable=False)


# A «Rendiconto» is `requested` from the day rebase received the freelancer's month and
# `approved` once a person decided it (collaborators design § 4). Reaching the tacit date with
# no decision is not a state a row is written in: `monthly_reports.effective_state` reads it.
MONTHLY_REPORT_STATES: tuple[str, ...] = ("requested", "approved")
OBJECTION_MAX_LENGTH = 2000


class MonthlyReport(Base, PrimaryKeyMixin, TimestampMixin):
    """The month's report of one engagement, as an approval request (design § 4): framework
    agreement 6.1 to 6.2. It holds no days: the report is read live from the freelancer's
    work units and hours, and this row only says which month was received, when it counts as
    approved if nobody objects (`tacit_on`), who approved it and which dates are held.

    `held_dates` are the days of the month the client contested; a held day is not payable
    while the rest of the month is. Holding a day needs the reason in `objection`.
    `freelancer_id` repeats the engagement's so the row-level policy reads the same
    organization the engagement's does.
    """

    __tablename__ = "engagement_monthly_reports"
    __table_args__ = (
        Index("uq_engagement_monthly_reports_month", "engagement_id", "month", unique=True),
        CheckConstraint(
            _in("state", MONTHLY_REPORT_STATES), name="ck_engagement_monthly_reports_state"
        ),
        CheckConstraint(
            "EXTRACT(DAY FROM month) = 1", name="ck_engagement_monthly_reports_month_first_day"
        ),
        CheckConstraint(
            "(state = 'approved') = (decided_on IS NOT NULL)",
            name="ck_engagement_monthly_reports_decided",
        ),
        CheckConstraint(
            "jsonb_typeof(held_dates) = 'array'", name="ck_engagement_monthly_reports_held_array"
        ),
        CheckConstraint(
            "jsonb_array_length(held_dates) = 0 OR objection IS NOT NULL",
            name="ck_engagement_monthly_reports_hold_has_reason",
        ),
    )

    engagement_id: Mapped[UUID] = mapped_column(
        ForeignKey("engagements.id"), nullable=False, index=True
    )
    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    # The first day of the month the report covers.
    month: Mapped[dt.date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="requested", server_default=text("'requested'")
    )
    # The day rebase received the report, and the last day an objection counts: the seventh
    # working day after it (framework 6.2).
    received_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    tacit_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    decided_on: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # The person who decided it: a user id, never an agent's (no tool approves).
    decided_by: Mapped[UUID | None] = mapped_column(default=None)
    objection: Mapped[str | None] = mapped_column(Text, default=None)
    held_dates: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


# What an engagement's notice to the hub reports (design § 5): the moments the hub's read model
# is upserted on. `updated` is a draft's fields changing before it is sent, which the hub's row
# would otherwise show stale until the first `sent`.
NOTICE_KINDS: tuple[str, ...] = (
    "created",
    "updated",
    "sent",
    "signed",
    "cancelled",
    "closed",
    "notice_served",
)
# `pending` waits to be delivered (and is retried), `delivered` is a 2xx, `rejected` is an answer
# the hub will give again however often it is asked (a 4xx that is not a wrong token or a limit).
NOTICE_STATES: tuple[str, ...] = ("pending", "delivered", "rejected")


class EngagementNotice(Base, PrimaryKeyMixin, TimestampMixin):
    """The outbox of the notices the CRM sends the hub about an engagement (collaborators design
    § 5, REB-1106): a row is written in the transaction that changes the engagement, so a change
    and its notice land together or not at all, and `HubNoticeSweep` delivers it from the leads
    space's frequent pass, retrying with a backoff until the hub answers 2xx.

    `sequence` counts the engagement's notices from 1: the hub ignores a notice older than the one
    it holds, so a retry or a late delivery cannot move its read model back. `payload` is the whole
    read model as it was when the change happened, never recomputed at delivery.

    The table has no foreign key on purpose: the notice is about a freelancer's engagement but is
    the CRM's own bookkeeping, read by no person and no API, so it carries no legal-entity policy
    (nothing joins it to `organizations`), and an engagement is never deleted, so the id never
    dangles. Every space writes its rows; only the space `PIGROCRM_LEADS_SPACE` names sweeps them.
    """

    __tablename__ = "engagement_notices"
    __table_args__ = (
        Index("uq_engagement_notices_sequence", "engagement_id", "sequence", unique=True),
        Index(
            "ix_engagement_notices_due",
            "next_attempt_at",
            postgresql_where=text("state = 'pending'"),
        ),
        CheckConstraint(_in("kind", NOTICE_KINDS), name="ck_engagement_notices_kind"),
        CheckConstraint(_in("state", NOTICE_STATES), name="ck_engagement_notices_state"),
        CheckConstraint("sequence >= 1", name="ck_engagement_notices_sequence"),
        CheckConstraint("attempts >= 0", name="ck_engagement_notices_attempts"),
    )

    engagement_id: Mapped[UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(
        String(10), nullable=False, default="pending", server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # The cause of the last failed try, bounded: a status or a word, never a body or a header.
    last_error: Mapped[str | None] = mapped_column(String(200), default=None)
