"""The walk every command that visits more than one installation shares.

The root installation is no registry row, so a walk reads the registry for the spaces and
puts the root in front of them. Every line a command prints about a space starts with its
label, and a failure is reported as the exception's type, never its text: a psycopg error
can carry the database URL, password included, and these lines are appended to a file on
the host.
"""

import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.engine import URL

from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.reminders.jobs import JobClock
from pigrocrm.core.reminders.run import ROOT_LABEL
from pigrocrm.core.tenants import Tenant, ensure_tenants_database, space_base_settings
from pigrocrm.core.tenants.database import tenant_database_url


@dataclass(frozen=True)
class SpaceTarget:
    """One installation to visit."""

    label: str  # what a log line starts with: `ROOT_LABEL` or the space's slug
    slug: str | None  # the registry slug, `None` for the root
    db_name: str | None  # the registry's database name, `None` for the root
    url: str | URL  # the application role's URL
    owner_email: str | None  # the registry's owner, `None` for the root
    public_url: str  # the base of every link a mail or a notification carries


@dataclass(frozen=True)
class Walk:
    targets: list[SpaceTarget] = field(default_factory=list)
    # Whether the registry was asked for and answered.
    registry_read: bool = False
    # The exception type when it was asked for and could not be read.
    unreadable: str | None = None


def read_spaces(settings: Settings, *, slug: str | None = None, include_root: bool = True) -> Walk:
    """The installations a command visits, the root first.

    `slug` narrows to one of them: `ROOT_LABEL` is the root alone and does not open the
    registry, any other name is a registry space. That name only: `root` is reserved
    against signups, so it can never be a space, while `PIGROCRM_ROOT_SLUG` could still
    name a registry row created before the root took it, and `--slug` with it goes on
    meaning that row. An unreachable registry is not an exception: the root is not in it
    and is still visited, so the caller prints its own line from `Walk.unreadable`.
    """
    root_only = slug == ROOT_LABEL
    targets: list[SpaceTarget] = []
    if include_root and (slug is None or root_only):
        # `rstrip`: with no root slug this is `public_url` untouched, and a trailing
        # slash on it would put `//app` in every link.
        root_url = space_base_settings(settings, settings.root_slug or None).public_url
        targets.append(
            SpaceTarget(ROOT_LABEL, None, None, settings.database_url, None, root_url.rstrip("/"))
        )
    if root_only:
        return Walk(targets)
    try:
        registry = ensure_tenants_database(settings)
        try:
            with session_factory(registry)() as session:
                targets.extend(
                    SpaceTarget(
                        row.slug,
                        row.slug,
                        row.db_name,
                        tenant_database_url(settings, row.db_name),
                        row.owner_email,
                        space_base_settings(settings, row.slug).public_url,
                    )
                    for row in session.scalars(select(Tenant).order_by(Tenant.created_at)).all()
                    if slug is None or row.slug == slug
                )
        finally:
            registry.dispose()
    except Exception as exc:  # noqa: BLE001 - a cron line, never a traceback
        return Walk(targets, unreadable=type(exc).__name__)
    return Walk(targets, registry_read=True)


def report_unreadable(walk: Walk, *, prefix: str = "") -> bool:
    """The one line an unreadable registry gets, on `stderr`. Whether it printed."""
    if walk.unreadable is None:
        return False
    print(f"{prefix}space registry unreachable ({walk.unreadable})", file=sys.stderr)
    return True


@contextmanager
def disposed(engine: Engine) -> Iterator[Engine]:
    """An engine of this command's own, disposed when its work is done."""
    try:
        yield engine
    finally:
        engine.dispose()


def space_engine(url: str | URL) -> AbstractContextManager[Engine]:
    """An engine on `url` for one space, disposed before the walk goes on to the next."""
    return disposed(create_engine(url, future=True))


def record_job(
    engine: Engine, job: str, label: str, failure: str | None, *, quiet: bool = False
) -> bool:
    """Writes the space's clock for `job` (REB-764): a success when `failure` is `None`,
    otherwise a failed attempt with that text. In a session and a transaction of its own,
    after the command's own work has committed or rolled back, so it can neither be undone
    by a failure nor undo anything.

    `True` when it is written. A success that cannot be written is said on `stderr` with
    the exception's type, because a job whose clock stops moving reads as late and the log
    should say why; `quiet` is for a failure that could not be written either, which is
    the same space answering the same way and already has its own line. Never raises: the
    clock is an account of the run and the run must not depend on it."""
    try:
        with session_factory(engine)() as session:
            clock = JobClock(session)
            if failure is None:
                clock.succeeded(job)
            else:
                clock.failed(job, failure)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - the clock must not stop the walk
        if not quiet:
            print(f"{label}: clock not written ({type(exc).__name__})", file=sys.stderr)
        return False
    return True
