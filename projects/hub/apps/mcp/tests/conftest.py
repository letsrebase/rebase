from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.config import Settings
from rebase_core.db import create_engine_from_settings, session_factory


@pytest.fixture(scope="session")
def mcp_engine(hub_postgres: Any) -> Iterator[Engine]:
    """A clone of the worker's template database (`projects/hub/conftest.py`, REB-579)."""
    url = hub_postgres.clone("mcp")
    engine = create_engine_from_settings(Settings(database_url=url, _env_file=None))  # type: ignore[call-arg]
    yield engine
    engine.dispose()


@pytest.fixture
def factory(mcp_engine: Engine) -> Iterator[sessionmaker[Session]]:
    made = session_factory(mcp_engine)
    yield made
    session = made()
    session.execute(text("DELETE FROM signups"))
    session.commit()
    session.close()
