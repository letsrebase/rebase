from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import pigrocrm.core.models_registry  # noqa: F401  (populates Base.metadata)
from pigrocrm.core.config import get_settings
from pigrocrm.core.db import Base

config = context.config

# `alembic.ini`'s own `sqlalchemy.url` line is Alembic's standard mechanism for
# pointing a migration at a specific database -- documented in that file's own
# comments -- and it must actually work: an operator who edits it and gets no effect
# and no warning would silently migrate the wrong database. `get_settings()` is only
# the fallback for the common case where the config carries no URL at all, or still
# carries the placeholder `alembic init` wrote into a fresh `alembic.ini`. An
# explicitly configured URL -- whether from a hand-edited ini file, or a caller's own
# `Config.set_main_option("sqlalchemy.url", ...)` before invoking Alembic
# programmatically -- always wins.
_PLACEHOLDER_URL = "driver://user:pass@localhost/dbname"
_configured_url = config.get_main_option("sqlalchemy.url", "")
if not _configured_url or _configured_url == _PLACEHOLDER_URL:
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    # `disable_existing_loggers` defaults to True, which is right for `alembic upgrade`
    # from a shell and wrong everywhere else: `migrate_to_head` runs this file inside
    # the API process (a signup provisions in-process, and the boot's
    # `ensure-space-defaults` reaches every space the same way), and the first run
    # after boot disabled every logger the ini does not name -- access lines and
    # application warnings stopped. The ini's own formatters and levels still apply;
    # only the blanket disable is dropped. REB-190, caught by CI the day the route
    # matrix shifted the xdist distribution and test_mail.py landed on the worker
    # that had just migrated. Pinned by
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
