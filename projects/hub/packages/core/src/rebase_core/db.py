"""The declarative base, the two mixins every table shares, and the engine helpers."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from uuid import UUID

import uuid_utils
from sqlalchemy import DateTime, Engine, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from rebase_core.config import Settings


def uuid7() -> UUID:
    """UUIDv7: random enough to be unguessable, ordered enough to index well."""
    return UUID(str(uuid_utils.uuid7()))


class Base(DeclarativeBase):
    """The hub's metadata, and only the hub's: the CRM's Alembic never sees it, and this
    project's Alembic never sees the CRM's."""


class PrimaryKeyMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


def create_engine_from_settings(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


# A session for work that runs after an API response (REB-387's webhook, REB-510's card
# write): the API's `get_session_opener` answers one, and a background task opens its
# own session with it rather than borrow the request's, which is closed by then.
SessionOpener = Callable[[], AbstractContextManager[Session]]
