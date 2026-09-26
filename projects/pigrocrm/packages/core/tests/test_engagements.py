"""rebase's engagements door (spec 2026-09-25 § 2.2, § 2.3): the registry table
`rebase_engagements`, and `EngagementService.ensure`, which sets up a freelancer's
space, the customer «rebase» and the deal of one letter, once per hub match; and
`EngagementService.report` (§ 2.4), which reads that deal's hours back with the invoices
they sit on.

Every `ensure` test runs on the container for real: `TenantService.provision` creates
the space's database and migrates it exactly as the signup does. The `door` fixture
empties the registry and drops every space on the way out, the way
`test_tenants_api.py`'s `_serving` does, on core alone.
"""

import ast
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, inspect, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import pigrocrm.core.db.clock as db_clock
import pigrocrm.core.engagements as engagements_package
import pigrocrm.core.engagements.service as engagements_service
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.schemas import (
    EngagementRead,
    EngagementUpsert,
    ReportDeal,
    ReportInvoice,
)
from pigrocrm.core.engagements.service import (
    EngagementBusy,
    EngagementService,
    SpaceUnreachable,
    deal_marker,
    deal_name,
)
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.tenants import (
    Tenant,
    TenantAvailability,
    TenantService,
    TenantSignup,
    ensure_defaults,
    ensure_tenants_database,
)
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.service import migrate_to_head
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.schemas import TimeEntryCreate
from pigrocrm.core.timetracking.service import TimeEntryService

ADA = "ada@studio.it"
CUSTOMER_NOTE = (
    "Creato da rebase per la lettera n. 3/2026. rebase legge le ore dei deal di questo "
    "cliente per rendicontare gli incarichi."
)
DEAL_NOTE = (
    "Creato da rebase per la lettera n. 3/2026 con Acme S.r.l. rebase legge le ore di "
    "questo deal per la rendicontazione al cliente."
)
DEAL_NAME = "Lettera n. 3/2026 · Backend developer per Acme S.r.l."
GONE = "Il deal di questa lettera è stato eliminato nello spazio."
BUSY = "Un'altra chiamata sta preparando lo spazio di questo indirizzo: riprova tra poco."
REVERSED = "La data di fine precede quella di inizio."


def _settings_for(engine: Engine, *, public_url: str = "https://pigro.test") -> Settings:
    return Settings(
        database_url=engine.url.render_as_string(hide_password=False),
        public_url=public_url,
        _env_file=None,  # type: ignore[call-arg]
    )


def _upsert(**parts: dict[str, Any]) -> EngagementUpsert:
    """The body of spec § 2.3, with any part's fields overridden by name."""
    body: dict[str, dict[str, Any]] = {
        "freelancer": {"email": ADA, "nome": "Ada", "cognome": "Lovelace"},
        "lettera": {
            "numero": "3/2026",
            "ruolo": "Backend developer",
            "azienda": "Acme S.r.l.",
            "data_inizio": "2026-10-01",
            "data_fine": "2026-12-31",
            "compenso": "400.00",
            "giorni_previsti": 40,
        },
        "rebase": {
            "ragione_sociale": "rebase S.r.l.",
            "partita_iva": "01234567890",
            "codice_fiscale": "01234567890",
            "indirizzo": "Via Roma 1, 20121 Milano",
            "pec": "rebase@pec.it",
            "codice_sdi": "M5UXCR1",
        },
    }
    for part, overrides in parts.items():
        body[part] = {**body[part], **overrides}
    return EngagementUpsert.model_validate(body)


@dataclass
class Door:
    settings: Settings
    registry: Engine
    sender: RecordingSender = field(default_factory=RecordingSender)

    def service(self) -> EngagementService:
        return EngagementService(self.registry, self.settings, self.sender)


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
            TenantSignup(slug=slug, nome="Ada Lovelace", email=email)
        )
    return tenant.id


def _half_provisioned(door: Door, slug: str, *, database: bool) -> UUID:
    """A registry row `provision` committed before a restart cut it short: no user in
    the space. With `database`, the space as the next boot's `ensure-space-defaults`
    leaves it -- created, migrated, furnished -- and without, no database at all."""
    with session_factory(door.registry)() as registry:
        tenant = Tenant(slug=slug, db_name=tenant_database_name(slug), owner_email=ADA)
        registry.add(tenant)
        registry.commit()
        tenant_id = tenant.id
    if database:
        url = tenant_database_url(door.settings, tenant_database_name(slug))
        create_database_if_missing(door.settings, url)
        migrate_to_head(door.settings, url.render_as_string(hide_password=False))
        with _space(door, slug) as space:
            ensure_defaults(space)
    return tenant_id


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


def _owned(door: Door, email: str = ADA) -> list[str]:
    with session_factory(door.registry)() as registry:
        return list(
            registry.scalars(
                select(Tenant.slug)
                .where(func.lower(Tenant.owner_email) == email)
                .order_by(Tenant.created_at)
            )
        )


def _row(door: Door, match_id: UUID) -> RebaseEngagement | None:
    with session_factory(door.registry)() as registry:
        return registry.scalar(
            select(RebaseEngagement).where(RebaseEngagement.match_id == match_id)
        )


def _count(space: Session, model: type[Customer] | type[Deal], *where: Any) -> int:
    return space.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _at_once(door: Door, bodies: dict[str, EngagementUpsert]) -> dict[str, EngagementRead]:
    """Each body through `ensure` on a thread of its own, with a match id of its own,
    released together by a barrier. An error on a thread fails the test here, and so
    does a thread still alive after its join: a hang is a failure, not a slow pass."""
    barrier = threading.Barrier(len(bodies))
    answers: dict[str, EngagementRead] = {}
    errors: list[BaseException] = []

    def activate(key: str, body: EngagementUpsert) -> None:
        try:
            barrier.wait(timeout=30)
            answers[key] = door.service().ensure(uuid4(), body)
        except BaseException as exc:  # noqa: BLE001 -- carried to the main thread
            errors.append(exc)

    threads = [
        threading.Thread(target=activate, args=(key, body), daemon=True)
        for key, body in bodies.items()
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
        assert not thread.is_alive()
    assert errors == []
    return answers


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


# --- ensure: the first call and the ones after ---------------------------------------


def test_first_call_creates_space_customer_and_deal(door: Door) -> None:
    match_id = uuid4()
    answer = door.service().ensure(match_id, _upsert())

    assert answer.creato is True and answer.spazio_creato is True
    assert answer.slug == "ada-lovelace"
    assert answer.url == "https://pigro.test/ada-lovelace/app/"
    assert answer.deal_url == f"https://pigro.test/ada-lovelace/app/deal/{answer.deal_id}"
    assert _owned(door) == ["ada-lovelace"]

    with _space(door, "ada-lovelace") as space:
        customer = space.get(Customer, answer.customer_id)
        assert customer is not None
        assert customer.ragione_sociale == "rebase S.r.l."
        assert customer.partita_iva == "01234567890"
        assert customer.codice_fiscale == "01234567890"
        assert customer.indirizzo == "Via Roma 1, 20121 Milano"
        assert customer.pec == "rebase@pec.it"
        assert customer.codice_sdi == "M5UXCR1"
        assert customer.note == CUSTOMER_NOTE

        deal = space.get(Deal, answer.deal_id)
        assert deal is not None
        assert deal.nome == DEAL_NAME
        assert deal.customer_id == customer.id
        # 400.00 a day over eight hours, at the column's six places.
        assert str(deal.tariffa_oraria) == "50.000000"
        assert str(deal.ore_preventivate) == "320.00"
        assert deal.data_chiusura_prevista == date(2026, 12, 31)
        # The sentence, then the marker a retry finds the deal by.
        assert deal_marker(match_id) == f"rebase:match={match_id}"
        assert deal.note == f"{DEAL_NOTE}\n{deal_marker(match_id)}"
        assert deal.pipeline_stage_id == PipelineService(space).default_stage().id
        admin = UserRepository(space).get_by_email(ADA)
        assert admin is not None and deal.owner_id == admin.id

        actors = space.scalars(
            select(Activity.actor_type).where(Activity.entity_id.in_([customer.id, deal.id]))
        ).all()
        assert actors == ["rebase", "rebase"]

    row = _row(door, match_id)
    assert row is not None
    assert (row.customer_id, row.deal_id) == (answer.customer_id, answer.deal_id)


def test_second_call_answers_the_same_ids_and_creates_nothing(door: Door) -> None:
    match_id = uuid4()
    first = door.service().ensure(match_id, _upsert())
    second = door.service().ensure(match_id, _upsert())

    assert second.creato is False and second.spazio_creato is False
    assert (second.slug, second.customer_id, second.deal_id) == (
        first.slug,
        first.customer_id,
        first.deal_id,
    )
    assert second.deal_url == first.deal_url
    assert _owned(door) == ["ada-lovelace"]
    with _space(door, "ada-lovelace") as space:
        assert _count(space, Customer) == 1
        assert _count(space, Deal) == 1

    # A retry whose body changed since is answered the recorded ids too, and writes
    # nothing: not a second customer or deal, not the new rate on the recorded deal.
    changed = door.service().ensure(
        match_id,
        _upsert(
            lettera={"numero": "9/2026", "compenso": "800.00"},
            rebase={"ragione_sociale": "rebase S.p.A.", "partita_iva": "09876543210"},
        ),
    )
    assert (changed.creato, changed.customer_id, changed.deal_id) == (
        False,
        first.customer_id,
        first.deal_id,
    )
    with _space(door, "ada-lovelace") as space:
        assert _count(space, Customer) == 1 and _count(space, Deal) == 1
        recorded = space.get(Deal, first.deal_id)
        assert recorded is not None
        assert (recorded.nome, str(recorded.tariffa_oraria)) == (DEAL_NAME, "50.000000")

    # The row's ids are answered (spec § 2.3 step 2), not what the deal points at now.
    with _space(door, "ada-lovelace") as space:
        other = CustomerService(space).create(
            CustomerCreate(ragione_sociale="Altro"), Actor.system()
        )
        space.execute(
            text("UPDATE deals SET customer_id = :c WHERE id = :d"),
            {"c": other.id, "d": first.deal_id},
        )
        space.commit()
    third = door.service().ensure(match_id, _upsert())
    assert third.customer_id == first.customer_id


def test_owned_space_is_reused_oldest_first(door: Door) -> None:
    _provision(door, "studio-ada")
    _provision(door, "ada-seconda")

    answer = door.service().ensure(uuid4(), _upsert())

    assert answer.slug == "studio-ada"
    assert answer.spazio_creato is False and answer.creato is True
    assert _owned(door) == ["studio-ada", "ada-seconda"]
    with _space(door, "studio-ada") as space:
        assert _count(space, Deal, Deal.nome == DEAL_NAME) == 1
    with _space(door, "ada-seconda") as space:
        assert _count(space, Deal) == 0
    # A space the door did not open is not welcomed again.
    assert door.sender.sent == []


def test_owner_lookup_is_case_insensitive(door: Door) -> None:
    _provision(door, "studio-ada")

    answer = door.service().ensure(uuid4(), _upsert(freelancer={"email": "Ada@Studio.it "}))

    assert answer.slug == "studio-ada" and answer.spazio_creato is False
    with _space(door, "studio-ada") as space:
        deal = space.get(Deal, answer.deal_id)
        admin = UserRepository(space).get_by_email(ADA)
        assert deal is not None and admin is not None and deal.owner_id == admin.id

    # And the other side: a registry row that kept an address's capitals.
    with door.registry.begin() as connection:
        connection.execute(text("UPDATE tenants SET owner_email = 'Ada@Studio.IT'"))
    again = door.service().ensure(uuid4(), _upsert(lettera={"numero": "4/2026"}))
    assert again.slug == "studio-ada" and again.spazio_creato is False
    assert _owned(door) == ["studio-ada"]


def test_slug_collision_takes_a_suffix(door: Door) -> None:
    _provision(door, "ada-lovelace", email="altra@studio.it")

    answer = door.service().ensure(uuid4(), _upsert())

    assert answer.slug == "ada-lovelace-2" and answer.spazio_creato is True
    assert answer.url == "https://pigro.test/ada-lovelace-2/app/"
    assert _owned(door) == ["ada-lovelace-2"]
    assert _owned(door, "altra@studio.it") == ["ada-lovelace"]


def test_a_slug_lost_at_the_insert_moves_on(door: Door, monkeypatch: pytest.MonkeyPatch) -> None:
    """A namesake's signup takes the name between `availability` and the insert:
    `provision` answers the slug's `Conflict`, and the next candidate is tried."""
    real = TenantService.provision
    tried: list[str] = []

    def racing(self: TenantService, data: TenantSignup) -> Any:
        tried.append(data.slug)
        if len(tried) == 1:
            raise Conflict("tenant", "questo nome è già in uso", slug=data.slug)
        return real(self, data)

    monkeypatch.setattr(TenantService, "provision", racing)
    answer = door.service().ensure(uuid4(), _upsert())

    assert tried == ["ada-lovelace", "ada-lovelace-2"]
    assert answer.slug == "ada-lovelace-2"


def test_a_conflict_about_something_else_is_not_retried(
    door: Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    tried: list[str] = []

    def refusing(self: TenantService, data: TenantSignup) -> Any:
        tried.append(data.slug)
        if len(tried) > 60:  # the RED run's guard against retrying it for ever
            raise RuntimeError("a foreign Conflict is retried")
        raise Conflict("tenant", "un altro motivo", slug="un-altro-nome")

    monkeypatch.setattr(TenantService, "provision", refusing)
    with pytest.raises(Conflict) as refused:
        door.service().ensure(uuid4(), _upsert())

    assert tried == ["ada-lovelace"]
    assert refused.value.details["reason"] == "un altro motivo"


def test_the_suffix_gives_up_after_fifty(door: Door, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def taken(self: TenantService, slug: str) -> TenantAvailability:
        asked.append(slug)
        if len(asked) > 60:  # the RED run's guard against an unbounded loop
            raise RuntimeError("the suffix has no bound")
        return TenantAvailability(slug=slug, disponibile=False, motivo="questo nome è già in uso")

    monkeypatch.setattr(TenantService, "availability", taken)
    with pytest.raises(Conflict):
        door.service().ensure(uuid4(), _upsert())

    assert len(asked) == 50
    assert asked[-1] == "ada-lovelace-50"
    assert _owned(door) == []


def test_two_matches_one_email_share_one_space(door: Door) -> None:
    """Two letters of the same freelancer activating together (spec § 3.10): the lock
    by address serialises the whole call, so the second finds the space the first
    opened instead of opening another under `ada-lovelace-2`."""
    answers = _at_once(door, {n: _upsert(lettera={"numero": n}) for n in ("1/2026", "2/2026")})

    assert _owned(door) == ["ada-lovelace"]
    assert {a.slug for a in answers.values()} == {"ada-lovelace"}
    assert sorted(a.spazio_creato for a in answers.values()) == [False, True]
    assert answers["1/2026"].customer_id == answers["2/2026"].customer_id
    assert answers["1/2026"].deal_id != answers["2/2026"].deal_id
    with _space(door, "ada-lovelace") as space:
        assert _count(space, Customer) == 1
        assert _count(space, Deal) == 2
    assert len(door.sender.sent) == 1


def test_two_addresses_at_once_open_two_spaces(door: Door) -> None:
    """Two freelancers' letters activating in the same instant: the lock by address
    lets both through, so two spaces are provisioned at once in one process. Alembic's
    script directory is not thread-safe (`DuplicateTable`, `KeyError('script')` before
    `migrate_to_head` took its own lock), so the migrations take turns."""
    grace = {"email": "grace@studio.it", "nome": "Grace", "cognome": "Hopper"}
    answers = _at_once(
        door,
        {
            "ada": _upsert(lettera={"numero": "1/2026"}),
            "grace": _upsert(freelancer=grace, lettera={"numero": "2/2026"}),
        },
    )

    assert (answers["ada"].slug, answers["grace"].slug) == ("ada-lovelace", "grace-hopper")
    assert answers["ada"].spazio_creato is True and answers["grace"].spazio_creato is True
    assert _owned(door) == ["ada-lovelace"]
    assert _owned(door, "grace@studio.it") == ["grace-hopper"]
    for slug in ("ada-lovelace", "grace-hopper"):
        with _space(door, slug) as space:
            assert _count(space, Customer) == 1 and _count(space, Deal) == 1
    assert sorted(mail.to for mail in door.sender.sent) == [ADA, "grace@studio.it"]


def test_a_held_lock_times_out_as_busy(door: Door, monkeypatch: pytest.MonkeyPatch) -> None:
    """A call that hangs while holding an address must not pin every later call for
    it: the wait is bounded by `lock_timeout`, and the answer says to try again -- a
    «not now» (`EngagementBusy`, the router's 503), not a `Conflict` the hub would file
    as refused for good -- and carries no address."""
    monkeypatch.setattr(engagements_service, "LOCK_TIMEOUT", "200ms")
    with door.registry.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(hashtext(:e))"), {"e": ADA})
        try:
            with pytest.raises(EngagementBusy) as refused:
                door.service().ensure(uuid4(), _upsert())
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(hashtext(:e))"), {"e": ADA})
            holder.commit()

    assert not isinstance(refused.value, Conflict)
    assert refused.value.args == (BUSY,)
    assert _owned(door) == []


def test_a_failed_unlock_drops_the_connection_and_frees_the_address(
    door: Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `pg_advisory_unlock` itself fails, the connection is invalidated rather than
    handed back to the pool still holding the address; closing it is what releases a
    session lock. The call's own error is the one that surfaces."""

    def fails_midway(self: EngagementService, *args: object) -> EngagementRead:
        raise RuntimeError("a failure in the middle of the call")

    monkeypatch.setattr(EngagementService, "_ensure", fails_midway)
    monkeypatch.setattr(
        engagements_service, "UNLOCK", text("SELECT pg_advisory_unlock_missing(:email)")
    )
    with pytest.raises(RuntimeError, match="in the middle"):
        door.service().ensure(uuid4(), _upsert())

    # A connection of its own, outside the pool: a session lock is re-entrant, so the
    # pooled connection that kept it would answer yes to its own question.
    other = create_engine(door.registry.url, poolclass=NullPool)
    try:
        with other.connect() as connection:
            free = False
            for _ in range(50):
                free = bool(
                    connection.scalar(text("SELECT pg_try_advisory_lock(hashtext(:e))"), {"e": ADA})
                )
                if free:
                    connection.execute(text("SELECT pg_advisory_unlock(hashtext(:e))"), {"e": ADA})
                    break
                connection.commit()
                threading.Event().wait(0.05)
            assert free
    finally:
        other.dispose()


# --- ensure: resuming what a previous call left halfway ------------------------------


def test_half_written_row_is_completed(door: Door) -> None:
    """A call that stopped between step 3 and step 6 left a row with its space and no
    deal: the next one continues in that row's space, not in the one the address would
    find first (`studio-ada`, the oldest)."""
    _provision(door, "studio-ada")
    tenant_id = _provision(door, "ada-seconda")
    match_id = uuid4()
    with session_factory(door.registry)() as registry:
        registry.add(RebaseEngagement(match_id=match_id, tenant_id=tenant_id))
        registry.commit()

    answer = door.service().ensure(match_id, _upsert())

    assert answer.slug == "ada-seconda"
    assert answer.spazio_creato is False and answer.creato is True
    with _space(door, "ada-seconda") as space:
        assert _count(space, Customer) == 1 and _count(space, Deal) == 1
        deal = space.get(Deal, answer.deal_id)
        assert deal is not None and deal.customer_id == answer.customer_id
    with _space(door, "studio-ada") as space:
        assert _count(space, Customer) == 0
    row = _row(door, match_id)
    assert row is not None
    assert (row.tenant_id, row.customer_id, row.deal_id) == (
        tenant_id,
        answer.customer_id,
        answer.deal_id,
    )


def test_a_space_left_without_its_admin_gets_one_and_the_welcome_once(door: Door) -> None:
    """`provision` commits the registry row before it creates the database: a restart
    in between leaves a row whose space the next boot creates and furnishes, with
    nobody in it. The door, finding it as the address's own, creates the admin the
    way `provision` does and sends the welcome -- once, whatever the calls after --
    so the deal has an owner and the hub's link leads somewhere."""
    _half_provisioned(door, "ada-lovelace", database=True)

    answer = door.service().ensure(uuid4(), _upsert())

    assert answer.slug == "ada-lovelace" and answer.creato is True
    # Opened now, as far as the freelancer can tell: the hub's mail says so.
    assert answer.spazio_creato is True
    assert _owned(door) == ["ada-lovelace"]
    with _space(door, "ada-lovelace") as space:
        admin = UserRepository(space).get_by_email(ADA)
        assert admin is not None
        assert (admin.ruolo, admin.nome, admin.password_hash) == ("admin", "Ada Lovelace", None)
        deal = space.get(Deal, answer.deal_id)
        assert deal is not None and deal.owner_id == admin.id
    assert [mail.to for mail in door.sender.sent] == [ADA]
    assert "https://pigro.test/ada-lovelace/app/verify?t=" in door.sender.sent[0].text

    again = door.service().ensure(uuid4(), _upsert(lettera={"numero": "4/2026"}))
    assert again.slug == "ada-lovelace" and again.spazio_creato is False
    assert len(door.sender.sent) == 1


def test_a_space_without_its_database_is_unreachable_by_tenant_id(door: Door) -> None:
    """The same restart with no boot after it: the space has no database. The call is a
    «not now» naming the tenant's id (never the slug or the address), which the router
    logs with the match's id, so an operator can find the row."""
    tenant_id = _half_provisioned(door, "ada-lovelace", database=False)

    with pytest.raises(SpaceUnreachable) as unreachable:
        door.service().ensure(uuid4(), _upsert())

    assert unreachable.value.tenant_id == tenant_id
    assert "ada-lovelace" not in str(unreachable.value) and ADA not in str(unreachable.value)
    assert door.sender.sent == []


def test_retry_after_deal_created_but_unrecorded_reuses_it(door: Door) -> None:
    """A failure between step 5 and step 6: the deal exists with this match's marker,
    and the row has no `deal_id`. The retry finds the customer by its exact name (no
    VAT number in this body: the preview's case) and the deal by the marker, whatever
    the freelancer has renamed it to since."""
    tenant_id = _provision(door, "ada-lovelace")
    match_id = uuid4()
    with _space(door, "ada-lovelace") as space:
        customer = CustomerService(space).create(
            CustomerCreate(ragione_sociale="rebase S.r.l."), Actor.rebase()
        )
        deal = DealService(space).create(
            DealCreate(
                nome="Acme, il backend",
                customer_id=customer.id,
                note=f"{DEAL_NOTE}\n{deal_marker(match_id)}",
            ),
            Actor.rebase(),
        )
    with session_factory(door.registry)() as registry:
        registry.add(RebaseEngagement(match_id=match_id, tenant_id=tenant_id))
        registry.commit()

    answer = door.service().ensure(match_id, _upsert(rebase={"partita_iva": None}))

    assert (answer.customer_id, answer.deal_id) == (customer.id, deal.id)
    assert answer.creato is True and answer.spazio_creato is False
    with _space(door, "ada-lovelace") as space:
        assert _count(space, Customer) == 1
        assert _count(space, Deal) == 1
    row = _row(door, match_id)
    assert row is not None
    assert (row.customer_id, row.deal_id) == (customer.id, deal.id)


def test_retry_finds_its_deal_under_a_customer_the_freelancer_edited(door: Door) -> None:
    """A failure between step 5 and step 6, and the freelancer renames the customer
    «rebase» and changes its VAT number before the retry (Greptile on #427): the deal
    is found by its marker under whichever customer it sits, and so is that customer.
    Looked up by name or VAT number first, the retry would have made a second customer
    and a second deal."""
    _provision(door, "ada-lovelace")
    match_id = uuid4()
    first = door.service().ensure(match_id, _upsert())
    with session_factory(door.registry)() as registry:
        registry.execute(
            update(RebaseEngagement)
            .where(RebaseEngagement.match_id == match_id)
            .values(customer_id=None, deal_id=None)
        )
        registry.commit()
    with _space(door, "ada-lovelace") as space:
        space.execute(
            update(Customer)
            .where(Customer.id == first.customer_id)
            .values(ragione_sociale="rebase (fornitore)", partita_iva="09876543210")
        )
        space.commit()

    answer = door.service().ensure(match_id, _upsert())

    assert (answer.customer_id, answer.deal_id) == (first.customer_id, first.deal_id)
    assert answer.creato is True
    with _space(door, "ada-lovelace") as space:
        assert _count(space, Customer) == 1
        assert _count(space, Deal) == 1


def test_same_named_deal_without_marker_is_not_reused(door: Door) -> None:
    """The freelancer's own deal with the same label (the letter recorded by hand, say)
    carries no marker: it is not ours, ours is created beside it, and theirs is left as
    it is."""
    _provision(door, "ada-lovelace")
    with _space(door, "ada-lovelace") as space:
        customer = CustomerService(space).create(
            CustomerCreate(ragione_sociale="rebase S.r.l.", partita_iva="01234567890"),
            Actor.system(),
        )
        theirs = DealService(space).create(
            DealCreate(nome=DEAL_NAME, customer_id=customer.id, note="La mia lettera."),
            Actor.system(),
        )

    answer = door.service().ensure(uuid4(), _upsert())

    assert answer.customer_id == customer.id
    assert answer.deal_id != theirs.id
    with _space(door, "ada-lovelace") as space:
        assert _count(space, Deal, Deal.nome == DEAL_NAME) == 2
        untouched = space.get(Deal, theirs.id)
        assert untouched is not None and untouched.note == "La mia lettera."


def test_a_vat_number_that_finds_nothing_falls_back_to_the_name(door: Door) -> None:
    """The customer «rebase» a body without a VAT number created, found by its name the
    day the signer data gains one: the same customer, not a second one, and left as it
    is (no VAT number written onto it)."""
    no_vat = door.service().ensure(uuid4(), _upsert(rebase={"partita_iva": None}))

    with_vat = door.service().ensure(uuid4(), _upsert(lettera={"numero": "4/2026"}))

    assert with_vat.customer_id == no_vat.customer_id
    with _space(door, "ada-lovelace") as space:
        assert _count(space, Customer) == 1
        customer = space.get(Customer, no_vat.customer_id)
        assert customer is not None and customer.partita_iva is None


# --- ensure: the edges ---------------------------------------------------------------


def test_longest_role_and_company_still_make_a_valid_name(door: Door) -> None:
    numero, ruolo, azienda = "9" * 20, "R" * 200, "A" * 255
    name = deal_name(numero, ruolo, azienda)
    assert name == f"Lettera n. {numero} · {'R' * 80} per {'A' * 100}"
    assert len(name) <= 255

    match_id = uuid4()
    answer = door.service().ensure(
        match_id, _upsert(lettera={"numero": numero, "ruolo": ruolo, "azienda": azienda})
    )

    with _space(door, answer.slug) as space:
        deal = space.get(Deal, answer.deal_id)
        assert deal is not None and deal.nome == name
        # The note is a `Text`: it keeps the whole company, and a name with no full stop
        # of its own gets one.
        assert deal.note == (
            f"Creato da rebase per la lettera n. {numero} con {azienda}. rebase legge le "
            "ore di questo deal per la rendicontazione al cliente.\n"
            f"{deal_marker(match_id)}"
        )


def test_deleted_deal_answers_409(door: Door) -> None:
    match_id = uuid4()
    first = door.service().ensure(match_id, _upsert())
    with _space(door, first.slug) as space:
        DealService(space).soft_delete(first.deal_id, Actor.system())

    with pytest.raises(Conflict) as refused:
        door.service().ensure(match_id, _upsert())

    assert refused.value.details["reason"] == GONE
    assert refused.value.details["match_id"] == str(match_id)
    # Nothing recreated behind the freelancer's back.
    with _space(door, first.slug) as space:
        assert _count(space, Deal) == 1
    row = _row(door, match_id)
    assert row is not None and row.deal_id == first.deal_id


def test_refuses_without_public_url(door: Door, db_engine: Engine) -> None:
    service = EngagementService(
        door.registry, _settings_for(db_engine, public_url="  "), door.sender
    )

    with pytest.raises(ValidationFailed) as refused:
        service.ensure(uuid4(), _upsert())

    assert refused.value.details["entity"] == "engagement"
    assert refused.value.details["field"] == "public_url"
    assert refused.value.details["reason"] == "PIGROCRM_PUBLIC_URL non configurato"
    assert _owned(door) == []


def test_welcome_mail_is_sent_once_for_a_new_space(door: Door) -> None:
    door.service().ensure(uuid4(), _upsert())

    assert len(door.sender.sent) == 1
    mail = door.sender.sent[0]
    assert mail.to == ADA
    assert "https://pigro.test/ada-lovelace/app/verify?t=" in mail.text
    # A member of the community: the mail does not invite her into it (`membro=True`).
    assert "Se vuoi entrarci" not in mail.text

    door.service().ensure(uuid4(), _upsert(lettera={"numero": "4/2026"}))
    assert len(door.sender.sent) == 1


def test_a_call_that_stops_after_opening_the_space_leaves_the_welcome_to_its_retry(
    door: Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Greptile on #427: the welcome went out with the provisioning, so a call that
    opened the space and then failed left a retry that found the space and its admin,
    sent nothing, and answered `spazio_creato: false`. The row now records that this
    match opened the space, and the call that completes the engagement sends the
    welcome and says so: once."""
    match_id = uuid4()
    customer_step = EngagementService._customer

    def stops(self: EngagementService, space: Session, data: EngagementUpsert) -> UUID:
        raise RuntimeError("stopped after the space")

    monkeypatch.setattr(EngagementService, "_customer", stops)
    with pytest.raises(RuntimeError):
        door.service().ensure(match_id, _upsert())
    assert door.sender.sent == []
    row = _row(door, match_id)
    assert row is not None and row.space_created is True and row.deal_id is None

    monkeypatch.setattr(EngagementService, "_customer", customer_step)
    answer = door.service().ensure(match_id, _upsert())

    assert answer.slug == "ada-lovelace"
    assert answer.creato is True and answer.spazio_creato is True
    assert [mail.to for mail in door.sender.sent] == [ADA]
    assert "https://pigro.test/ada-lovelace/app/verify?t=" in door.sender.sent[0].text
    again = door.service().ensure(match_id, _upsert())
    assert again.creato is False and len(door.sender.sent) == 1


def test_a_space_whose_admin_was_created_before_a_failure_still_gets_its_welcome(
    door: Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same for a space left without its admin: the row says the space is opened
    for this match before the admin exists, so a call that creates the admin and then
    fails leaves the retry both the answer and the mail."""
    _half_provisioned(door, "ada-lovelace", database=True)
    match_id = uuid4()
    customer_step = EngagementService._customer

    def stops(self: EngagementService, space: Session, data: EngagementUpsert) -> UUID:
        space.commit()  # the admin, as a step that commits on its own would leave it
        raise RuntimeError("stopped after the admin")

    monkeypatch.setattr(EngagementService, "_customer", stops)
    with pytest.raises(RuntimeError):
        door.service().ensure(match_id, _upsert())
    with _space(door, "ada-lovelace") as space:
        assert UserRepository(space).get_by_email(ADA) is not None
    assert door.sender.sent == []

    monkeypatch.setattr(EngagementService, "_customer", customer_step)
    answer = door.service().ensure(match_id, _upsert())

    assert answer.creato is True and answer.spazio_creato is True
    assert [mail.to for mail in door.sender.sent] == [ADA]


# --- report: a match's hours with their invoices (§ 2.4) -----------------------------


def _log(
    space: Session, deal_id: UUID, day: date, descrizione: str, *, fatturabile: bool = True
) -> UUID:
    """Eight hours on the deal, logged for the space's admin through the service: the
    admin has no password, so the service is the way in."""
    admin = UserRepository(space).get_by_email(ADA)
    assert admin is not None
    entry = TimeEntryService(space).create(
        TimeEntryCreate(
            deal_id=deal_id,
            user_id=admin.id,
            data=day,
            ore=Decimal("8.00"),
            descrizione=descrizione,
            fatturabile=fatturabile,
        ),
        Actor.system(),
    )
    return entry.id


def _invoice(space: Session, linked: EngagementRead, entries: list[UUID], **fields: Any) -> UUID:
    """An invoice of the deal's customer with one line, and `entries` bound to that
    line, written as rows: the report reads an invoice's state, not how it got there."""
    invoice = Invoice(customer_id=linked.customer_id, deal_id=linked.deal_id, **fields)
    space.add(invoice)
    space.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        numero_linea=1,
        descrizione="Ore di lavoro",
        quantita=Decimal("8"),
        prezzo_unitario=Decimal("50"),
        prezzo_totale=Decimal("400.00"),
        aliquota_iva=Decimal("22.00"),
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
    linked = door.service().ensure(match_id, _upsert())
    today = today_local(door.settings)
    first, second = today - timedelta(days=2), today - timedelta(days=1)
    with _space(door, linked.slug) as space:
        setup = _log(space, linked.deal_id, first, "Setup")
        api = _log(space, linked.deal_id, second, "API")
        _log(space, linked.deal_id, second, "Riunione interna", fatturabile=False)
        invoice_id = _invoice(
            space,
            linked,
            [setup, api],
            tipo="fattura",
            stato="emessa",
            anno=2026,
            numero=12,
            data_emissione=second,
        )

    report = door.service().report(match_id, da=first)

    assert report.slug == "ada-lovelace"
    assert report.deal_url == linked.deal_url
    assert report.deal == ReportDeal(
        id=linked.deal_id,
        nome=DEAL_NAME,
        tariffa_oraria=Decimal("50.000000"),
        ore_preventivate=Decimal("320.00"),
        stato="in corso",
    )
    issued = ReportInvoice(
        id=invoice_id,
        tipo="fattura",
        anno=2026,
        numero=12,
        stato="emessa",
        stato_pagamento="da_incassare",
        data=second,
    )
    assert [(g.data, g.ore, g.descrizione, g.fatturabile, g.fattura) for g in report.giorni] == [
        (first, Decimal("8.00"), "Setup", True, issued),
        (second, Decimal("8.00"), "API", True, issued),
        (second, Decimal("8.00"), "Riunione interna", False, None),
    ]
    assert report.fatture == [issued.model_copy(update={"ore": Decimal("16.00")})]
    # The wire shape the hub reads (spec § 2.4): hours at two places, the rate at six,
    # and no hours on an entry's own invoice.
    wire = report.model_dump(mode="json")
    assert (wire["totale_ore"], wire["ore_fatturate"], wire["ore_non_fatturate"]) == (
        "24.00",
        "16.00",
        "8.00",
    )
    assert wire["deal"]["tariffa_oraria"] == "50.000000"
    assert wire["deal"]["ore_preventivate"] == "320.00"
    assert wire["giorni"][0]["fattura"]["ore"] is None
    assert wire["fatture"][0]["ore"] == "16.00"
    assert wire["fatture"][0]["data"] == second.isoformat()


def test_report_counts_only_issued_invoices_as_billed(door: Door) -> None:
    """Billed is the CRM's own word for it (`billed_entry_ids`): a line of a `fattura`
    that is `emessa`. An entry on a proforma or on a draft still shows that invoice, and
    counts as not billed; an entry on an invoice deleted since shows none, as the CRM
    itself no longer shows that invoice. The invoices come newest first, the undated
    ones last."""
    match_id = uuid4()
    linked = door.service().ensure(match_id, _upsert())
    today = today_local(door.settings)
    days = [today - timedelta(days=n) for n in (3, 2, 1, 0)]
    with _space(door, linked.slug) as space:
        on_proforma, on_draft, on_issued, on_deleted = [
            _log(space, linked.deal_id, day, name)
            for day, name in zip(days, ("Analisi", "Sviluppo", "Rilascio", "Scartata"), strict=True)
        ]
        proforma = _invoice(space, linked, [on_proforma], tipo="proforma", stato="confermata")
        draft = _invoice(space, linked, [on_draft], tipo="fattura", stato="bozza")
        issued = _invoice(
            space,
            linked,
            [on_issued],
            tipo="fattura",
            stato="emessa",
            anno=2026,
            numero=7,
            data_emissione=days[2],
        )
        _invoice(
            space,
            linked,
            [on_deleted],
            tipo="fattura",
            stato="bozza",
            deleted_at=datetime.now(UTC),
        )

    report = door.service().report(match_id, da=days[0])

    assert [(g.descrizione, g.fattura.id if g.fattura else None) for g in report.giorni] == [
        ("Analisi", proforma),
        ("Sviluppo", draft),
        ("Rilascio", issued),
        ("Scartata", None),
    ]
    shown = report.giorni[0].fattura
    assert shown is not None
    assert (shown.tipo, shown.stato, shown.anno, shown.numero, shown.data, shown.ore) == (
        "proforma",
        "confermata",
        None,
        None,
        None,
        None,
    )
    assert (report.totale_ore, report.ore_fatturate, report.ore_non_fatturate) == (
        Decimal("32.00"),
        Decimal("8.00"),
        Decimal("24.00"),
    )
    assert [(f.id, f.ore) for f in report.fatture] == [
        (issued, Decimal("8.00")),
        (draft, Decimal("8.00")),
        (proforma, Decimal("8.00")),
    ]


def test_report_defaults_and_caps_the_period(door: Door, monkeypatch: pytest.MonkeyPatch) -> None:
    """`da` is the day the deal was created, in the CRM's zone (22:30 UTC on 1 October is
    already the 2nd in Rome), `a` is today by the CRM's clock; a period over 800 days,
    or one that ends before it starts, is refused."""
    match_id = uuid4()
    linked = door.service().ensure(match_id, _upsert())
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

    assert [g.data for g in report.giorni] == [date(2026, 10, 2), date(2026, 10, 31)]

    for da, a, reason in (
        (date(2024, 1, 1), date(2024, 1, 1) + timedelta(days=801), "al massimo 800 giorni"),
        (date(2026, 10, 2), date(2026, 10, 1), REVERSED),
    ):
        with pytest.raises(ValidationFailed) as refused:
            door.service().report(match_id, da=da, a=a)
        assert refused.value.details == {
            "entity": "engagement",
            "field": "periodo",
            "reason": reason,
        }
    # `a` alone before the deal's first day is the same reversed period.
    with pytest.raises(ValidationFailed) as refused:
        door.service().report(match_id, a=date(2026, 10, 1))
    assert refused.value.details["reason"] == REVERSED


def test_report_accepts_exactly_800_days(door: Door) -> None:
    """The hub walks 800-day windows whose bounds touch (`da + 800`, then `da + 801`):
    a span of exactly 800 days is accepted, and both of its ends are in it."""
    match_id = uuid4()
    linked = door.service().ensure(match_id, _upsert())
    a = today_local(door.settings)
    da = a - timedelta(days=800)
    with _space(door, linked.slug) as space:
        _log(space, linked.deal_id, da - timedelta(days=1), "Prima")
        _log(space, linked.deal_id, da, "Il primo giorno")
        _log(space, linked.deal_id, a, "L'ultimo giorno")

    report = door.service().report(match_id, da=da, a=a)

    assert [g.descrizione for g in report.giorni] == ["Il primo giorno", "L'ultimo giorno"]
    assert report.totale_ore == Decimal("16.00")


def test_report_of_unknown_match_is_not_found(door: Door) -> None:
    unknown = uuid4()
    with pytest.raises(NotFound) as missing:
        door.service().report(unknown)
    assert missing.value.details == {"entity": "engagement", "identifier": str(unknown)}

    # A row a call left before its deal existed has nothing to report either.
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
    linked = door.service().ensure(match_id, _upsert())
    with _space(door, linked.slug) as space:
        DealService(space).soft_delete(linked.deal_id, Actor.system())

    with pytest.raises(Conflict) as refused:
        door.service().report(match_id)

    assert refused.value.details["reason"] == GONE
    assert refused.value.details["match_id"] == str(match_id)


def test_report_refuses_without_public_url(door: Door, db_engine: Engine) -> None:
    """The report carries the deal's page, built from `PIGROCRM_PUBLIC_URL` like
    `ensure`'s links: without it the door answers as `ensure` does, before it looks for
    the match."""
    service = EngagementService(door.registry, _settings_for(db_engine, public_url=""), door.sender)

    with pytest.raises(ValidationFailed) as refused:
        service.report(uuid4())

    assert refused.value.details["field"] == "public_url"
