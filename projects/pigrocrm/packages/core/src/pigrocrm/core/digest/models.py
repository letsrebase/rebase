"""One row per week the report went out (spec 2026-09-16 §3.3).

`week` is the ISO week (`2026-W38`) and it is unique: a cron that runs twice, or
two hosts, cannot send the same week twice. `sent_to` keeps the addresses that
received it, which is what a person asking «did I get it?» needs answered.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin


class Digest(Base, PrimaryKeyMixin):
    __tablename__ = "digests"

    week: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    sent_to: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
