"""rebase's engagements door (spec 2026-09-25 § 2.2, § 2.3): the registry table
`rebase_engagements`, and `EngagementService.ensure`, which sets up a freelancer's
space, the customer «rebase» and the deal of one letter, once per hub match.

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
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

import pigrocrm.core.engagements as engagements_package
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.schemas import EngagementRead, EngagementUpsert
from pigrocrm.core.engagements.service import EngagementService, deal_marker, deal_name
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.tenants import Tenant, TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url

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


def test_two_matches_one_email_share_one_space(door: Door) -> None:
    """Two letters of the same freelancer activating together (spec § 3.10): the lock
    by address serialises the whole call, so the second finds the space the first
    opened instead of opening another under `ada-lovelace-2`."""
    barrier = threading.Barrier(2)
    answers: dict[str, EngagementRead] = {}
    errors: list[BaseException] = []

    def activate(numero: str) -> None:
        try:
            barrier.wait(timeout=30)
            answers[numero] = door.service().ensure(uuid4(), _upsert(lettera={"numero": numero}))
        except BaseException as exc:  # noqa: BLE001 -- carried to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=activate, args=(n,)) for n in ("1/2026", "2/2026")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)

    assert errors == []
    assert _owned(door) == ["ada-lovelace"]
    assert {a.slug for a in answers.values()} == {"ada-lovelace"}
    assert sorted(a.spazio_creato for a in answers.values()) == [False, True]
    assert answers["1/2026"].customer_id == answers["2/2026"].customer_id
    assert answers["1/2026"].deal_id != answers["2/2026"].deal_id
    with _space(door, "ada-lovelace") as space:
        assert _count(space, Customer) == 1
        assert _count(space, Deal) == 2
    assert len(door.sender.sent) == 1


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
