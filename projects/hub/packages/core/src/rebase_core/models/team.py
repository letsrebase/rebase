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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.models.identity import PHONE_MAX_LENGTH, REFERRAL_CODE_LENGTH
from rebase_core.models.profiles import COMPANY_MAX_LENGTH, JOB_TITLE_MAX_LENGTH
from rebase_core.models.sessions import TOKEN_HASH_LENGTH

# ---- the team builder and the talent cloud (REB-509, design 2026-09-25) ----------------

CARD_SENIORITIES = ("junior", "mid", "senior", "lead")
TEAM_REQUEST_STATES = ("new", "contacted", "closed")
TEAM_PROPOSAL_ORIGINS = ("public", "cloud", "admin")
# The refusals an attempt row keeps in `error` (0028): the domain codes of
# `LlmUnavailable` and `TeamBuilderBusy`, and nothing else, since a switch that is off
# or a description the schema refuses never reaches the engine.
TEAM_PROPOSAL_ERRORS = ("llm_unavailable", "team_builder_busy")
TEAM_REQUEST_ORIGINS = ("public", "cloud")
TALENT_ANSWERS = ("yes", "no")
# An Anthropic model id, `claude-opus-5` today: short, but the SDK's own ids run longer
# (`claude-opus-4-20250514`), so the width is generous the same way every other width in
# this file is.
CARD_MODEL_MAX_LENGTH = 60


class FreelancerCard(Base, TimestampMixin):
    """Claude's anonymous read of a freelancer's CV (spec § 2.1), one row per card
    (`freelancer_id` is the primary key: at most one per person, gone with the person on
    `ON DELETE CASCADE`). `cv_sha256`, `card`, `model` and the two token counts are the
    *last successful* generation, written together by `rebase_core.card_writer.CardWriter`
    (spec § 5.1) or not at all -- a freelancer whose CV has never produced a card has
    every one of these `NULL`. `error` and `error_cv_sha256` are the *last failure*,
    independent of whether a card exists at all: an empty CV, a refusal, a `max_tokens`
    stop or a body that is not the shape writes these two, so the same CV is not retried
    and paid for again until it changes, and retires a card written from another CV (it
    must not outlive the CV it describes); an outage writes `error` alone and keeps the
    card, and the next attempt asks again (REB-510)."""

    __tablename__ = "freelancer_cards"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), primary_key=True
    )
    cv_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    card: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    model: Mapped[str | None] = mapped_column(String(CARD_MODEL_MAX_LENGTH), default=None)
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    error_cv_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    # What the card was written from and by what (REB-837), all `NULL` on a card written
    # before: the prompt version and provider, the model's claim of which sources
    # supported each field (`sources`, checked by code, never public), the hash of every
    # source it read (`source_hashes`: `github`, `link:<n>`), the conflicts the code found
    # between the CV and another source, and the thinking tokens the call spent.
    prompt_version: Mapped[int | None] = mapped_column(Integer, default=None)
    provider: Mapped[str | None] = mapped_column(String(20), default=None)
    sources: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    source_hashes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    conflicts: Mapped[list[Any] | None] = mapped_column(JSONB, default=None)
    thinking_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    # What the same call read out of the CV about who the person is (REB-855): name, email,
    # phone, city, links and the companies named, as `CardPersonRead`. Admin only: no
    # public, cloud, team-builder or member read touches it. Written with the card, kept
    # when the guard refused the anonymous card, and retired with it when the CV changes.
    person: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)


class TeamProposal(Base, PrimaryKeyMixin):
    """One team the engine proposed (spec § 3.4), immutable once written -- no
    `TimestampMixin`: a «Rigenera» writes a new row with `previous_id` pointing at the
    one it replaces, never edits this one. `location`, `team` and `economics` are the JSONB
    the engine and § 3.3 describe; `model` and the three token counts are `usage` from
    the call that produced this row. `origin` says who asked (`TEAM_PROPOSAL_ORIGINS`):
    the public page, a signed-in cloud user, or an admin from the talent cloud;
    `user_id` is that person, `NULL` for a public visitor with no account at all.
    `people` is the headcount the visitor picked (REB-591), `NULL` when the description
    alone sized the team, as the MCP tool asks. Since 0028 every «Proponi il team» that
    reached the hub is a row, so the asks a company never files are still counted (Ivan,
    2026-09-29, DECISIONS.md): `error` is `NULL` on a proposal that answered, and the
    domain code (`TEAM_PROPOSAL_ERRORS`) on an attempt Claude did not answer or the caps
    refused, whose summary is empty, whose team is nobody, and whose id never left the
    hub -- `get`, «Rigenera» and «Assumi team» all refuse it."""

    __tablename__ = "team_proposals"

    description: Mapped[str] = mapped_column(Text, nullable=False)
    # The «Rigenera» note, e.g. «togli il designer» (spec § 3.4): `NULL` on a first ask.
    note: Mapped[str | None] = mapped_column(Text, default=None)
    previous_id: Mapped[UUID | None] = mapped_column(ForeignKey("team_proposals.id"), default=None)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    team: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    economics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(String(CARD_MODEL_MAX_LENGTH), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    people: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(String(30), default=None)
    # The company of the grant a cloud proposal was made under (0029, REB-578): «Assumi
    # team» files under it or refuses. `NULL` on the public and admin proposals, which
    # belong to no company, and on the cloud ones written before 0029, which fall back
    # to the newest-grant date check.
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_team_proposals_created_at", "created_at"),
        # «Fallite» in «Proposte» (0028): the attempts alone, newest first, without a
        # scan of every proposal that answered.
        Index(
            "ix_team_proposals_error_created_at",
            "created_at",
            postgresql_where=text("error IS NOT NULL"),
        ),
        CheckConstraint("origin IN ('public', 'cloud', 'admin')", name="ck_team_proposals_origin"),
        CheckConstraint(
            "people IS NULL OR (people >= 1 AND people <= 10)",
            name="ck_team_proposals_people",
        ),
        CheckConstraint(
            "error IN ('llm_unavailable', 'team_builder_busy')",
            name="ck_team_proposals_error",
        ),
    )


class TeamRequest(Base, PrimaryKeyMixin, TimestampMixin):
    """A company's «Assumi team» (spec § 3.2, § 3.5): who asked (`company`, `email`,
    `phone`, the same shape the company wizard already collects), and which proposal
    it is for -- `proposal_id`, `NULL` for a single-talent request with no team proposal
    behind it, unique where set (`uq_team_requests_proposal_id`) so a double click on
    «Assumi team» is a `409`, not a second row. `origin` is `public` or `cloud`
    (`TEAM_REQUEST_ORIGINS`); `user_id` and `company_id` are the cloud user and their own
    request row, both `NULL` for a public visitor with neither. `status` is the admin's
    own progress on it (`TEAM_REQUEST_STATES`), `note` theirs too; `contacted_at` and
    `closed_at` are each set once, by «Contatta i talenti» and «Chiudi»."""

    __tablename__ = "team_requests"

    proposal_id: Mapped[UUID | None] = mapped_column(ForeignKey("team_proposals.id"), default=None)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    company: Mapped[str] = mapped_column(String(COMPANY_MAX_LENGTH), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(PHONE_MAX_LENGTH), default=None)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"), default=None)
    # The referral code the public visitor arrived with (`?ref=`, REB-600), kept as typed
    # and unresolved: a public request has no account, so nothing is credited here. It is
    # read once, when a `Company` appears for the same contact (the company wizard, or an
    # admin opening the cloud), by `ReferralService.link_team_request`.
    ref: Mapped[str | None] = mapped_column(String(REFERRAL_CODE_LENGTH), default=None)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    note: Mapped[str | None] = mapped_column(Text, default=None)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index(
            "uq_team_requests_proposal_id",
            "proposal_id",
            unique=True,
            postgresql_where=text("proposal_id IS NOT NULL"),
        ),
        Index("ix_team_requests_status_created_at", "status", "created_at"),
        CheckConstraint("status IN ('new', 'contacted', 'closed')", name="ck_team_requests_status"),
        CheckConstraint("origin IN ('public', 'cloud')", name="ck_team_requests_origin"),
    )


class TeamRequestTalent(Base, PrimaryKeyMixin):
    """One talent asked for on a request (spec § 3.5, § 3.6): `role` is the role they
    were proposed for, carried here on its own rather than re-read off the proposal's
    `team` JSONB, so a request keeps its own copy even if the proposal above it is later
    regenerated. `token_hash` is the availability mail's one-use token (SHA-256 at rest,
    `token_urlsafe(32)` sent, the magic link's own shape), `NULL` until the first send;
    `mail_sent_at`, `answer` (`TALENT_ANSWERS`) and `answered_at` fill in as the mail
    goes out and the talent answers, all `NULL` until then. `unique (request_id,
    freelancer_id)`: a talent is asked once per request."""

    __tablename__ = "team_request_talents"

    request_id: Mapped[UUID] = mapped_column(ForeignKey("team_requests.id"), nullable=False)
    freelancer_id: Mapped[UUID] = mapped_column(ForeignKey("freelancers.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(JOB_TITLE_MAX_LENGTH), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(TOKEN_HASH_LENGTH), default=None)
    mail_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    answer: Mapped[str | None] = mapped_column(String(10), default=None)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index(
            "uq_team_request_talents_request_freelancer",
            "request_id",
            "freelancer_id",
            unique=True,
        ),
        Index("uq_team_request_talents_token_hash", "token_hash", unique=True),
        CheckConstraint("answer IN ('yes', 'no')", name="ck_team_request_talents_answer"),
    )


class TalentCloudGrant(Base, PrimaryKeyMixin):
    """A company's access to the private talent cloud (spec § 4.1): opened by the
    admin's «open the talent cloud» action on a company request's page, for the contact
    person's own `user_id` and that request's `company_id`, closed by the revoke action.
    One live grant per person and company (`revoked_at IS NULL`, the partial unique index
    below, REB-518): a second «open» on the same request answers the existing row rather
    than doubling it, and a person behind two companies holds two grants, the cloud open
    while either is live (`rebase_core.cloud`). `granted_by` and `revoked_by` are the admin
    who acted."""

    __tablename__ = "talent_cloud_grants"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    granted_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index(
            "uq_talent_cloud_grants_user_company_live",
            "user_id",
            "company_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
