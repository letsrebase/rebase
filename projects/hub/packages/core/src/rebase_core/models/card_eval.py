from datetime import datetime
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
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin
from rebase_core.models.team import CARD_MODEL_MAX_LENGTH

# ---- the evaluation set (REB-835) ---------------------------------------------------------

CARD_EVAL_LABEL_MAX_LENGTH = 40


class CardEvalProfile(Base):
    """One person of the evaluation sample, the set `rebase cards-eval` runs when it is
    given no one in particular (design 2026-10-27, «Evaluation before a change goes
    live»; REB-842). It holds who and why, never a card: the sample is chosen once, by id,
    so two runs measure the same people. `label` is the failure the person covers («senza
    date», «duplicato», «GitHub»), which is what the sample is stratified by. The row goes
    with the person (`ON DELETE CASCADE`)."""

    __tablename__ = "card_eval_profiles"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str] = mapped_column(String(CARD_EVAL_LABEL_MAX_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CardEvalRun(Base, PrimaryKeyMixin):
    """One `rebase cards-eval`: which task, provider, model and effort it measured, the
    prompt version it ran, and the totals (`summary`: counts, latency, cost, the change
    against the previous run of the task). The per-person rows are `CardEvalResult`."""

    __tablename__ = "card_eval_runs"

    task: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(CARD_MODEL_MAX_LENGTH), nullable=False)
    effort: Mapped[str | None] = mapped_column(String(10), default=None)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("task IN ('card', 'team_proposal')", name="ck_card_eval_runs_task"),
        Index("ix_card_eval_runs_task_created", "task", "created_at"),
    )


class CardEvalResult(Base):
    """What a run made of one person: the card it wrote (personal data, so the row goes
    with the run and with the person), what the rules found in it (`checks`: the years,
    anonymity; `anni_ok` and `guard_clean` are their headline), what the judging call said
    of each field against the sources
    (`judgement`), and the latency and cost of the generation and of the judging, each
    apart. `failure` is the kind of failure when no card came out (`cards.Failure`), and
    then the checks and the judgement are `NULL`."""

    __tablename__ = "card_eval_results"

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("card_eval_runs.id", ondelete="CASCADE"), primary_key=True
    )
    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), primary_key=True
    )
    label: Mapped[str] = mapped_column(String(CARD_EVAL_LABEL_MAX_LENGTH), nullable=False)
    anni_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    guard_clean: Mapped[bool | None] = mapped_column(Boolean, default=None)
    failure: Mapped[str | None] = mapped_column(String(20), default=None)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    cost_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    card: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    checks: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    judgement: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    judge_latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    judge_cost_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
