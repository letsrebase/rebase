from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.models.identity import NAME_MAX_LENGTH

# ---- campaigns: a list, a mail, and what it led to (P-REB-41) ---------------------------

CAMPAIGN_SOURCES = ("status", "filters", "list")
CAMPAIGN_STATES = ("draft", "scheduled", "sending", "sent", "cancelled")
CAMPAIGN_ACTIONS = (
    "signed_in",
    "cv",
    "card_complete",
    "profile_created",
    "request_updated",
    "pigro_customer",
    "click",
)
CAMPAIGN_DESTINATIONS = ("area", "wizard", "pigro", "request", "link")
RECIPIENT_STATES = ("queued", "sent", "skipped", "failed")
RECIPIENT_KINDS = ("freelancer", "lead", "company", "owner")
OPTOUT_SOURCES = ("link", "complaint", "admin")
CAMPAIGN_NAME_MAX_LENGTH = 120
CAMPAIGN_SLUG_MAX_LENGTH = 80
CAMPAIGN_SUBJECT_MAX_LENGTH = 200
CAMPAIGN_BUTTON_MAX_LENGTH = 60
CAMPAIGN_BUTTON_URL_MAX_LENGTH = 500
CAMPAIGN_TEXT_MAX_LENGTH = 5000
RECIPIENT_REASON_MAX_LENGTH = 200
CAMPAIGN_STALL_MAX_LENGTH = 200


class Campaign(Base, PrimaryKeyMixin, TimestampMixin):
    """One list and one mail (spec § 1). `source` says where the list comes from: a
    journey state (`journey_status`), the Talenti or company filters (`filters`), or the
    recipients of an earlier campaign who did nothing (`follows_id`, phase 2), and the
    constraints keep each source with its own field. `content_at` moves only when the
    admin edits the list or the mail, never on the writes that record a test or a
    send, because «Invia» compares it with `test_sent_at`: `updated_at` would move
    on the test's own write and lock the button for good."""

    __tablename__ = "campaigns"

    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(CAMPAIGN_NAME_MAX_LENGTH), nullable=False)
    slug: Mapped[str] = mapped_column(String(CAMPAIGN_SLUG_MAX_LENGTH), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    journey_status: Mapped[str | None] = mapped_column(String(30), default=None)
    # `none_as_null=True`: an explicit `None` on this attribute must bind as a true SQL
    # NULL, not a JSON `null` (SQLAlchemy's JSONB default), so a service can clear
    # `filters` on an edit without tripping `ck_campaigns_filters`. Bind-side only, no
    # migration needed.
    filters: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), default=None)
    follows_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id"), default=None)
    subject: Mapped[str] = mapped_column(String(CAMPAIGN_SUBJECT_MAX_LENGTH), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    button_text: Mapped[str] = mapped_column(String(CAMPAIGN_BUTTON_MAX_LENGTH), nullable=False)
    button_meta: Mapped[str] = mapped_column(String(10), nullable=False)
    # «Un link» (REB-530): where the button leads when it leaves the hub, and only then.
    button_url: Mapped[str | None] = mapped_column(
        String(CAMPAIGN_BUTTON_URL_MAX_LENGTH), default=None
    )
    action: Mapped[str] = mapped_column(String(25), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    content_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    test_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Phase 3 (spec § 6.3): the last good and the last failed read of the CRM's usage.
    pigro_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pigro_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # REB-524: the last time a pass stopped on something that will not fix itself (a
    # key Resend refuses, a list it can no longer read), and why, in the admin's words.
    # Both `None` again once a mail leaves or the send ends (`tick.py`).
    stalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    stalled_reason: Mapped[str | None] = mapped_column(
        String(CAMPAIGN_STALL_MAX_LENGTH), default=None
    )

    __table_args__ = (
        Index("uq_campaigns_slug", "slug", unique=True),
        Index("ix_campaigns_status_scheduled_for", "status", "scheduled_for"),
        CheckConstraint("source IN ('status', 'filters', 'list')", name="ck_campaigns_source"),
        CheckConstraint(
            "status IN ('draft', 'scheduled', 'sending', 'sent', 'cancelled')",
            name="ck_campaigns_status",
        ),
        CheckConstraint(
            "action IN ('signed_in', 'cv', 'card_complete', 'profile_created', "
            "'request_updated', 'pigro_customer', 'click')",
            name="ck_campaigns_action",
        ),
        CheckConstraint(
            "button_meta IN ('area', 'wizard', 'pigro', 'request', 'link')",
            name="ck_campaigns_button_meta",
        ),
        CheckConstraint(
            "(source = 'status') = (journey_status IS NOT NULL)", name="ck_campaigns_journey_status"
        ),
        CheckConstraint(
            "(source = 'filters') = (filters IS NOT NULL)", name="ck_campaigns_filters"
        ),
        CheckConstraint(
            "(source = 'list') = (follows_id IS NOT NULL)", name="ck_campaigns_follows"
        ),
        CheckConstraint(
            "(button_meta = 'link') = (button_url IS NOT NULL)", name="ck_campaigns_button_url"
        ),
        CheckConstraint(
            "(button_meta = 'link') = (action = 'click')", name="ck_campaigns_link_click"
        ),
    )


class CampaignRecipient(Base, PrimaryKeyMixin):
    """One person of a frozen list (spec § 3). `email` is lowercase and unique within a
    campaign. `before` is the state the action is measured against, taken when the list
    was frozen. `unsubscribe_token` is kept as it was minted, not hashed: Resend's
    `Idempotency-Key` wants the byte-identical request on a retry (409
    `invalid_idempotent_request` otherwise), so a retried mail must render the same
    link, and what a leaked token can do is only unsubscribe that one address."""

    __tablename__ = "campaign_recipients"

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    freelancer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("freelancers.id", ondelete="SET NULL"), default=None
    )
    signup_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("signups.id", ondelete="SET NULL"), default=None
    )
    pigro_slugs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    code: Mapped[str] = mapped_column(String(8), nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")
    reason: Mapped[str | None] = mapped_column(String(RECIPIENT_REASON_MAX_LENGTH), default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resend_id: Mapped[str | None] = mapped_column(String(64), default=None)
    unsubscribe_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    first_click_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    complaint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    signed_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("uq_campaign_recipients_campaign_email", "campaign_id", "email", unique=True),
        Index("uq_campaign_recipients_resend_id", "resend_id", unique=True),
        Index("uq_campaign_recipients_token", "unsubscribe_token", unique=True),
        Index("ix_campaign_recipients_email", "email"),
        Index("ix_campaign_recipients_campaign_status", "campaign_id", "status"),
        CheckConstraint(
            "status IN ('queued', 'sent', 'skipped', 'failed')",
            name="ck_campaign_recipients_status",
        ),
        CheckConstraint(
            "kind IN ('freelancer', 'lead', 'company', 'owner')",
            name="ck_campaign_recipients_kind",
        ),
    )


class CampaignOptout(Base):
    """An address no campaign may reach (spec § 7): by its own link, by a spam complaint
    Resend reported, or by an admin's «Non scrivere mai». Campaigns only: the magic link,
    the welcome mail and the contracts keep going."""

    __tablename__ = "campaign_optouts"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    campaign_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), default=None
    )

    __table_args__ = (
        CheckConstraint(
            "source IN ('link', 'complaint', 'admin')", name="ck_campaign_optouts_source"
        ),
    )
