"""The Slack workspace a space is connected to, who in it is whom, and the channel posts
(REB-739, REB-740; spec 2026-10-26 §6.4, decision of 2026-10-26, REB-730).

One Slack app serves the installation and is installed once **per space**: a space is its
own database, so these tables hold at most one workspace, the one an admin of that space
connected. Nothing here is safe to print, and there is deliberately no `__repr__` on
`SlackWorkspace`, for the reason `GoogleAccount` has none: the inherited one prints the class
and an address, which is the behaviour the sealed bot token needs from every traceback.
"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class SlackWorkspace(Base, PrimaryKeyMixin, TimestampMixin):
    """The workspace this space's Slack app was installed in.

    `status` carries the distinction `GoogleAccount.status` works through for Gmail:
    `active`; `revoked` (Slack told us the token is dead: `token_revoked`, `invalid_auth`,
    `account_inactive`, an uninstall); `disconnected` (what an admin did). A `revoked` row
    keeps its team and its matches so that reconnecting the same workspace loses nothing;
    `disconnected` overwrites the sealed token and keeps the row for the same reason.

    The bot token is sealed with AES-256-GCM under `PIGROCRM_SLACK_TOKEN_KEY` derived for the
    space (`tenants/google.py`), the same `seal`/`unseal` as the Google refresh tokens, and
    never logged, never returned by an API schema and never reachable from an MCP tool.
    Slack bot tokens do not expire unless token rotation is on, and the app does not turn it
    on, so there is no refresh token and no expiry to track.
    """

    __tablename__ = "slack_workspaces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked', 'disconnected')", name="ck_slack_workspaces_status"
        ),
        # One workspace per space, whatever the rows say: the same expression in every row
        # is a value the unique index admits once.
        Index("uq_slack_workspaces_one_per_space", text("(true)"), unique=True),
    )

    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bot_user_id: Mapped[str] = mapped_column(String(32), nullable=False)
    bot_token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    bot_token_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    scopes_granted: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # The one channel the space-wide reminders are also posted to, or none. A Slack id and
    # the name it had when it was chosen, which is only what the page shows.
    channel_id: Mapped[str | None] = mapped_column(String(32), default=None)
    channel_name: Mapped[str | None] = mapped_column(String(80), default=None)
    connected_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    # The sentence the admin reads, in Italian: never a body from Slack and never a token.
    last_error: Mapped[str | None] = mapped_column(String(500), default=None)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class SlackUserLink(Base, PrimaryKeyMixin, TimestampMixin):
    """Which Slack member a PigroCRM user is.

    `source` says how: `email` (looked up by the user's address when the workspace was
    connected, on «Cerca di nuovo», or when the first reminder for them was due), `manual`
    (an admin gave the Slack address by hand, the way to fix a mismatch) or `none` (the
    lookup found nobody; `slack_user_id` is then empty). A `none` row is what stops a pass
    from asking Slack about the same address every quarter of an hour: it is looked up again
    once a day and whenever an admin asks. A user with no row, or a `none` one, receives
    nothing on Slack, and the settings page says so.
    """

    __tablename__ = "slack_user_links"
    __table_args__ = (
        CheckConstraint("source IN ('email', 'manual', 'none')", name="ck_slack_user_links_source"),
        CheckConstraint(
            "(source = 'none') = (slack_user_id IS NULL)", name="ck_slack_user_links_id_iff_found"
        ),
        # A Slack member is one person: two users cannot both be them.
        Index(
            "uq_slack_user_links_slack_user",
            "slack_user_id",
            unique=True,
            postgresql_where=text("slack_user_id IS NOT NULL"),
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    slack_user_id: Mapped[str | None] = mapped_column(String(32), default=None)
    # The address the Slack member answered to, what the page shows next to a manual fix.
    slack_email: Mapped[str | None] = mapped_column(String(320), default=None)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # When this person became that Slack member, or none while unmatched: a reminder that
    # reached their list before is backlog and is not sent to Slack (`reminders/slack.py`).
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class SlackChannelPost(Base, PrimaryKeyMixin, TimestampMixin):
    """That a space-wide reminder was posted to the channel, so it is not posted again.

    The delivery ledger is keyed by user and a channel post has none, so this is its own
    small ledger with the same rules (`reminders/ledger.py`): written after Slack accepted
    the post, the rank kept at the highest delivered (an escalation is an upsert), `period`
    set only for the types delivered daily, and the rows of a key a pass no longer raises
    ended with the occurrence (`ReminderLedger.forget_unraised`). `type` repeats the key's
    prefix for that last delete, as every keyed reminder row does.
    """

    __tablename__ = "slack_channel_posts"
    __table_args__ = (
        UniqueConstraint(
            "key",
            "channel_id",
            "period",
            name="uq_slack_channel_post_occurrence",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("severity_rank BETWEEN 1 AND 3", name="ck_slack_channel_posts_rank"),
        Index("ix_slack_channel_posts_type", "type"),
    )

    key: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False)
    period: Mapped[date | None] = mapped_column(Date, default=None)
    severity_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
