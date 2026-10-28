"""A real Postgres per session, brought to `head` by this package's own migrations.

Not `create_all`: the schema the tests run against is the one the migrations produce,
so a table the model declares and the migration forgets is a failing test here rather
than a surprise on the server.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.db import create_engine_from_settings, session_factory


def settings_for(url: str) -> Settings:
    return Settings(database_url=url, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def hub_engine(hub_postgres: Any) -> Iterator[Engine]:
    """A clone of the worker's template database (`projects/hub/conftest.py`, REB-579),
    brought to head by the migrations once per worker."""
    engine = create_engine_from_settings(settings_for(hub_postgres.clone("core")))
    yield engine
    engine.dispose()


@pytest.fixture
def hub_session(hub_engine: Engine) -> Iterator[Session]:
    """Committed writes, wiped after each test: `SignupService.subscribe` commits on its
    own, so a rolled-back outer transaction would not isolate anything."""
    session = session_factory(hub_engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.execute(text("DELETE FROM signups"))
        session.commit()
        session.close()


@pytest.fixture
def clean_hub(hub_session: Session) -> Iterator[Session]:
    """A session over the worker's database that leaves it empty of people, the way the
    cards' tests need it: the card batches first (they point at the admin and the people
    the rest deletes), the source switches off again, then everything a card test makes."""
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("UPDATE ai_sources SET enabled = false, updated_by = NULL"))
    for table in (
        "card_batches",
        "admin_actions",
        "ai_usage",
        "comments",
        "matches",
        "companies",
        "freelancers",
        "users",
    ):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()
