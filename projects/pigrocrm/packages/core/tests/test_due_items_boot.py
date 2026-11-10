"""`pigrocrm ensure-space-defaults` gives the invoices the previous release wrote their due
items (REB-968, spec 2026-11-09, due items, § 1.1, «Rollout»): run for real against the
container, like `test_profile_at_boot.py`. The old API keeps serving while `migrate` runs, so
the invoices it writes meanwhile have no item until this step."""

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fakes.hand_built_invoices import suspended
from fakes.invoice_fixtures import unpaid_invoice
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from pigrocrm.core import cli
from pigrocrm.core.cli import spaces
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.tenants import (
    TenantService,
    TenantSignup,
    ensure_tenants_database,
)
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url

SLUG = "prova-fatture"


def _settings(db_engine: Engine) -> Settings:
    return Settings(
        database_url=db_engine.url.render_as_string(hide_password=False),
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def space(db_engine: Engine) -> Iterator[Engine]:
    """One provisioned space, dropped with its registry row on the way out."""
    settings = _settings(db_engine)
    registry = ensure_tenants_database(settings)
    with session_factory(registry)() as session:
        TenantService(session, settings).provision(
            TenantSignup(first_name="Ada", slug=SLUG, name="Ada", email=f"{SLUG}@studio.it")
        )
    url = tenant_database_url(settings, tenant_database_name(SLUG))
    engine = create_engine(url, future=True)
    try:
        yield engine
    finally:
        engine.dispose()
        with registry.begin() as connection:
            connection.execute(text("delete from tenants where slug = :s"), {"s": SLUG})
        drop_database(settings, url)
        registry.dispose()


def _write_previous_release_invoices(engine: Engine) -> None:
    today = today_local()
    with session_factory(engine)() as session, suspended():
        _legacy(session, due=today - dt.timedelta(days=10), total=Decimal("1000.00"))
        paid = _legacy(session, due=today - dt.timedelta(days=20), total=Decimal("250.00"))
        paid.payment_status = "paid"
        paid.payment_date = today - dt.timedelta(days=4)
        session.commit()


def _legacy(session: Session, **kwargs: object):  # type: ignore[no-untyped-def]
    return unpaid_invoice(session, **kwargs)  # type: ignore[arg-type]


def _items(engine: Engine) -> list[tuple[str, Decimal, int]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "select i.state, i.amount, "
                "(select count(*) from due_item_settlements s where s.due_item_id = i.id) "
                "from due_items i where i.source_kind = 'sales_invoice' order by i.amount"
            )
        ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _boot(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["ensure-space-defaults"]) == 0


def test_the_boot_gives_an_invoice_of_the_previous_release_its_item_once(
    db_engine: Engine,
    space: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(db_engine)
    _write_previous_release_invoices(space)
    assert _items(space) == []

    _boot(settings, monkeypatch)
    expected = [("settled", Decimal("250.00"), 1), ("expected", Decimal("1000.00"), 0)]
    assert _items(space) == expected
    assert f"{SLUG}: due items created for 2 invoices, repaired for 0" in capsys.readouterr().out

    _boot(settings, monkeypatch)
    assert _items(space) == expected
    assert f"{SLUG}: due items already in place" in capsys.readouterr().out


def test_a_space_that_fails_is_a_line_on_stderr_and_the_boot_goes_on(
    db_engine: Engine,
    space: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The boot's convention: one space's failure is a line naming the exception's type on
    stderr and never keeps the API down, so `ensure-space-defaults` still answers 0. The step
    that exists to be run on its own answers 1."""
    settings = _settings(db_engine)

    def refuse(session: Session) -> object:
        raise RuntimeError("secret detail")

    monkeypatch.setattr(spaces, "reconcile_sales_invoices", refuse)
    _boot(settings, monkeypatch)
    err = capsys.readouterr().err
    assert f"{SLUG}: due items not reconciled (RuntimeError)" in err
    assert "secret detail" not in err

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["reconcile-due-items", "--slug", SLUG]) == 1
    assert f"{SLUG}: due items not reconciled (RuntimeError)" in capsys.readouterr().err


def test_the_standalone_step_converges_an_invoice_the_old_release_wrote_after_the_boot(
    db_engine: Engine,
    space: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(db_engine)
    _write_previous_release_invoices(space)
    _boot(settings, monkeypatch)
    capsys.readouterr()
    # The old API, still serving, marks the open invoice paid after the boot step has run.
    with session_factory(space)() as session, suspended():
        session.execute(
            text(
                "update invoices set payment_status = 'paid', payment_date = :d "
                "where total = 1000.00"
            ),
            {"d": today_local()},
        )
        session.commit()
    assert [row[0] for row in _items(space)] == ["settled", "expected"]

    assert cli.main(["reconcile-due-items", "--slug", SLUG]) == 0
    assert f"{SLUG}: due items created for 0 invoices, repaired for 1" in capsys.readouterr().out
    assert [row[0] for row in _items(space)] == ["settled", "settled"]
    assert cli.main(["reconcile-due-items", "--slug", SLUG]) == 0
    assert f"{SLUG}: due items already in place" in capsys.readouterr().out
