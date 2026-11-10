"""0051 (REB-911): the address's confirmation columns on `users`, null for everyone already
there, and gone again on the way down."""

from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from rebase_core.migrate import INI_PATH, upgrade_to_head

REVISION = "0051"
COLUMNS = {
    "email_confirmed_at",
    "confirmation_requested_at",
    "confirmation_sent_at",
    "confirmation_token_hash",
    "confirmation_expires_at",
}
QUERY = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'users' "
    "AND column_name LIKE ANY (ARRAY['email_confirmed%', 'confirmation_%'])"
)


def test_revision_follows_the_contact_points() -> None:
    parent = ScriptDirectory.from_config(Config(str(INI_PATH))).get_revision(REVISION).down_revision
    assert parent == "0050"


def test_the_columns_arrive_empty_and_leave_with_the_downgrade(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, first_name, last_name, role, active) "
                    "VALUES (:id, 'a@acme.it', 'A', 'B', 'member', true)"
                ),
                {"id": uuid4()},
            )
            assert set(connection.execute(QUERY).scalars()) == COLUMNS
            assert connection.execute(
                text("SELECT email_confirmed_at, confirmation_sent_at FROM users")
            ).one() == (None, None)
        command.downgrade(config, "0050")
        with engine.connect() as connection:
            assert set(connection.execute(QUERY).scalars()) == set()
        engine.dispose()
