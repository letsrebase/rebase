from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin, TimestampMixin
from rebase_core.models.team import CARD_MODEL_MAX_LENGTH

# The model providers and the tasks they answer (REB-830): «Modelli AI» in the admin
# area, the Pydantic AI seam in `llm.py`. A provider is one of these three and a task one
# of these three; the database repeats both lists as `CHECK`s. The efforts are the one
# reasoning knob every provider maps in its own words (`llm.build_model`).
AI_PROVIDERS = ("anthropic", "openai", "google")
AI_TASKS = ("card", "team_proposal", "anonymous_cv", "crm_lead")
AI_EFFORTS = ("low", "medium", "high")
# Why a call ran (`ai_usage.purpose`): a real one, an admin's «Prova», an evaluation run.
# All three consume the cap of the task they ran as.
AI_USAGE_PURPOSES = ("run", "test", "eval")


class AiProvider(Base):
    """One model provider's account: the API key encrypted at rest (AES-256-GCM,
    `rebase_core.secrets`) in `key_ciphertext` and `key_nonce`, and `key_last4`, the only
    part of it that is ever shown again. Write-only: nothing reads the key back to a
    person, and no column here holds it in clear. `enabled` is the switch: a provider
    off is never called, whatever a task says."""

    __tablename__ = "ai_providers"

    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    key_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    key_last4: Mapped[str | None] = mapped_column(String(4), default=None)
    saved_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(
            "provider IN ('anthropic', 'openai', 'google')", name="ck_ai_providers_provider"
        ),
        CheckConstraint(
            "(key_ciphertext IS NULL) = (key_nonce IS NULL)", name="ck_ai_providers_key_pair"
        ),
    )


class AiTask(Base):
    """What answers one task (`card`, `team_proposal`, `anonymous_cv`, `crm_lead`): provider,
    free-text model name, reasoning effort where the provider has one, the output ceiling, and
    an optional daily cap in euros that stops the task's calls for the rest of the day. The
    rows are seeded by migrations 0036, 0044 and 0045; `rebase ai-seed` points them at the old
    Anthropic settings."""

    __tablename__ = "ai_tasks"

    task: Mapped[str] = mapped_column(String(20), primary_key=True)
    provider: Mapped[str] = mapped_column(ForeignKey("ai_providers.provider"), nullable=False)
    model: Mapped[str] = mapped_column(String(CARD_MODEL_MAX_LENGTH), nullable=False)
    effort: Mapped[str | None] = mapped_column(String(10), default=None)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    daily_cap_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "task IN ('card', 'team_proposal', 'anonymous_cv', 'crm_lead')", name="ck_ai_tasks_task"
        ),
        CheckConstraint("max_output_tokens > 0", name="ck_ai_tasks_max_output_tokens"),
        CheckConstraint(
            "daily_cap_eur IS NULL OR daily_cap_eur >= 0", name="ck_ai_tasks_daily_cap"
        ),
    )


class AiPrice(Base):
    """What one model costs, in euro per million tokens, as an admin entered it: no
    exchange rate lives in code. `thinking_eur_per_mtok` is `NULL` when the provider
    bills thinking like output, `cache_read_eur_per_mtok` when it has no cheaper rate for
    cached input. A task whose model has no row here is not callable
    (`AiConfigService.can_call`): an unpriced call is never recorded as free."""

    __tablename__ = "ai_prices"

    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    model: Mapped[str] = mapped_column(String(CARD_MODEL_MAX_LENGTH), primary_key=True)
    input_eur_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    output_eur_per_mtok: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    thinking_eur_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), default=None)
    cache_read_eur_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), default=None)

    __table_args__ = (
        CheckConstraint(
            "provider IN ('anthropic', 'openai', 'google')", name="ck_ai_prices_provider"
        ),
        CheckConstraint(
            "input_eur_per_mtok >= 0 AND output_eur_per_mtok >= 0 "
            "AND (thinking_eur_per_mtok IS NULL OR thinking_eur_per_mtok >= 0) "
            "AND (cache_read_eur_per_mtok IS NULL OR cache_read_eur_per_mtok >= 0)",
            name="ck_ai_prices_not_negative",
        ),
    )


class AiUsage(Base, PrimaryKeyMixin):
    """One model call the provider answered, a refusal included: `task` is the configured
    task whose daily cap the call consumes, `purpose` why it ran (`AI_USAGE_PURPOSES`),
    `provider` and `model` what actually answered (an evaluation run may differ from the
    task's own). `cost_eur` is `NULL`, and `priced` false, when no price row existed for
    the model: the call is counted and shown as unpriced, never as free.
    `freelancer_id` is the person a card call was about, `NULL` for the others and after
    the person is deleted."""

    __tablename__ = "ai_usage"

    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    task: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), nullable=False, default="run")
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(CARD_MODEL_MAX_LENGTH), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    priced: Mapped[bool] = mapped_column(Boolean, nullable=False)
    freelancer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("freelancers.id", ondelete="SET NULL"), default=None
    )
    # The call went through a provider's batch API (REB-853), and `cost_eur` is at the batch
    # price: half of the price row's, as Anthropic bills it.
    batch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Set while the row is a hold, not a call that was answered (HCORE-01, migration 0047):
    # `cost_eur` is then the estimate reserved before the network call, and the hold counts
    # against the daily cap until it is settled (this column cleared, the real numbers
    # written), released (the row deleted) or this moment passes.
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_ai_usage_task_at", "task", "at"),
        CheckConstraint(
            "task IN ('card', 'team_proposal', 'anonymous_cv', 'crm_lead')", name="ck_ai_usage_task"
        ),
        CheckConstraint("purpose IN ('run', 'test', 'eval')", name="ck_ai_usage_purpose"),
        CheckConstraint("priced = (cost_eur IS NOT NULL)", name="ck_ai_usage_priced"),
    )


# Where a card batch stands (`CardBatch.status`): being sent, running at the provider, ended
# there and being applied card by card, applied, or never sent. One batch is active at a time.
CARD_BATCH_STATUSES = ("submitting", "in_progress", "ended", "applied", "failed")
CARD_BATCH_ACTIVE = ("submitting", "in_progress", "ended")
# What became of one card of a batch (`CardBatchItem.status`): waiting for its result, written,
# failed on its own account (the CV's, as in `cards.Failure`), left as it was because the CV
# or the inputs changed meanwhile or another writer had the card, or never answered
# (the provider's error, an expiry, a cancellation): that card stays stale.
CARD_BATCH_ITEM_STATUSES = ("pending", "written", "failed", "superseded", "unchanged", "errored")


class CardBatch(Base, PrimaryKeyMixin, TimestampMixin):
    """One regeneration sent through the Message Batches API (REB-853): the provider's id
    and the handle that finds it again after a restart (`handle`: model, group and the time
    it was created, no secret in it), who started it and when it was sent, ended and applied.
    Its cards are `CardBatchItem`. At most one is active at a time (`CARD_BATCH_ACTIVE`),
    which `CardBatchService.submit` checks under the regeneration's advisory lock:
    the `cards` service's loop and `rebase cards-batch-poll` carry it from there."""

    __tablename__ = "card_batches"

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_batch_id: Mapped[str | None] = mapped_column(String(80), default=None, unique=True)
    handle: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="submitting")
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(
            "status IN ('submitting', 'in_progress', 'ended', 'applied', 'failed')",
            name="ck_card_batches_status",
        ),
        Index("ix_card_batches_status", "status"),
    )


class CardBatchItem(Base):
    """One card of a batch: the person (`custom_id` at the provider), the hash of the CV and of
    every input the request was built from, so a result is applied only to the card it was
    asked for, and what became of it. `usage_recorded` says the batch price of its answer is
    in `ai_usage`, so a result applied twice after a restart is paid for in the books once.
    Hashes only: never the CV, the request or the answer. Gone with the batch and with the
    person."""

    __tablename__ = "card_batch_items"

    batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("card_batches.id", ondelete="CASCADE"), primary_key=True
    )
    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), primary_key=True
    )
    cv_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hashes: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    # The `PROMPT_VERSION` the request was built with: a deploy that bumps it while the batch
    # runs must not stamp an answer to the old prompt as the new version's.
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    usage_recorded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'written', 'failed', 'superseded', 'unchanged', 'errored')",
            name="ck_card_batch_items_status",
        ),
        Index("ix_card_batch_items_freelancer", "freelancer_id"),
    )


AI_SOURCE_KINDS = ("github", "links")
SOURCE_KINDS = ("github", "link")
SOURCE_STATUSES = ("ok", "unreadable", "blocked", "robots", "too_large", "error")


class AiSource(Base):
    """The switch of one kind of source the card may be written from (REB-837): `github`
    and `links`, off by default and off in production until the privacy notice covers
    reading what a person linked (design 2026-10-27, «Privacy and legal»). Off means no
    fetch, no stored text read and none kept: `SourceService` deletes the rows of a kind
    that is off the next time it refetches."""

    __tablename__ = "ai_sources"

    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (CheckConstraint("kind IN ('github', 'links')", name="ck_ai_sources_kind"),)


class FreelancerSource(Base, PrimaryKeyMixin):
    """The text of one third-party source a card is written from, per person: the GitHub
    account (`kind` `github`, `ref` the login) or one link they gave (`kind` `link`, `ref`
    the URL, `position` its place among the links that are read). `text` is what was read
    and `content_sha256` the hash of it **without what moves on its own** (stars, last
    push, page dates), so a source changes the card's staleness only when its content
    does; both are empty when `status` is not `ok`. Gone with the person (`ON DELETE
    CASCADE`), and `SourceService.delete_all` removes it when the person is soft-deleted
    or merged away. LinkedIn is never a row: it is dropped before fetching."""

    __tablename__ = "freelancer_sources"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # How many reads in a row failed with `error` (REB-844): 0 after a read that worked, and
    # what `SourceService` turns into the wait before the next try (an hour, doubling, a day
    # at most). `fetched_at` is the last attempt, not the last success.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("freelancer_id", "kind", "ref", name="uq_freelancer_sources_ref"),
        CheckConstraint("attempts >= 0", name="ck_freelancer_sources_attempts"),
        CheckConstraint("kind IN ('github', 'link')", name="ck_freelancer_sources_kind"),
        CheckConstraint(
            "status IN ('ok', 'unreadable', 'blocked', 'robots', 'too_large', 'error')",
            name="ck_freelancer_sources_status",
        ),
    )
