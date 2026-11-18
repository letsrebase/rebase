from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin

# ---- the CLI's sign-in and the work profile it sends (REB-1133, REB-1124; spec 2026-11-17) ----

# What a device authorization may be asked for. One scope today: the token it ends in can send
# one work profile and nothing else.
CLI_SCOPES = ("work_profile",)
# `pending` until the person decides, then `approved` or `denied`; `spent` once the token it
# handed out sent a profile; `expired` once the deadline passed with no use.
CLI_AUTHORIZATION_STATES = ("pending", "approved", "denied", "spent", "expired")
# The state of a received profile: nothing counts until an admin approves it.
WORK_PROFILE_STATES = ("draft", "approved", "rejected")
# How often and how steadily a technology shows up in a person's work (REB-1130), from the
# months it appears in and nothing else, lowest first. A plain-language label for counts, never a
# score and never a measure of skill (upstream ADR-001).
TECH_USAGE_BANDS = ("occasional", "recurring", "continuous")
# How a practice is counted (REB-1144): `code` from the session logs alone, `model` by the
# person's own model reading a sample of sessions.
PRACTICE_METHODS = ("code", "model")
# The sha256 hex of a device code or an access token, like `TOKEN_HASH_LENGTH`.
CLI_HASH_LENGTH = 64
# `XXXX-XXXX`.
USER_CODE_LENGTH = 9
TOOL_VERSION_MAX_LENGTH = 40


class CliAuthorization(Base, PrimaryKeyMixin):
    """One device authorization (RFC 8628, kept as small as the one use needs): the CLI asks
    for a code, the signed-in person approves it in the browser, the CLI polls until it holds
    a token that can send one profile. Only the sha256 of the device code and of the token is
    stored. `user_code` is unique only while the row is `pending`, so a code can come round
    again after its row has gone. `user_id` stays `NULL` until a person decides; the row hangs
    on that person with `ON DELETE CASCADE`.

    `poll_interval` is the seconds the CLI must wait between polls: 5, and 5 more each time it
    polls faster (`slow_down`). `expires_at` is the code's deadline (600 seconds) until the
    token is handed out, and the token's (900 seconds) from then on, so one column says when the
    row stops being of any use. The sweep deletes a row a day after it expired or was spent."""

    __tablename__ = "cli_authorizations"

    device_code_hash: Mapped[str] = mapped_column(String(CLI_HASH_LENGTH), nullable=False)
    user_code: Mapped[str] = mapped_column(String(USER_CODE_LENGTH), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), default=None
    )
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    token_hash: Mapped[str | None] = mapped_column(String(CLI_HASH_LENGTH), default=None)
    poll_interval: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    spent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint("scope IN ('work_profile')", name="ck_cli_authorizations_scope"),
        CheckConstraint(
            "state IN ('pending', 'approved', 'denied', 'spent', 'expired')",
            name="ck_cli_authorizations_state",
        ),
        Index("uq_cli_authorizations_device_code_hash", "device_code_hash", unique=True),
        Index(
            "uq_cli_authorizations_user_code_pending",
            "user_code",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index("uq_cli_authorizations_token_hash", "token_hash", unique=True),
        Index("ix_cli_authorizations_user_id", "user_id"),
    )


class WorkProfile(Base, PrimaryKeyMixin):
    """The work profile a freelancer sent (`apply-new`'s `candidate.json`, schema
    `playnew-profile/v1`), as received minus `contact`: the hub knows who sent it from the token
    and does not store the email and city a second time. One row per freelancer
    (`uq_work_profiles_freelancer_id`): a new submission replaces the row, whose state goes back
    to `draft`, and the replaced payload is not kept.

    `groundedness` and `unsupported_anchors` are the hub's own recomputation
    (`rebase_core.work_profiles.groundedness`), never the CLI's claim. `expires_at` is
    `submitted_at` plus 12 months, and the sweep deletes the row after it. A draft or rejected
    profile counts for nothing outside the admin; the decision columns are the admin review's."""

    __tablename__ = "work_profiles"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(TOOL_VERSION_MAX_LENGTH), default=None)
    groundedness: Mapped[int] = mapped_column(Integer, nullable=False)
    unsupported_anchors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # What the anonymity guards found in the fields a company reads: `{field, rule}`, never the
    # matched text (REB-1127). Approval is refused while one stands, unless an admin overrides.
    guard_hits: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # Rows of techUsage dropped at intake for a name this hub's labels do not have (REB-1141).
    unknown_technologies: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Practices items dropped at intake for an id this hub does not list (REB-1144).
    unknown_practices: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    decision_note: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        CheckConstraint(
            "state IN ('draft', 'approved', 'rejected')", name="ck_work_profiles_state"
        ),
        CheckConstraint(
            "groundedness BETWEEN 0 AND 100", name="ck_work_profiles_groundedness_range"
        ),
        Index("uq_work_profiles_freelancer_id", "freelancer_id", unique=True),
        Index("ix_work_profiles_expires_at", "expires_at"),
    )
