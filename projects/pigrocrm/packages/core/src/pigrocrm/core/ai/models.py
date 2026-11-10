"""PigroCRM's own model settings and what it spent (design 2026-11-09, incoming documents
inbox, § 3.2): the hub's «Modelli AI» (`ai_providers`, `ai_tasks`, `ai_prices`, `ai_usage`) cut
down to what one task in one space needs.

`ai_model_config` is **one row at most** (`singleton`, unique): the provider, the model, the
key (sealed with `space_settings.sealing`, never returned by a read), the daily spend cap and
the two prices a cap needs, and the switch. The model is **off by default** and nothing turns
it on by itself: `enabled` needs everything else to be set and the privacy notice
acknowledged by the person who switches it on (`ck_ai_model_config_enabled_complete`).

`ai_model_usage` is one row per call. A call **reserves its estimated cost before the network**
(`status` `reserved`, counted in the day's total) and settles it to the real tokens after
(`done`), or marks it `failed` when the provider never answered with a response (a failed call
costs nothing and its hold is released from the total).
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

PROVIDERS: tuple[str, ...] = ("anthropic", "openai")
USAGE_TASKS: tuple[str, ...] = ("read_document", "test")
USAGE_STATUSES: tuple[str, ...] = ("reserved", "done", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AiModelConfig(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_model_config"

    singleton: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"), unique=True
    )
    provider: Mapped[str | None] = mapped_column(String(20), default=None)
    model: Mapped[str | None] = mapped_column(String(100), default=None)
    api_key_sealed: Mapped[str | None] = mapped_column(Text, default=None)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    daily_cap_eur: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), default=None)
    input_eur_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), default=None)
    output_eur_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), default=None)
    privacy_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    privacy_acknowledged_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id"), default=None
    )
    # The second switch the staff design asks for before the model reads a payslip's figures
    # (§ 2, REB-1045): off by default, and meaningful only while the model itself is on.
    read_payslips: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint("singleton", name="ck_ai_model_config_singleton"),
        CheckConstraint(
            f"provider IS NULL OR {_in('provider', PROVIDERS)}", name="ck_ai_model_config_provider"
        ),
        CheckConstraint(
            "daily_cap_eur IS NULL OR daily_cap_eur >= 0", name="ck_ai_model_config_cap"
        ),
        CheckConstraint(
            "NOT enabled OR (provider IS NOT NULL AND model IS NOT NULL "
            "AND api_key_sealed IS NOT NULL AND daily_cap_eur IS NOT NULL "
            "AND input_eur_per_mtok IS NOT NULL AND output_eur_per_mtok IS NOT NULL "
            "AND privacy_acknowledged_at IS NOT NULL)",
            name="ck_ai_model_config_enabled_complete",
        ),
    )


class AiModelUsage(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_model_usage"

    task: Mapped[str] = mapped_column(String(20), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="reserved")
    # The item a read was for. Not a foreign key: a usage row is an audit record of what was
    # spent, it outlives what it was spent on, and a table that reaches `inbox_items` would owe
    # the entity scope a policy it has no use for.
    inbox_item_id: Mapped[UUID | None] = mapped_column(default=None)
    estimated_cost_eur: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    cost_eur: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        CheckConstraint(_in("task", USAGE_TASKS), name="ck_ai_model_usage_task"),
        CheckConstraint(_in("status", USAGE_STATUSES), name="ck_ai_model_usage_status"),
        Index("ix_ai_model_usage_day", "day"),
    )
