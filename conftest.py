"""One PostgreSQL per xdist worker, for every Python test root in this repository.

Until REB-579 each test root (`packages/core`, `apps/api`, `apps/mcp` of each project)
started its own testcontainers PostgreSQL in its own session-scoped fixture and built its
schema in it, and every migration test started one more of its own: a worker that touched
all three roots of a project paid three container starts and three schema builds before its
first assertion, 12 to 17s each on the hosted runner, which the `--durations` tables showed
at the top of every shard. This file is loaded by pytest for every test root (the rootdir is
the repository, `pyproject.toml`), and gives each worker one server: session scope under
xdist is per worker process, so the workers still share nothing.

What a project does with it is its own rootdir conftest's business: `projects/<name>/
conftest.py` builds that project's schema once into a template database (`create_all` for
the CRM, the migrations for the hub) and hands each of its test roots a clone of it, a file
copy inside the server rather than a container and a schema build. A migration test asks
for an empty database instead of a container of its own.

The server runs with `fsync`, `synchronous_commit` and `full_page_writes` off: a test
database has nothing to protect from a crash, and every commit, vacuum and bulk load is
cheaper without the disk waits. None of the three touches what the planner reads.

Two things keep the containers from outliving their run (REB-854). On a local Docker
daemon the port is published on loopback, because Docker's own firewall rules accept a
port published on every interface ahead of the host's firewall, so a leaked container
was a `test`/`test` PostgreSQL open to the internet. And each container carries the
identity of the pytest process that started it, so the next session removes the ones a
killed run left behind: Ryuk would, but a local run often turns it off
(`TESTCONTAINERS_RYUK_DISABLED`) because it flakes on a busy machine.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

import docker.errors
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.docker_client import ConnectionMode

_IMAGE = "postgres:17-alpine"
_SERVER_SETTINGS = "postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off"
_OWNER_LABEL = "letsrebase.tests.owner"


def _process_identity(pid: int) -> str | None:
    """The boot, PID namespace, pid and start time of a process; None off Linux or once gone.

    The start time is what tells a live owner from a later process that was given its pid,
    and the boot and namespace are what make a pid comparable at all: a container started
    from another machine sharing the daemon, or from inside a sandbox, is never judged.
    """
    try:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        namespace = os.readlink("/proc/self/ns/pid")
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    start = stat.rsplit(")", 1)[1].split()[19]
    return f"{boot}:{namespace}:{pid}:{start}"


def _remove_orphans(container: PostgresContainer) -> None:
    """Remove the worker containers whose pytest process, on this machine and namespace, is gone."""
    mine = _process_identity(os.getpid())
    if mine is None:
        return
    here = mine.rsplit(":", 2)[0]
    client = container.get_docker_client().client
    for leftover in client.containers.list(all=True, filters={"label": _OWNER_LABEL}):
        owner = leftover.labels.get(_OWNER_LABEL, "")
        where, _, pid = owner.rsplit(":", 1)[0].rpartition(":")
        if where != here or not pid.isdigit() or _process_identity(int(pid)) == owner:
            continue
        with suppress(docker.errors.APIError):  # another worker got to it first
            leftover.remove(force=True, v=True)


def _worker_container() -> tuple[PostgresContainer, str | None]:
    """The container, and the host to reach it on when that is not testcontainers' default.

    Bound to loopback, the port answers on 127.0.0.1 only, while `localhost` may resolve to
    `::1` first. With `localhost` in the URL the hub's
    `test_regenerate_stale_writes_the_limit_after_the_response` failed on every run and passed
    with `127.0.0.1`, so the URL names the address and nothing depends on how it resolves.
    """
    container = PostgresContainer(_IMAGE, driver="psycopg").with_command(_SERVER_SETTINGS)
    owner = _process_identity(os.getpid())
    if owner is not None:
        container.with_kwargs(labels={_OWNER_LABEL: owner})
    client = container.get_docker_client()
    if client.get_connection_mode() is ConnectionMode.docker_host and client.host() in {
        "localhost",
        "127.0.0.1",
    }:
        # docker-py takes (address, port) for a binding; no port means Docker picks one.
        container.ports[str(container.port)] = ("127.0.0.1", None)  # type: ignore[assignment]
        return container, "127.0.0.1"
    return container, None


class PostgresPerWorker:
    """The worker's server: databases are made from a template or empty, by name."""

    def __init__(self, admin_url: str) -> None:
        self._admin_url = admin_url
        self._admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    def url_for(self, database: str) -> str:
        url = make_url(self._admin_url).set(database=database)
        return url.render_as_string(hide_password=False)

    def fresh(self, database: str) -> str:
        """An empty database of that name, replacing one of the same name if it exists."""
        with self._admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}"'))
        return self.url_for(database)

    def clone(self, database: str, template: str) -> str:
        """A copy of `template`: its extensions, tables, triggers and rows."""
        with self._admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
            connection.execute(text(f'CREATE DATABASE "{database}" TEMPLATE "{template}"'))
        return self.url_for(database)

    def project(self, template: str, build: Callable[[str], None]) -> ProjectDatabases:
        """A project's handle: `build(url)` fills the template the first time a root asks
        for a clone, and must leave no session open on it, since PostgreSQL refuses to
        copy a database somebody is connected to. Lazy, so a worker that runs only
        migration tests, which ask for empty databases, never builds a schema it does not
        use. Called from a session-scoped fixture, so at most once per worker."""
        return ProjectDatabases(self, template, build)

    def drop(self, database: str) -> None:
        """`WITH (FORCE)`: a session a test forgot to close does not keep the files."""
        with self._admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))

    def close(self) -> None:
        self._admin.dispose()


class ProjectDatabases:
    """A project's view of the worker's server: clones of its template, or empty ones."""

    def __init__(
        self, server: PostgresPerWorker, template: str, build: Callable[[str], None]
    ) -> None:
        self._server = server
        self._template = template
        self._build = build
        self._built = False
        # Two projects on one worker (a bare `uv run pytest` at the root runs both) each
        # have an `api` root; the template's own name keeps their clones apart on the
        # shared server. The prefix is also the namespace of whatever a project derives
        # from its engine URL by swapping the database name (the CRM's `pigrocrm_tenants`
        # registry and its `pigro_t_<slug>` spaces), so a root's name must not be one of
        # those: `clone` drops a database of that name before creating it.
        self._prefix = template.removesuffix("_template")

    def clone(self, database: str) -> str:
        if not self._built:
            self._build(self._server.fresh(self._template))
            self._built = True
        return self._server.clone(f"{self._prefix}_{database}", self._template)

    @contextmanager
    def fresh_container(self) -> Iterator[_FreshDatabase]:
        """What a migration test used to get from `PostgresContainer(...)`: an object with
        `get_connection_url()`, for an empty database of its own on the worker's server,
        dropped on the way out as the container used to be."""
        name = f"fresh_{uuid4().hex[:12]}"
        try:
            yield _FreshDatabase(self._server.fresh(name))
        finally:
            self._server.drop(name)


class _FreshDatabase:
    def __init__(self, url: str) -> None:
        self._url = url

    def get_connection_url(self) -> str:
        return self._url


@pytest.fixture(scope="session")
def postgres_per_worker() -> Iterator[PostgresPerWorker]:
    """One PostgreSQL for this worker process, for every test root it runs."""
    container, host = _worker_container()
    _remove_orphans(container)
    with container:
        server = PostgresPerWorker(container.get_connection_url(host=host))
        try:
            yield server
        finally:
            server.close()
