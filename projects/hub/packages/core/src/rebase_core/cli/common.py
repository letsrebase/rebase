"""What every command group shares: the shape of a command and the way one opens the database.

A command is the arguments it takes (`add_arguments`) and what it runs (`run`). The package's
`main` imports a group's module only when one of its commands is chosen, so a command pays
for the imports of its own work and no other (REB-892): `rebase campaigns-tick` never loads
the model providers' libraries.
"""

import argparse
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from rebase_core.config import Settings
from rebase_core.db import create_engine_from_settings, session_factory


@dataclass(frozen=True)
class Command:
    add_arguments: Callable[[argparse.ArgumentParser], None]
    run: Callable[[argparse.Namespace], int]


def no_arguments(parser: argparse.ArgumentParser) -> None:
    """For a command that takes none."""


def open_session(settings: Settings) -> sessionmaker[Session]:
    """A session factory on a new engine for `settings`' database. A command runs once, so the
    engine goes with the process."""
    return session_factory(create_engine_from_settings(settings))
