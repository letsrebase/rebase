from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import rebase_core.models  # noqa: F401  (populates Base.metadata)
from rebase_core.config import get_settings
from rebase_core.db import Base

config = context.config

# `alembic.ini`'s own `sqlalchemy.url` wins when somebody set it; the settings are the
# fallback for the ordinary case where the ini still carries `alembic init`'s
# placeholder. Same rule as PigroCRM's env.py, for the same reason: an operator who
# edits the ini and gets no effect would silently migrate the wrong database.
_PLACEHOLDER_URL = "driver://user:pass@localhost/dbname"
_configured_url = config.get_main_option("sqlalchemy.url", "")
if not _configured_url or _configured_url == _PLACEHOLDER_URL:
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    # `disable_existing_loggers` defaults to True, which is right for `alembic
    # upgrade` from a shell and wrong inside the API process: `upgrade_to_head` runs
    # this file in-process (a signup provisions this way), and the default disables
    # every logger the ini does not name -- including other projects' loggers alive
    # on the same test worker, which is how a hub migration left `pigrocrm.core.mail`
    # silent and its `caplog` assertions empty under xdist. The pigrocrm `env.py` was
    # fixed the same way (REB-190); this is its twin (REB-660). Pinned by
    # `test_a_migration_leaves_the_host_process_loggers_emitting`.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
