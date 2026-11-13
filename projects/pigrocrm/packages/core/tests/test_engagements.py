"""What still reads a hub-era engagement through the registry (spec 2026-09-25 § 2.2, § 2.4):
the registry table `rebase_engagements`, the registry's retargeting when the freelancer folds
the customer «rebase» into another row of the same company, and `EngagementService.report`,
which reads a match's deal hours back with the invoices they sit on (the collaborators' days
report calls it). The hub no longer creates these rows (REB-1108): `_seed` writes them as a
space the door set up before.

Every test runs on the container for real: `TenantService.provision` creates the space's
database and migrates it exactly as the signup does. The `door` fixture empties the registry
and drops every space on the way out, the way `test_tenants_api.py`'s `_serving` does, on core
alone.
"""

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, inspect, select, text, update
from sqlalchemy.orm import Session

import pigrocrm.core.db.clock as db_clock
import pigrocrm.core.engagements as engagements_package
import pigrocrm.core.engagements.service as engagements_service
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.config import Settings
from pigrocrm.core.contracts.rate_card_service import RateCardService
from pigrocrm.core.contracts.schemas import (
    ContractCreate,
    ContractStateChange,
    RateCardCreate,
)
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.registry import EngagementRegistry
from pigrocrm.core.engagements.schemas import ReportDeal, ReportInvoice
from pigrocrm.core.engagements.service import EngagementService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.schemas import OrganizationCreate
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.tenants import TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.schemas import TimeEntryCreate
from pigrocrm.core.timetracking.service import TimeEntryService

ADA = "ada@studio.it"
DEAL_NAME = "Lettera n. 3/2026 · Backend developer per Acme S.r.l."
GONE = "Il deal di questa lettera è stato eliminato nello spazio."
REVERSED = "La data di fine precede quella di inizio."


def _settings_for(engine: Engine, *, public_url: str = "https://pigro.test") -> Settings:
    return Settings(
        database_url=engine.url.render_as_string(hide_password=False),
        public_url=public_url,
        _env_file=None,  # type: ignore[call-arg]
    )


@dataclass
class Door:
    settings: Settings
    registry: Engine

    def service(self) -> EngagementService:
        return EngagementService(self.registry, self.settings)


@pytest.fixture
def door(db_engine: Engine) -> Iterator[Door]:
    settings = _settings_for(db_engine)
    registry = ensure_tenants_database(settings)
    try:
        yield Door(settings, registry)
    finally:
        with registry.begin() as connection:
            slugs = connection.execute(text("SELECT slug FROM tenants")).scalars().all()
            connection.execute(text("DELETE FROM rebase_engagements"))
            connection.execute(text("DELETE FROM tenants"))
        registry.dispose()
        for slug in slugs:
            drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))


def _provision(door: Door, slug: str, email: str = ADA) -> UUID:
    """A space opened at the signup, before the door ever saw the address."""
    with session_factory(door.registry)() as registry:
        tenant = TenantService(registry, door.settings).provision(
            TenantSignup(first_name="Ada", slug=slug, name="Ada Lovelace", email=email)
        )
    return tenant.id


@contextmanager
def _space(door: Door, slug: str) -> Iterator[Session]:
    engine = create_engine(
        tenant_database_url(door.settings, tenant_database_name(slug)), future=True
    )
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _row(door: Door, match_id: UUID) -> RebaseEngagement | None:
    with session_factory(door.registry)() as registry:
        return registry.scalar(
            select(RebaseEngagement).where(RebaseEngagement.match_id == match_id)
        )


def _count(space: Session, model: type[Organization] | type[Deal], *where: Any) -> int:
    return space.scalar(select(func.count()).select_from(model).where(*where)) or 0


@dataclass(frozen=True)
class Seeded:
    """What a space the door set up for one match holds, as the registry row names it."""

    slug: str
    customer_id: UUID
    deal_id: UUID
    contract_id: UUID

    @property
    def deal_url(self) -> str:
        return f"https://pigro.test/{self.slug}/app/deal/{self.deal_id}"


def _seed(
    door: Door, match_id: UUID | None = None, *, slug: str = "ada-lovelace", email: str = ADA
) -> Seeded:
    """The space, the customer «rebase», the letter's deal and its active contract with a
    daily rate card, and the registry row naming them: what the hub's door wrote for a match
    before REB-1108, written here through the services so the report reads real rows."""
    match_id = match_id or uuid4()
    tenant_id = _provision(door, slug, email)
    with _space(door, slug) as space:
        customer = OrganizationService(space).create(
            OrganizationCreate(legal_name="rebase S.r.l.", vat_number="01234567890"),
            Actor.rebase(),
        )
        owner = UserRepository(space).get_by_email(email)
        assert owner is not None
        deal = DealService(space).create(
            DealCreate(
                name=DEAL_NAME,
                customer_id=customer.id,
                hourly_rate=Decimal("50.000000"),
                budgeted_hours=Decimal("320.00"),
                expected_close_date=date(2026, 12, 31),
                owner_id=owner.id,
            ),
            Actor.rebase(),
            rebase_ref=f"rebase:match={match_id}",
        )
        contracts = ContractService(space)
        contract = contracts.create(
            ContractCreate(
                customer_id=customer.id,
                title=DEAL_NAME,
                start=date(2026, 10, 1),
                end=date(2026, 12, 31),
                renewal_type="none",
                termination_notice_days=0,
                billing_cadence="monthly",
                expense_policy={"kind": "not_reimbursable"},
            ),
            Actor.rebase(),
        )
        contracts.set_state(contract.id, ContractStateChange(status="active"), Actor.rebase())
        RateCardService(space).create(
            contract.id,
            RateCardCreate(
                valid_from=date(2026, 10, 1),
                valid_to=date(2026, 12, 31),
                type="daily",
                amount=Decimal("400.00"),
                unit="day",
                delivery_period="one_off",
                expected_quantity=Decimal(40),
            ),
            Actor.rebase(),
        )
    with session_factory(door.registry)() as registry:
        registry.add(
            RebaseEngagement(
                match_id=match_id,
                tenant_id=tenant_id,
                space_created=True,
                customer_id=customer.id,
                deal_id=deal.id,
                contract_id=contract.id,
            )
        )
        registry.commit()
    return Seeded(slug, customer.id, deal.id, contract.id)


# --- the registry table (A2) ---------------------------------------------------------


def test_engagements_package_imports_and_exports_nothing() -> None:
    """The import trap of spec § 2.2: `tenants/database.py` imports
    `pigrocrm.core.engagements.models`, which runs this package's `__init__` first, and
    an import there of `service` would chain `service -> tenants.service ->
    tenants.database` back into a module still initialising.

    Read from the source, not from `dir()` of the package: Python binds every imported
    submodule onto its package for the life of the process, so `dir()` answers what
    this file and every module collected before it imported (this file imports
    `service` at the top), never what `__init__.py` itself does."""
    assert engagements_package.__file__ is not None
    tree = ast.parse(Path(engagements_package.__file__).read_text())
    assert [n for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom)] == []
    assert [n for n in tree.body if isinstance(n, ast.Assign | ast.AnnAssign)] == []


def test_registry_has_the_engagements_table(db_engine: Engine) -> None:
    engine = ensure_tenants_database(_settings_for(db_engine))
    try:
        assert inspect(engine).has_table("rebase_engagements")
    finally:
        engine.dispose()


def test_booting_against_a_registry_without_contract_id_adds_the_column(door: Door) -> None:
    with door.registry.begin() as connection:
        connection.execute(text("ALTER TABLE rebase_engagements DROP COLUMN contract_id"))

    again = ensure_tenants_database(door.settings)
    try:
        columns = {c["name"] for c in inspect(again).get_columns("rebase_engagements")}
        assert "contract_id" in columns
        # A second boot finds it there and changes nothing.
        ensure_tenants_database(door.settings).dispose()
    finally:
        again.dispose()


# --- the merge of the customer «rebase» points the registry row at the survivor --------

MERGER = Actor(id=None, type="user", role="admin")  # type: ignore[arg-type]


class _RegistryThatFailsToCommit(EngagementRegistry):
    def commit(self) -> None:
        raise RuntimeError("the registry went away")


def _survivor(door: Door, slug: str) -> UUID:
    """The same company typed a second time by the freelancer, in the space `slug`."""
    with _space(door, slug) as space:
        return (
            OrganizationService(space)
            .create(OrganizationCreate(legal_name="rebase srl"), Actor.system())
            .id
        )


def _fold(
    door: Door,
    slug: str,
    survivor_id: UUID,
    loser_id: UUID,
    *,
    dry_run: bool,
    registry_class: type[EngagementRegistry] = EngagementRegistry,
    space_fails_to_commit: bool = False,
) -> Any:
    """`OrganizationService.merge` in the space `slug` with the registry's own session beside
    the space's, as the routes and the MCP tool give it."""
    with _space(door, slug) as space, session_factory(door.registry)() as registry:
        if space_fails_to_commit:

            def fail() -> None:
                raise RuntimeError("the space went away")

            space.commit = fail  # type: ignore[method-assign]
        return OrganizationService(space).merge(
            survivor_id,
            loser_id,
            MERGER,
            dry_run=dry_run,
            registry=registry_class(registry, slug),
        )


def test_a_merge_points_the_registry_at_the_survivor(door: Door) -> None:
    """REB-1031: the freelancer folds the customer «rebase» into another row of the same
    company. The merge is what writes the registry row: the survivor comes back, and the
    deal and the contract stay where they were."""
    from pigrocrm.core.contracts.models import Contract

    match_id = uuid4()
    first = _seed(door, match_id)
    survivor = _survivor(door, "ada-lovelace")

    done = _fold(door, "ada-lovelace", survivor, first.customer_id, dry_run=False)

    assert done.moved["rebase_engagements"] == 1
    row = _row(door, match_id)
    assert row is not None and row.customer_id == survivor
    assert (row.deal_id, row.contract_id) == (first.deal_id, first.contract_id)
    with _space(door, "ada-lovelace") as space:
        # The survivor and the archived loser: no third customer.
        assert _count(space, Organization) == 2
        assert _count(space, Deal) == 1
        assert space.scalar(select(func.count()).select_from(Contract)) == 1
        deal = space.get(Deal, first.deal_id)
        assert deal is not None and deal.customer_id == survivor


def test_the_preview_counts_the_registry_row_and_writes_nothing_there(door: Door) -> None:
    match_id = uuid4()
    first = _seed(door, match_id)
    survivor = _survivor(door, "ada-lovelace")

    preview = _fold(door, "ada-lovelace", survivor, first.customer_id, dry_run=True)

    assert preview.dry_run is True and preview.moved["rebase_engagements"] == 1
    row = _row(door, match_id)
    assert row is not None and row.customer_id == first.customer_id


def test_a_merge_leaves_the_registry_rows_of_other_spaces_alone(door: Door) -> None:
    mine = _seed(door)
    other_match = uuid4()
    other = _seed(door, other_match, slug="grace-hopper", email="grace@studio.it")
    assert other.slug == "grace-hopper"
    # An id never recurs across spaces by chance, so it is put there: only the space the merge
    # serves is read.
    with session_factory(door.registry)() as registry:
        registry.execute(
            update(RebaseEngagement)
            .where(RebaseEngagement.match_id == other_match)
            .values(customer_id=mine.customer_id)
        )
        registry.commit()
    survivor = _survivor(door, "ada-lovelace")

    done = _fold(door, "ada-lovelace", survivor, mine.customer_id, dry_run=False)

    assert done.moved["rebase_engagements"] == 1
    row = _row(door, other_match)
    assert row is not None and row.customer_id == mine.customer_id


def test_a_registry_that_fails_leaves_the_merge_undone_and_runnable(door: Door) -> None:
    match_id = uuid4()
    first = _seed(door, match_id)
    survivor = _survivor(door, "ada-lovelace")

    with pytest.raises(RuntimeError, match="went away"):
        _fold(
            door,
            "ada-lovelace",
            survivor,
            first.customer_id,
            dry_run=False,
            registry_class=_RegistryThatFailsToCommit,
        )

    row = _row(door, match_id)
    assert row is not None and row.customer_id == first.customer_id
    with _space(door, "ada-lovelace") as space:
        loser = space.get(Organization, first.customer_id)
        assert loser is not None and loser.deleted_at is None
        deal = space.get(Deal, first.deal_id)
        assert deal is not None and deal.customer_id == first.customer_id

    again = _fold(door, "ada-lovelace", survivor, first.customer_id, dry_run=False)

    assert again.moved["rebase_engagements"] == 1
    row = _row(door, match_id)
    assert row is not None and row.customer_id == survivor


def test_a_space_that_fails_after_the_registry_leaves_the_merge_runnable(door: Door) -> None:
    """The order the merge commits in: the registry first. When the space then fails, the
    registry names the survivor, a live row of the same company, and both organizations are
    still live in the space, so the merge runs again and finishes."""
    match_id = uuid4()
    first = _seed(door, match_id)
    survivor = _survivor(door, "ada-lovelace")

    with pytest.raises(RuntimeError, match="space went away"):
        _fold(
            door,
            "ada-lovelace",
            survivor,
            first.customer_id,
            dry_run=False,
            space_fails_to_commit=True,
        )

    row = _row(door, match_id)
    assert row is not None and row.customer_id == survivor
    with _space(door, "ada-lovelace") as space:
        for organization_id in (survivor, first.customer_id):
            organization = space.get(Organization, organization_id)
            assert organization is not None and organization.deleted_at is None
        deal = space.get(Deal, first.deal_id)
        assert deal is not None and deal.customer_id == first.customer_id

    again = _fold(door, "ada-lovelace", survivor, first.customer_id, dry_run=False)

    assert again.moved.get("rebase_engagements") is None  # the registry was already right
    assert again.moved["deals"] == 1


def test_the_root_and_a_space_the_registry_does_not_know_have_no_rows_to_point(
    door: Door,
) -> None:
    first = _seed(door)
    survivor = uuid4()
    with session_factory(door.registry)() as registry:
        for slug in (None, "nobody-here"):
            nobody = EngagementRegistry(registry, slug)
            assert nobody.count(first.customer_id) == 0
            assert nobody.repoint(first.customer_id, survivor) == 0
        assert EngagementRegistry(registry, "ada-lovelace").count(first.customer_id) == 1


# --- report: a match's hours with their invoices (§ 2.4) -----------------------------


def _log(
    space: Session, deal_id: UUID, day: date, description: str, *, billable: bool = True
) -> UUID:
    """Eight hours on the deal, logged for the space's admin through the service: the
    admin has no password, so the service is the way in."""
    admin = UserRepository(space).get_by_email(ADA)
    assert admin is not None
    entry = TimeEntryService(space).create(
        TimeEntryCreate(
            deal_id=deal_id,
            user_id=admin.id,
            date=day,
            hours=Decimal("8.00"),
            description=description,
            billable=billable,
        ),
        Actor.system(),
    )
    return entry.id


def _invoice(space: Session, linked: Seeded, entries: list[UUID], **fields: Any) -> UUID:
    """An invoice of the deal's customer with one line, and `entries` bound to that
    line, written as rows: the report reads an invoice's state, not how it got there."""
    invoice = Invoice(customer_id=linked.customer_id, deal_id=linked.deal_id, **fields)
    space.add(invoice)
    space.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        line_number=1,
        description="Ore di lavoro",
        quantity=Decimal("8"),
        unit_price=Decimal("50"),
        total_price=Decimal("400.00"),
        vat_rate=Decimal("22.00"),
    )
    space.add(line)
    space.flush()
    space.execute(
        update(TimeEntry).where(TimeEntry.id.in_(entries)).values(invoice_line_id=line.id)
    )
    space.commit()
    return invoice.id


def _frozen(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    """The product's one clock (`db.clock._now`) at `instant`."""
    monkeypatch.setattr(db_clock, "_now", lambda: instant)


def test_report_lists_entries_with_their_invoice(
    door: Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One row per entry, sorted by day and then by when it was logged, each with the
    invoice its line belongs to; the billed hours and the invoices summed from them.
    Read one entry per page here, so the paging is walked to its end."""
    monkeypatch.setattr(engagements_service, "REPORT_PAGE_SIZE", 1)
    match_id = uuid4()
    linked = _seed(door, match_id)
    today = today_local(door.settings)
    first, second = today - timedelta(days=2), today - timedelta(days=1)
    with _space(door, linked.slug) as space:
        setup = _log(space, linked.deal_id, first, "Setup")
        api = _log(space, linked.deal_id, second, "API")
        _log(space, linked.deal_id, second, "Riunione interna", billable=False)
        invoice_id = _invoice(
            space,
            linked,
            [setup, api],
            type="invoice",
            status="issued",
            year=2026,
            number=12,
            issue_date=second,
        )

    report = door.service().report(match_id, date_from=first)

    assert report.slug == "ada-lovelace"
    assert report.deal_url == linked.deal_url
    assert report.deal == ReportDeal(
        id=linked.deal_id,
        name=DEAL_NAME,
        hourly_rate=Decimal("50.000000"),
        budgeted_hours=Decimal("320.00"),
        status="in_progress",
    )
    issued = ReportInvoice(
        id=invoice_id,
        type="invoice",
        year=2026,
        number=12,
        status="issued",
        payment_status="unpaid",
        date=second,
    )
    assert [(g.date, g.hours, g.description, g.billable, g.invoice) for g in report.days] == [
        (first, Decimal("8.00"), "Setup", True, issued),
        (second, Decimal("8.00"), "API", True, issued),
        (second, Decimal("8.00"), "Riunione interna", False, None),
    ]
    assert report.invoices == [issued.model_copy(update={"hours": Decimal("16.00")})]
    # The wire shape (spec § 2.4): hours at two places, the rate at six, and no hours on an
    # entry's own invoice.
    wire = report.model_dump(mode="json")
    assert (wire["total_hours"], wire["billed_hours"], wire["unbilled_hours"]) == (
        "24.00",
        "16.00",
        "8.00",
    )
    assert wire["deal"]["hourly_rate"] == "50.000000"
    assert wire["deal"]["budgeted_hours"] == "320.00"
    assert wire["days"][0]["invoice"]["hours"] is None
    assert wire["invoices"][0]["hours"] == "16.00"
    assert wire["invoices"][0]["date"] == second.isoformat()


def test_report_counts_only_issued_invoices_as_billed(door: Door) -> None:
    """Billed is the CRM's own word for it (`billed_entry_ids`): a line of a `invoice`
    that is `issued`. An entry on a proforma or on a draft still shows that invoice, and
    counts as not billed; an entry on an invoice deleted since shows none, as the CRM
    itself no longer shows that invoice. The invoices come newest first, the undated
    ones last."""
    match_id = uuid4()
    linked = _seed(door, match_id)
    today = today_local(door.settings)
    days = [today - timedelta(days=n) for n in (3, 2, 1, 0)]
    with _space(door, linked.slug) as space:
        on_proforma, on_draft, on_issued, on_deleted = [
            _log(space, linked.deal_id, day, name)
            for day, name in zip(days, ("Analisi", "Sviluppo", "Rilascio", "Scartata"), strict=True)
        ]
        proforma = _invoice(space, linked, [on_proforma], type="proforma", status="confirmed")
        draft = _invoice(space, linked, [on_draft], type="invoice", status="draft")
        issued = _invoice(
            space,
            linked,
            [on_issued],
            type="invoice",
            status="issued",
            year=2026,
            number=7,
            issue_date=days[2],
        )
        _invoice(
            space,
            linked,
            [on_deleted],
            type="invoice",
            status="draft",
            deleted_at=datetime.now(UTC),
        )

    report = door.service().report(match_id, date_from=days[0])

    assert [(g.description, g.invoice.id if g.invoice else None) for g in report.days] == [
        ("Analisi", proforma),
        ("Sviluppo", draft),
        ("Rilascio", issued),
        ("Scartata", None),
    ]
    shown = report.days[0].invoice
    assert shown is not None
    assert (shown.type, shown.status, shown.year, shown.number, shown.date, shown.hours) == (
        "proforma",
        "confirmed",
        None,
        None,
        None,
        None,
    )
    assert (report.total_hours, report.billed_hours, report.unbilled_hours) == (
        Decimal("32.00"),
        Decimal("8.00"),
        Decimal("24.00"),
    )
    assert [(f.id, f.hours) for f in report.invoices] == [
        (issued, Decimal("8.00")),
        (draft, Decimal("8.00")),
        (proforma, Decimal("8.00")),
    ]


def test_report_defaults_and_caps_the_period(door: Door, monkeypatch: pytest.MonkeyPatch) -> None:
    """`date_from` is the day the deal was created, in the CRM's zone (22:30 UTC on 1 October is
    already the 2nd in Rome), `date_to` is today by the CRM's clock; a period over 800 days,
    or one that ends before it starts, is refused."""
    match_id = uuid4()
    linked = _seed(door, match_id)
    with _space(door, linked.slug) as space:
        space.execute(
            text("UPDATE deals SET created_at = :at WHERE id = :deal"),
            {"at": datetime(2026, 10, 1, 22, 30, tzinfo=UTC), "deal": linked.deal_id},
        )
        space.commit()
        _frozen(monkeypatch, datetime(2026, 11, 1, 12, 0, tzinfo=UTC))
        for day in (1, 2, 31):
            _log(space, linked.deal_id, date(2026, 10, day), f"Il {day} ottobre")
        _log(space, linked.deal_id, date(2026, 11, 1), "Il primo novembre")

    _frozen(monkeypatch, datetime(2026, 10, 31, 12, 0, tzinfo=UTC))
    report = door.service().report(match_id)

    assert [g.date for g in report.days] == [date(2026, 10, 2), date(2026, 10, 31)]

    for date_from, date_to, reason in (
        (date(2024, 1, 1), date(2024, 1, 1) + timedelta(days=801), "al massimo 800 giorni"),
        (date(2026, 10, 2), date(2026, 10, 1), REVERSED),
    ):
        with pytest.raises(ValidationFailed) as refused:
            door.service().report(match_id, date_from=date_from, date_to=date_to)
        assert refused.value.details == {
            "entity": "engagement",
            "field": "period",
            "reason": reason,
        }
    # `date_to` alone before the deal's first day is the same reversed period.
    with pytest.raises(ValidationFailed) as refused:
        door.service().report(match_id, date_to=date(2026, 10, 1))
    assert refused.value.details["reason"] == REVERSED


def test_report_accepts_exactly_800_days(door: Door) -> None:
    """The days report walks 800-day windows whose bounds touch (`date_from + 800`, then
    `date_from + 801`): a span of exactly 800 days is accepted, and both of its ends are in it."""
    match_id = uuid4()
    linked = _seed(door, match_id)
    date_to = today_local(door.settings)
    date_from = date_to - timedelta(days=800)
    with _space(door, linked.slug) as space:
        _log(space, linked.deal_id, date_from - timedelta(days=1), "Prima")
        _log(space, linked.deal_id, date_from, "Il primo giorno")
        _log(space, linked.deal_id, date_to, "L'ultimo giorno")

    report = door.service().report(match_id, date_from=date_from, date_to=date_to)

    assert [g.description for g in report.days] == ["Il primo giorno", "L'ultimo giorno"]
    assert report.total_hours == Decimal("16.00")


def test_report_of_unknown_match_is_not_found(door: Door) -> None:
    unknown = uuid4()
    with pytest.raises(NotFound) as missing:
        door.service().report(unknown)
    assert missing.value.details == {"entity": "engagement", "identifier": str(unknown)}

    # A row left before its deal existed has nothing to report either.
    tenant_id = _provision(door, "ada-lovelace")
    halfway = uuid4()
    with session_factory(door.registry)() as registry:
        registry.add(RebaseEngagement(match_id=halfway, tenant_id=tenant_id))
        registry.commit()
    with pytest.raises(NotFound) as missing:
        door.service().report(halfway)
    assert missing.value.details["identifier"] == str(halfway)


def test_report_of_deleted_deal_answers_409(door: Door) -> None:
    match_id = uuid4()
    linked = _seed(door, match_id)
    with _space(door, linked.slug) as space:
        DealService(space).soft_delete(linked.deal_id, Actor.system())

    with pytest.raises(Conflict) as refused:
        door.service().report(match_id)

    assert refused.value.details["reason"] == GONE
    assert refused.value.details["match_id"] == str(match_id)


def test_report_refuses_without_public_url(door: Door, db_engine: Engine) -> None:
    """The report carries the deal's page, built from `PIGROCRM_PUBLIC_URL`: without it the
    service refuses before it looks for the match."""
    service = EngagementService(door.registry, _settings_for(db_engine, public_url=""))

    with pytest.raises(ValidationFailed) as refused:
        service.report(uuid4())

    assert refused.value.details["field"] == "public_url"
