"""The one list of tables a hub test may write, and the one way to empty them.

Five conftests used to wipe five different sets of tables, so a row one test left behind
(a login, a magic-link token, a session) turned up in the next test's first `select`.
The list is read from the models rather than typed, in an order where every table comes
before the tables it points at, so a new table is wiped from the day it exists.

What stays is the configuration the migrations seed and the tests edit in place with their
own resets: the model providers, tasks, sources and prices of «Modelli AI», and the
referral settings row.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

import rebase_core.models  # noqa: F401  (registers every table on the metadata)
from rebase_core.db import Base

SEEDED_TABLES = frozenset(
    {"ai_providers", "ai_tasks", "ai_sources", "ai_prices", "referral_settings"}
)

# Children before parents, so a plain `DELETE` of each in turn never meets a foreign key.
HUB_TABLES: tuple[str, ...] = tuple(
    table.name for table in reversed(Base.metadata.sorted_tables) if table.name not in SEEDED_TABLES
)


def wipe_hub(session: Session) -> None:
    """Empties every `HUB_TABLES` table and commits. For the end of a test, after a
    rollback: the hub's own services commit, so an outer rolled-back transaction would
    isolate nothing."""
    for table in HUB_TABLES:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
