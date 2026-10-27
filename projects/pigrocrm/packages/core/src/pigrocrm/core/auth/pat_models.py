from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, false
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class PersonalAccessToken(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "personal_access_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # What this credential may do beyond the ordinary surface (REB-810): the fiscal acts,
    # rates, period locks and the Google reads `AGENT_FORBIDDEN_ACTIONS` names. Chosen by
    # the owner when the token is made, closed unless asked for, and read by
    # `PatService.resolve` together with the space's `mcp_full_access`, which stays the
    # ceiling: a token never has more than the space allows.
    full_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
