from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class PipelineStage(Base, PrimaryKeyMixin, TimestampMixin):
    """`code` is the stable identity for the seeded default stages; `name` is the
    visible label a user is free to rename. `seed_defaults` deduplicates on `code`,
    never on `name` -- the same reasoning that makes `type` exist: logic must not
    depend on a string the user can change.

    Stages a user creates through `create` have no `code` (`None`) and are never
    touched by `seed_defaults`. The unique index below is safe for that: Postgres
    treats every `NULL` as distinct from every other value under a unique index, so
    any number of user-created, code-less stages can coexist.
    """

    __tablename__ = "pipeline_stages"
    __table_args__ = (Index("uq_pipeline_stage_code", "code", unique=True),)

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    default_probability: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    type: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    code: Mapped[str | None] = mapped_column(String(30), default=None)
