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
    from fakes.hand_built_invoices import install

    install()
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


def _legal_entity_scope_statements() -> list[str]:
    """`statements()` of revisions 0068 (REB-898), 0070, 0073 to 0076, 0078 to 0081, 0084, 0086,
    0088, 0091, 0094, 0095, 0096, 0098, 0101, 0102, 0103, 0104, 0105, 0106, 0108, 0109, 0110
    and 0125, loaded from the files: a
    revision is a script in `versions/`, not a module on the path. It carries the final functions,
    the policies, `ENABLE`/`FORCE` and the hardening of the definer functions, so the template
    and production cannot disagree on a predicate; the earlier revisions' own versions of them are
    replaced by it."""
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).resolve().parent / "packages/core/migrations/versions"
    # REB-969: those revisions speak of `customers`; the revision that renamed the table
    # says the same statements in the new names through its `rewrite()`.
    (renaming,) = versions.glob("*_organizations.py")
    renaming_spec = importlib.util.spec_from_file_location(
        "pigrocrm_migration_organizations", renaming
    )
    assert renaming_spec is not None and renaming_spec.loader is not None
    renaming_module = importlib.util.module_from_spec(renaming_spec)
    renaming_spec.loader.exec_module(renaming_module)
    # REB-993: the same for the table that `costs` became; 0086 says the statements of the
    # revisions before it in the new names through its `rewrite()`, after the one above.
    (purchasing,) = versions.glob("*_purchases.py")
    purchasing_spec = importlib.util.spec_from_file_location(
        "pigrocrm_migration_purchases", purchasing
    )
    assert purchasing_spec is not None and purchasing_spec.loader is not None
    purchasing_module = importlib.util.module_from_spec(purchasing_spec)
    purchasing_spec.loader.exec_module(purchasing_module)
    out: list[str] = []
    # 0068 carries the final functions and policies; later revisions that add a table add
    # their own policy statements after it.
    for name, file in (
        ("pigrocrm_migration_0068", "0068_english_names.py"),
        ("pigrocrm_migration_0070", "0070_deal_value_lines.py"),
        ("pigrocrm_migration_0073", "0073_contact_points.py"),
        ("pigrocrm_migration_0074", "0074_inbox_items.py"),
        ("pigrocrm_migration_0075", "0075_tax_year_readings.py"),
        ("pigrocrm_migration_0076", "0076_due_items.py"),
        ("pigrocrm_migration_0078", "0078_organization_roles.py"),
        ("pigrocrm_migration_0079", "0079_staff_members.py"),
        ("pigrocrm_migration_0080", "0080_inbox_classification.py"),
        ("pigrocrm_migration_0081", "0081_collaborators.py"),
        ("pigrocrm_migration_0084", "0084_bank_accounts_statements_movements.py"),
        ("pigrocrm_migration_0086", "0086_purchases.py"),
        ("pigrocrm_migration_0088", "0088_declared_tax_figures.py"),
        ("pigrocrm_migration_0091", "0091_staff_payroll_estimate.py"),
        ("pigrocrm_migration_0094", "0094_accountant_exports.py"),
        ("pigrocrm_migration_0095", "0095_engagement_monthly_reports.py"),
        ("pigrocrm_migration_0096", "0096_iva_settlements.py"),
        ("pigrocrm_migration_0098", "0098_recurring_expenses.py"),
        ("pigrocrm_migration_0101", "0101_iva_period_locks.py"),
        ("pigrocrm_migration_0102", "0102_movement_matches.py"),
        ("pigrocrm_migration_0103", "0103_payslips_from_the_inbox.py"),
        ("pigrocrm_migration_0104", "0104_export_share_links_and_drive_writes.py"),
        ("pigrocrm_migration_0105", "0105_expense_notes.py"),
        ("pigrocrm_migration_0106", "0106_export_readiness_and_payroll.py"),
        ("pigrocrm_migration_0108", "0108_tax_advance_methods.py"),
        ("pigrocrm_migration_0109", "0109_tax_payment_plans.py"),
        ("pigrocrm_migration_0110", "0110_tax_credits_and_f24_groups.py"),
        ("pigrocrm_migration_0119", "0119_tax_losses.py"),
        ("pigrocrm_migration_0125", "0125_legal_entity_sites.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, versions / file)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out.extend(
            purchasing_module.rewrite(renaming_module.rewrite(statement))
            for statement in module.statements()
        )
    return out


# Test-only DDL, never a migration: a test that builds an issued invoice as a row gets its due item
# from `fakes/hand_built_invoices.py`, and the many tests that clean up by deleting their invoices
# or contracts by hand would otherwise leave the item behind, with the forecasts of a draft or a
# contract (REB-997), and block the deletion of its organization (REB-968). Production never
# deletes an issued invoice.
_DROP_INVOICE_DUE_ITEMS_SQL = """
CREATE FUNCTION test_drop_invoice_due_items() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM due_item_settlements WHERE due_item_id IN (
        SELECT id FROM due_items WHERE source_kind = 'sales_invoice' AND source_id = OLD.id);
    DELETE FROM due_items WHERE source_kind IN ('sales_invoice', 'sales_draft')
        AND source_id = OLD.id;
    RETURN OLD;
END $$;
CREATE TRIGGER test_drop_invoice_due_items AFTER DELETE ON invoices
    FOR EACH ROW EXECUTE FUNCTION test_drop_invoice_due_items();
CREATE FUNCTION test_drop_contract_due_items() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM due_items WHERE source_kind = 'contract_occurrence' AND source_id = OLD.id;
    RETURN OLD;
END $$;
CREATE TRIGGER test_drop_contract_due_items AFTER DELETE ON contracts
    FOR EACH ROW EXECUTE FUNCTION test_drop_contract_due_items();
"""


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
            # The row-level policies of 0048 (REB-633) as 0068 leaves them (REB-898, with
            # 0049's fast path), the other DDL `create_all` cannot express: read from the
            # revisions themselves, so the template and production cannot disagree on a
            # predicate. The suite's user is a superuser, which
            # Postgres keeps outside every policy, so no existing test sees them; the
            # scope tests connect as the application role the boot creates and do.
            for statement in _legal_entity_scope_statements():
                connection.execute(text(statement))
        with engine.begin() as connection:
            connection.execute(text(_DROP_INVOICE_DUE_ITEMS_SQL))
        # A template must have no session connected while it is copied.
        engine.dispose()

    return postgres_per_worker.project(_TEMPLATE, build)
