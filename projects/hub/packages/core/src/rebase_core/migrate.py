"""Runs this package's Alembic history against a URL, from Python.

Used by the test fixtures and by anything that must bring a database to `head` without
shelling out: the API container does it with the `alembic` command at start-up, the
tests do it here so the schema they run against is the one the migrations produce and
not one `create_all` invented beside them.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

INI_PATH = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def upgrade_to_head(database_url: str) -> None:
    config = Config(str(INI_PATH))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def head_revision() -> str:
    """The newest revision this package ships, as the version table will read it."""
    head = ScriptDirectory.from_config(Config(str(INI_PATH))).get_current_head()
    if head is None:
        raise RuntimeError("the migrations directory has no head revision")
    return head
