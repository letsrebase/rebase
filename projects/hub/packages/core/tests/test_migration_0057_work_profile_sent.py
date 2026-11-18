"""0057 (REB-1131): `campaigns.action` takes a seventh value, `work_profile_sent`. The check
names it, the downgrade turns such a campaign back into `signed_in`, and the migration comes
and goes."""

from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from rebase_core.migrate import INI_PATH, upgrade_to_head

_CHECK = text(
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_campaigns_action'"
)


def test_the_check_names_the_seventh_action_and_the_migration_comes_and_goes(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert "work_profile_sent" in connection.execute(_CHECK).scalar_one()
        command.downgrade(config, "0056")
        with engine.connect() as connection:
            assert "work_profile_sent" not in connection.execute(_CHECK).scalar_one()
        command.upgrade(config, "0057")
        with engine.connect() as connection:
            definition = connection.execute(_CHECK).scalar_one()
        assert "work_profile_sent" in definition and "'click'" in definition
        engine.dispose()
