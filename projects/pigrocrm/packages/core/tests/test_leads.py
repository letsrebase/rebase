"""The leads route of the engagements door (spec 2026-10-30 § 2, § 8, REB-866):
`LeadService.file` files a hub company request in rebase's own space as a customer, a
contact and a deal.

Like `test_engagements.py`, the tests run on the container for real: the space is
provisioned by `TenantService.provision`, and the `door` fixture drops it on the way out.
The names are invented (Rossi, Acme).
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes.notes import texts
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session

import pigrocrm.core.engagements.leads as leads_module
from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.leads import (
    BUSY,
    KIND_FILED,
    KIND_LINKED,
    NO_SUCH_DEAL,
    NOT_CONFIGURED,
    LeadBusy,
    LeadService,
    LeadsSpaceNotConfigured,
    lead_marker,
)
from pigrocrm.core.engagements.schemas import LeadRead, LeadUpsert
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.schemas import PersonCreate
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.tenants import (
    TenantService,
    TenantSignup,
    ensure_tenants_database,
)
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url

SLUG = "rebase-lead-test"
MARIA = "maria.rossi@acme.it"


def _body(**parts: Any) -> LeadUpsert:
    """The body of spec § 3, with any top-level or `contact_person`/`deal` field overridden."""
    body: dict[str, Any] = {
        "legal_entity": "Acme S.r.l.",
        "contact_person": {
            "nome": "Maria",
            "last_name": "Rossi",
            "email": MARIA,
            "phone": "+39 02 1234567",
            "linkedin_url": "https://www.linkedin.com/in/maria-rossi",
        },
        "deal": {
            "nome": "Acme S.r.l.: Backend developer",
            "expected_value": "24000.00",
            "expected_close_date": "2099-01-15",
            "note": "Richiesta dell'azienda: cerca uno sviluppatore backend.",
        },
    }
    for key, value in parts.items():
        if isinstance(value, dict) and isinstance(body.get(key), dict):
            body[key] = {**body[key], **value}
        else:
            body[key] = value
    return LeadUpsert.model_validate(body)


@dataclass
class Door:
    settings: Settings
    registry: Engine

    def service(self) -> LeadService:
        return LeadService(self.registry, self.settings)


@pytest.fixture
def door(db_engine: Engine) -> Iterator[Door]:
    settings = Settings(
        database_url=db_engine.url.render_as_string(hide_password=False),
        public_url="https://pigro.test",
        leads_space=SLUG,
        _env_file=None,  # type: ignore[call-arg]
    )
    registry = ensure_tenants_database(settings)
    with session_factory(registry)() as session:
        TenantService(session, settings).provision(
            TenantSignup(slug=SLUG, nome="rebase", email="spazio@studio.it")
        )
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


@contextmanager
def _space(door: Door) -> Iterator[Session]:
    engine = create_engine(
        tenant_database_url(door.settings, tenant_database_name(SLUG)), future=True
    )
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _count(space: Session, model: type[Customer] | type[Person] | type[Deal]) -> int:
    return space.scalar(select(func.count()).select_from(model)) or 0


def _customer(space: Session, name: str = "Rossi Costruzioni S.r.l.") -> UUID:
    return CustomerService(space).create(CustomerCreate(legal_name=name), Actor.system()).id


def _person(space: Session, email: str, customer_id: UUID | None, nome: str = "Maria") -> UUID:
    return (
        PersonService(space)
        .create(
            PersonCreate(nome=nome, last_name="Rossi", email=email, customer_id=customer_id),
            Actor.system(),
        )
        .id
    )


def _deal(space: Session, customer_id: UUID, note: str | None = None) -> UUID:
    return (
        DealService(space)
        .create(
            DealCreate(nome="Trattativa in corso", customer_id=customer_id, note=note),
            Actor.system(),
        )
        .id
    )


def test_first_call_files_customer_person_and_deal(door: Door) -> None:
    request_id = uuid4()
    answer = door.service().file(request_id, _body())

    assert answer.created is True
    assert answer.deal_url == f"https://pigro.test/{SLUG}/app/deal/{answer.deal_id}"
    with _space(door) as space:
        customer = space.get(Customer, answer.customer_id)
        person = space.get(Person, answer.person_id)
        deal = space.get(Deal, answer.deal_id)
        assert customer is not None and person is not None and deal is not None
        assert customer.legal_name == "Acme S.r.l."
        [customer_note] = texts(space, "customer", customer.id)
        assert "hub" in customer_note
        assert (person.nome, person.last_name, person.email) == ("Maria", "Rossi", MARIA)
        assert person.phone == "+39 02 1234567"
        assert person.linkedin == "https://www.linkedin.com/in/maria-rossi"
        assert person.customer_id == customer.id
        assert deal.customer_id == customer.id
        assert deal.nome == "Acme S.r.l.: Backend developer"
        assert deal.expected_value == Decimal("24000.00")
        assert deal.expected_close_date == date(2099, 1, 15)
        assert deal.hourly_rate is None and deal.budgeted_hours is None
        assert deal.owner_id is None
        # The hub's sentence is the first note, written by rebase; the reference a retry
        # finds the deal by is in `rebase_refs` and in no note.
        [deal_note] = texts(space, "deal", deal.id)
        assert "cerca uno sviluppatore backend" in deal_note
        assert lead_marker(request_id) not in deal_note
        assert deal.rebase_refs == [lead_marker(request_id)]


def test_every_write_is_recorded_as_rebase(door: Door) -> None:
    answer = door.service().file(uuid4(), _body())
    with _space(door) as space:
        ids = {answer.customer_id, answer.person_id, answer.deal_id}
        rows = [r for r in space.scalars(select(Activity)).all() if r.entity_id in ids]
        assert {(r.entity_type, r.kind) for r in rows} >= {
            ("customer", "created"),
            ("person", "created"),
            ("deal", "created"),
            ("deal", KIND_FILED),
        }
        assert {r.actor_type for r in rows} == {"rebase"}
        assert all(r.actor_id is None for r in rows)
        filed = [r for r in rows if r.kind == KIND_FILED]
        assert [r.entity_id for r in filed] == [answer.deal_id]


def test_a_second_put_for_the_request_writes_nothing(door: Door) -> None:
    request_id = uuid4()
    first = door.service().file(request_id, _body())
    with _space(door) as space:
        before = space.scalar(select(func.count()).select_from(Activity))
    second = door.service().file(request_id, _body())

    assert second.created is False
    assert (second.customer_id, second.person_id, second.deal_id) == (
        first.customer_id,
        first.person_id,
        first.deal_id,
    )
    with _space(door) as space:
        assert (_count(space, Customer), _count(space, Person), _count(space, Deal)) == (1, 1, 1)
        assert space.scalar(select(func.count()).select_from(Activity)) == before


def test_an_existing_person_is_reused_whatever_the_case_of_the_address(door: Door) -> None:
    with _space(door) as space:
        customer_id = _customer(space)
        person_id = _person(space, MARIA, customer_id)
        # The older, mixed-case address of the same person at another live customer:
        # the oldest one wins, so this one is created after and loses.
        other_customer = _customer(space, "Altra S.r.l.")
        _person(space, MARIA, other_customer, nome="Mariella")

    answer = door.service().file(uuid4(), _body(contact_person={"email": MARIA.upper()}))

    assert answer.created is True
    assert (answer.customer_id, answer.person_id) == (customer_id, person_id)
    with _space(door) as space:
        assert _count(space, Customer) == 2 and _count(space, Person) == 2
        deal = space.get(Deal, answer.deal_id)
        assert deal is not None and deal.customer_id == customer_id


def test_a_different_typed_name_is_said_in_the_deal_note(door: Door) -> None:
    with _space(door) as space:
        _person(space, MARIA, _customer(space, "Rossi Costruzioni S.r.l."))

    answer = door.service().file(uuid4(), _body(legal_entity="Acme S.r.l."))

    with _space(door) as space:
        deal = space.get(Deal, answer.deal_id)
        assert deal is not None
        [deal_note] = texts(space, "deal", deal.id)
        assert "Nome indicato nella richiesta: Acme S.r.l." in deal_note
        assert "Rossi Costruzioni S.r.l." in deal_note


def test_the_same_typed_name_adds_no_line(door: Door) -> None:
    with _space(door) as space:
        _person(space, MARIA, _customer(space, "acme  s.r.l."))

    answer = door.service().file(uuid4(), _body(legal_entity="Acme S.r.l."))

    with _space(door) as space:
        deal = space.get(Deal, answer.deal_id)
        assert deal is not None
        [deal_note] = texts(space, "deal", deal.id)
        assert "Nome indicato" not in deal_note


def test_an_archived_customer_gives_a_new_customer_and_moves_the_person(door: Door) -> None:
    with _space(door) as space:
        old_customer = _customer(space)
        person_id = _person(space, MARIA, old_customer)
        CustomerService(space).soft_delete(old_customer, Actor.system())

    answer = door.service().file(uuid4(), _body())

    assert answer.person_id == person_id
    assert answer.customer_id != old_customer
    with _space(door) as space:
        assert _count(space, Customer) == 2 and _count(space, Person) == 1
        person = space.get(Person, person_id)
        assert person is not None and person.customer_id == answer.customer_id


def test_a_person_with_no_customer_is_attached_to_a_new_one(door: Door) -> None:
    with _space(door) as space:
        person_id = _person(space, MARIA, None)

    answer = door.service().file(uuid4(), _body())

    assert answer.person_id == person_id
    with _space(door) as space:
        assert _count(space, Customer) == 1 and _count(space, Person) == 1
        person = space.get(Person, person_id)
        assert person is not None and person.customer_id == answer.customer_id


def test_an_archived_person_is_not_reused(door: Door) -> None:
    with _space(door) as space:
        person_id = _person(space, MARIA, _customer(space))
        PersonService(space).soft_delete(person_id, Actor.system())

    answer = door.service().file(uuid4(), _body())

    assert answer.person_id != person_id
    with _space(door) as space:
        assert _count(space, Customer) == 2


def test_the_same_email_and_another_request_is_a_second_deal_on_the_same_customer(
    door: Door,
) -> None:
    first = door.service().file(uuid4(), _body())
    second = door.service().file(uuid4(), _body(deal={"nome": "Acme S.r.l.: Frontend"}))

    assert second.created is True
    assert second.deal_id != first.deal_id
    assert (second.customer_id, second.person_id) == (first.customer_id, first.person_id)
    with _space(door) as space:
        assert (_count(space, Customer), _count(space, Person), _count(space, Deal)) == (1, 1, 2)


def test_a_new_deal_is_made_even_when_the_customer_has_an_open_one(door: Door) -> None:
    with _space(door) as space:
        customer_id = _customer(space)
        _person(space, MARIA, customer_id)
        open_deal = _deal(space, customer_id)

    answer = door.service().file(uuid4(), _body())

    assert answer.created is True and answer.deal_id != open_deal
    with _space(door) as space:
        untouched = space.get(Deal, open_deal)
        assert untouched is not None and untouched.rebase_refs == []
        assert texts(space, "deal", open_deal) == []


def test_a_customer_is_never_matched_by_name(door: Door) -> None:
    with _space(door) as space:
        _customer(space, "Acme S.r.l.")

    answer = door.service().file(uuid4(), _body(legal_entity="Acme S.r.l."))

    with _space(door) as space:
        assert _count(space, Customer) == 2
        deal = space.get(Deal, answer.deal_id)
        assert deal is not None
        assert deal.customer_id == answer.customer_id


def test_an_explicit_deal_is_linked_with_an_activity_and_nothing_is_created(door: Door) -> None:
    with _space(door) as space:
        customer_id = _customer(space)
        person_id = _person(space, MARIA, customer_id)
        deal_id = _deal(space, customer_id, note="Trattativa in corso con Rossi.")
        counts = (_count(space, Customer), _count(space, Person), _count(space, Deal))
    request_id = uuid4()

    answer = door.service().file(request_id, _body(deal_id=str(deal_id)))

    assert answer.created is False
    assert (answer.customer_id, answer.person_id, answer.deal_id) == (
        customer_id,
        person_id,
        deal_id,
    )
    with _space(door) as space:
        assert (_count(space, Customer), _count(space, Person), _count(space, Deal)) == counts
        deal = space.get(Deal, deal_id)
        assert deal is not None
        # The deal's own note is left as it was; the request's sentence is added to the
        # thread after it (written by rebase), and the reference with it.
        assert texts(space, "deal", deal_id) == [
            "Trattativa in corso con Rossi.",
            "Richiesta dell'azienda: cerca uno sviluppatore backend.",
        ]
        assert deal.rebase_refs == [lead_marker(request_id)]
        linked = space.scalars(select(Activity).where(Activity.kind == KIND_LINKED)).all()
        assert [(a.entity_type, a.entity_id, a.actor_type) for a in linked] == [
            ("deal", deal_id, "rebase")
        ]
        assert linked[0].payload["request"] == str(request_id)
        # The prospect's free text lives in the thread only: editing or deleting the note
        # leaves no copy in the append-only timeline.
        assert "note" not in linked[0].payload
        assert "cerca uno sviluppatore" not in str(linked[0].payload)

    # A retry of the link finds the marker and records nothing more.
    again = door.service().file(request_id, _body(deal_id=str(deal_id)))
    assert again.created is False and again.deal_id == deal_id
    with _space(door) as space:
        assert len(space.scalars(select(Activity).where(Activity.kind == KIND_LINKED)).all()) == 1
        assert len(texts(space, "deal", deal_id)) == 2, "the note is not added twice"
    # And the request without the deal id finds the same deal by its marker.
    plain = door.service().file(request_id, _body())
    assert plain.created is False and plain.deal_id == deal_id


def test_an_explicit_deal_that_is_not_in_the_space_is_refused(door: Door) -> None:
    with pytest.raises(ValidationFailed) as refused:
        door.service().file(uuid4(), _body(deal_id=str(uuid4())))
    assert refused.value.details["field"] == "deal_id"
    assert refused.value.details["reason"] == NO_SUCH_DEAL
    with _space(door) as space:
        assert (_count(space, Customer), _count(space, Person), _count(space, Deal)) == (0, 0, 0)


def test_an_archived_explicit_deal_is_refused(door: Door) -> None:
    with _space(door) as space:
        deal_id = _deal(space, _customer(space))
        DealService(space).soft_delete(deal_id, Actor.system())
    with pytest.raises(ValidationFailed):
        door.service().file(uuid4(), _body(deal_id=str(deal_id)))


def test_an_empty_leads_space_is_not_configured(door: Door) -> None:
    settings = door.settings.model_copy(update={"leads_space": ""})
    with pytest.raises(LeadsSpaceNotConfigured) as refused:
        LeadService(door.registry, settings).file(uuid4(), _body())
    assert refused.value.args == (NOT_CONFIGURED,)


def test_a_space_the_registry_does_not_have_is_not_configured(door: Door) -> None:
    settings = door.settings.model_copy(update={"leads_space": "non-esiste"})
    with pytest.raises(LeadsSpaceNotConfigured):
        LeadService(door.registry, settings).file(uuid4(), _body())


def test_the_caller_never_picks_the_space(door: Door) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 -- pydantic's ValidationError
        _body(slug="altro-spazio")


def test_without_public_url_the_route_is_not_configured(door: Door) -> None:
    settings = door.settings.model_copy(update={"public_url": ""})
    with pytest.raises(ValidationFailed) as refused:
        LeadService(door.registry, settings).file(uuid4(), _body())
    assert refused.value.details["field"] == "public_url"


def test_a_held_lock_times_out_as_busy(door: Door, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(leads_module, "LOCK_TIMEOUT", "200ms")
    with door.registry.connect() as holder:
        key = f"{leads_module.LOCK_PREFIX}{MARIA}"
        holder.execute(text("SELECT pg_advisory_lock(hashtext(:e))"), {"e": key})
        try:
            with pytest.raises(LeadBusy) as refused:
                door.service().file(uuid4(), _body())
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(hashtext(:e))"), {"e": key})
            holder.commit()

    assert refused.value.args == (BUSY,)
    assert MARIA not in str(refused.value)
    with _space(door) as space:
        assert (_count(space, Customer), _count(space, Deal)) == (0, 0)


def test_two_requests_of_one_person_at_once_make_one_customer_and_two_deals(door: Door) -> None:
    barrier = threading.Barrier(2)
    answers: list[LeadRead] = []
    errors: list[BaseException] = []

    def file(name: str) -> None:
        try:
            barrier.wait(timeout=30)
            answers.append(door.service().file(uuid4(), _body(deal={"nome": name})))
        except BaseException as exc:  # noqa: BLE001 -- carried to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=file, args=(n,), daemon=True) for n in ("Uno", "Due")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
        assert not thread.is_alive()

    assert errors == []
    assert len({a.customer_id for a in answers}) == 1
    assert len({a.person_id for a in answers}) == 1
    assert len({a.deal_id for a in answers}) == 2
    with _space(door) as space:
        assert (_count(space, Customer), _count(space, Person), _count(space, Deal)) == (1, 1, 2)


def test_the_lock_is_released_after_a_refusal(door: Door) -> None:
    with pytest.raises(ValidationFailed):
        door.service().file(uuid4(), _body(deal_id=str(uuid4())))
    # Were the lock still held by a leaked connection, this would time out as busy.
    assert door.service().file(uuid4(), _body()).created is True


def test_a_retry_after_the_contact_was_archived_names_nobody(door: Door) -> None:
    request_id = uuid4()
    first = door.service().file(request_id, _body())
    assert first.person_id is not None
    with _space(door) as space:
        PersonService(space).soft_delete(first.person_id, Actor.system())

    again = door.service().file(request_id, _body())

    assert again.created is False and again.deal_id == first.deal_id
    assert again.person_id is None


def test_a_person_of_another_customer_is_not_named_on_a_link(door: Door) -> None:
    with _space(door) as space:
        other = _customer(space, "Altra S.r.l.")
        _person(space, MARIA, other)
        deal_id = _deal(space, _customer(space))

    answer = door.service().file(uuid4(), _body(deal_id=str(deal_id)))

    assert answer.person_id is None
