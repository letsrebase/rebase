"""No test in this project may open a socket to anything but this machine.

A suite that skips when credentials are absent proves nothing: it is green on a
developer's laptop, green in CI, and has never executed the code it claims to cover.
Slice 5's seam is at the HTTP boundary (`core/gmail/transport.py`) precisely so that
everything above it -- URL building, the `q` string, RFC822 assembly, error
classification -- runs for real without a network. A socket opening during the suite
therefore means something bypassed the seam, and that is exactly the failure that must
not happen quietly.

So it is enforced rather than agreed. This file lives at the root of this project, not
in one test root's `conftest.py`, because the rule is about the *suite*: an autouse
fixture is scoped to the directory it is declared in, and a guard that covered
`packages/core/tests` while `apps/api/tests` and `apps/mcp/tests` went unwatched would
be a guarantee with two holes in it.

What is still allowed, and why:
  * the loopback interface, because `testcontainers` starts a real PostgreSQL and
    `psycopg` connects to it on a mapped localhost port -- the container *is* the
    system under test for every migration and repository test in this repository;
  * AF_UNIX sockets, because that is how the Docker daemon is reached on macOS, and a
    filesystem socket cannot leave the machine by construction.

Everything else raises `AssertionError` at the moment of the attempt, so the failure
names the test that made it instead of appearing later as a timeout.
"""

import socket
from typing import Any

import pytest
from sqlalchemy import text

_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"})

_REFUSAL = (
    "a test tried to reach {target!r}. This suite never touches the network: Gmail is "
    "driven through the GmailTransport seam with FakeGmail, Drive through FakeDrive, "
    "and PostgreSQL through a local testcontainer. If a new dependency needs a socket, "
    "it needs a seam first."
)

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo


def _is_local(address: object) -> bool:
    """`True` for the loopback interface and for anything that is not an IP address.

    A non-tuple address is an AF_UNIX path (or an AF_BLUETOOTH/AF_NETLINK address);
    none of those can reach another machine, so there is nothing for this guard to
    protect against and refusing them would only break the Docker client.
    """
    if not isinstance(address, tuple) or not address:
        return True
    host = address[0]
    if not isinstance(host, str):
        return False
    return (
        host in _LOOPBACK_NAMES
        or host.startswith("127.")
        # IPv4-mapped IPv6, which is what `getaddrinfo("localhost")` can hand back on
        # a dual-stack host.
        or host.startswith("::ffff:127.")
    )


def _guarded_connect(self: socket.socket, address: Any) -> None:
    if _is_local(address):
        _original_connect(self, address)
        return
    raise AssertionError(_REFUSAL.format(target=address))


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    if _is_local(address):
        return _original_connect_ex(self, address)
    raise AssertionError(_REFUSAL.format(target=address))


def _guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    """Name resolution is guarded too, and not only `connect`.

    Two reasons. A DNS lookup is itself traffic that leaves the machine, so a test
    that only resolves a hostname has already broken the rule. And it makes the
    refusal *deterministic*: without this, a sandboxed machine with no DNS answers a
    lookup for a public host with `gaierror` before `connect` is ever reached, so the
    guard's own test would pass for the wrong reason on one machine and fail on
    another.
    """
    if host is None or _is_local((host, port)):
        return _original_getaddrinfo(host, port, *args, **kwargs)
    raise AssertionError(_REFUSAL.format(target=host))


def pytest_configure(config: pytest.Config) -> None:
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]


def pytest_unconfigure(config: pytest.Config) -> None:
    socket.socket.connect = _original_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _original_getaddrinfo  # type: ignore[assignment]


# --- this project's databases on the worker's PostgreSQL (`conftest.py` at the repository
# root, REB-579): the schema `create_all` produces, once per worker, into a template that
# each test root clones.

_TEMPLATE = "pigrocrm_template"


@pytest.fixture(scope="session")
def pigrocrm_postgres(postgres_per_worker: Any) -> Any:
    """The CRM's template database, built once per worker, and clones of it on demand.

    `CREATE EXTENSION` runs before `create_all`, and the order is load-bearing: four models
    declare GIN indexes with `gin_trgm_ops`, and `create_all` fails outright with `operator
    class "gin_trgm_ops" does not exist` without `pg_trgm`; `RateCard`'s exclusion
    constraint needs `btree_gist` the same way (REB-358). The trigger DDL runs after
    `create_all`, since it needs the tables to exist: `WORK_UNIT_TRIGGER_SQL` and
    `CONTRACT_EXPENSE_TRIGGER_SQL` are the exact text the migrations run (`triggers.py` says
    why they are imported rather than copied), the only DDL of those tables `create_all`
    cannot express (REB-359, REB-360). A clone carries all of it: a template copy is the
    files of the database, extensions included.

    One server per worker means the three roots on a worker now share what a test derives
    from its engine URL by swapping the database name: the `pigrocrm_tenants` registry and
    the `pigro_t_<slug>` spaces `test_tenants.py` and `test_tenants_api.py` provision. A
    worker runs its files one after the other, so nothing overlaps; what changed is that a
    row a core module fails to clean up is now visible to the api root's `== []`, where it
    used to vanish with the core root's own server.
    """
    from pigrocrm.core.config import Settings
    from pigrocrm.core.contract_expenses.triggers import CONTRACT_EXPENSE_TRIGGER_SQL
    from pigrocrm.core.db import Base, create_engine_from_settings
    from pigrocrm.core.work_units.triggers import WORK_UNIT_TRIGGER_SQL

    def build(url: str) -> None:
        engine = create_engine_from_settings(Settings(database_url=url))
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        import pigrocrm.core.models_registry  # noqa: F401  (imports every model)

        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text(WORK_UNIT_TRIGGER_SQL))
            connection.execute(text(CONTRACT_EXPENSE_TRIGGER_SQL))
        # A template must have no session connected while it is copied.
        engine.dispose()

    return postgres_per_worker.project(_TEMPLATE, build)
