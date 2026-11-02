from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base
from rebase_core.models.team import CARD_MODEL_MAX_LENGTH


class AnonymousCvRow(Base):
    """The last anonymous CV written for a freelancer (REB-862, migration 0044): `body` is
    the `AnonymousCv` as validated, `cv_sha256` the hash of the CV file it was written
    from (the value `cards.stored_persona` computes) and `prompt_version` the prompt that
    wrote it. A download reuses the row only when both still match; otherwise the CV is
    written again and the row replaced. Gone with the person (`ON DELETE CASCADE`) and
    with the CV (`FreelancerService.clear_cv`)."""

    __tablename__ = "anonymous_cvs"

    freelancer_id: Mapped[UUID] = mapped_column(
        ForeignKey("freelancers.id", ondelete="CASCADE"), primary_key=True
    )
    cv_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(CARD_MODEL_MAX_LENGTH), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
