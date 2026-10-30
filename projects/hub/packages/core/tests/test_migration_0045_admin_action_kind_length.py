"""0045 (REB-862): `admin_actions.kind` is 40 wide, so `anonymous_cv_downloaded` is kept
whole instead of cut to its first 20 characters."""

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, upgrade_to_head


def test_revision_follows_the_anonymous_cvs() -> None:
    revision = ScriptDirectory.from_config(Config(str(INI_PATH))).get_revision("0045")
    assert revision is not None and revision.down_revision == "0044"


def test_upgrade_widens_the_kind_and_matches_the_models(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            width = connection.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_name = 'admin_actions' AND column_name = 'kind'"
                )
            ).scalar_one()
            assert width == 40
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
