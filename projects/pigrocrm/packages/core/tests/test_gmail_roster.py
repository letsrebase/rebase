from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.gmail.roster import AddressRoster, EntityRef, address_key
from pigrocrm.core.people.models import Person


def _resolve(session: Session, address: str) -> tuple[EntityRef, ...]:
    return AddressRoster(session).resolve_many([address]).get(address_key(address), ())


def _customer(session: Session, *, legal_name: str, email: str | None) -> Customer:
    customer = Customer(legal_name=legal_name, email=email)
    session.add(customer)
    session.flush()
    return customer


def test_roster_collects_both_tables_lowercased_and_deduplicated(db_session: Session) -> None:
    customer = _customer(db_session, legal_name="Acme", email="Info@Acme.IT")
    db_session.add(
        Person(first_name="Ada", last_name="Byron", email="ada@acme.it", customer_id=customer.id)
    )
    # The same address on a person and on a customer is one address, not two: a `q`
    # that repeats a clause wastes the length budget the 20-address batch depends on.
    db_session.add(
        Person(first_name="Bob", last_name="Rossi", email="info@acme.it", customer_id=customer.id)
    )
    db_session.flush()

    assert AddressRoster(db_session).known_addresses() == ("ada@acme.it", "info@acme.it")


def test_roster_ignores_soft_deleted_and_null_addresses(db_session: Session) -> None:
    kept = _customer(db_session, legal_name="Kept", email="kept@example.it")
    gone = _customer(db_session, legal_name="Gone", email="gone@example.it")
    gone.deleted_at = datetime.now(UTC)
    _customer(db_session, legal_name="Blank", email=None)
    db_session.add(
        Person(
            first_name="Via",
            last_name="Via",
            email="via@example.it",
            customer_id=kept.id,
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    assert AddressRoster(db_session).known_addresses() == ("kept@example.it",)


def test_resolving_returns_every_entity_the_address_touches(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    customer = _customer(db_session, legal_name="Acme", email="info@acme.it")
    person = Person(
        first_name="Ada", last_name="Byron", email="ada@acme.it", customer_id=customer.id
    )
    db_session.add(person)
    db_session.flush()
    deal = Deal(name="Rinnovo", customer_id=customer.id, pipeline_stage_id=seeded_open_stage_id)
    db_session.add(deal)
    db_session.flush()

    # One email concerns the person, her customer, and that customer's open deals at
    # once. This is why gmail_message_links is many-to-many and not three nullable
    # foreign keys: a single FK would force a choice the data does not support.
    assert set(_resolve(db_session, "Ada@Acme.it")) == {
        EntityRef("person", person.id),
        EntityRef("customer", customer.id),
        EntityRef("deal", deal.id),
    }


def test_resolving_ignores_soft_deleted_entities(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    """A deleted person, customer or deal is not a reason to file an email against it.
    Without this, archiving a contact would leave their conversations linked to a row
    the rest of the CRM refuses to show, and the link would be unreachable rubbish."""
    live_customer = _customer(db_session, legal_name="Viva", email="shared@acme.it")
    dead_customer = _customer(db_session, legal_name="Morta", email="shared@acme.it")
    dead_customer.deleted_at = datetime.now(UTC)
    dead_person = Person(
        first_name="Ex",
        last_name="Collega",
        email="shared@acme.it",
        customer_id=live_customer.id,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(dead_person)
    db_session.flush()
    dead_deal = Deal(
        name="Chiuso",
        customer_id=live_customer.id,
        pipeline_stage_id=seeded_open_stage_id,
        deleted_at=datetime.now(UTC),
    )
    db_session.add(dead_deal)
    db_session.flush()

    assert _resolve(db_session, "shared@acme.it") == (EntityRef("customer", live_customer.id),)


def test_resolving_does_not_reach_a_deleted_customer_through_a_live_person(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    """`people.customer_id` is a foreign key, not a liveness check: it keeps pointing at
    a customer after that customer is archived. Following it without re-checking
    `deleted_at` would file the message against the archived company *and* pull in its
    deals, which is the one path a soft-delete filter on the two direct lookups misses.
    """
    dead_customer = _customer(db_session, legal_name="Archiviata", email=None)
    dead_customer.deleted_at = datetime.now(UTC)
    person = Person(
        first_name="Ada", last_name="Byron", email="ada@acme.it", customer_id=dead_customer.id
    )
    db_session.add(person)
    db_session.flush()
    db_session.add(
        Deal(
            name="Vecchio",
            customer_id=dead_customer.id,
            pipeline_stage_id=seeded_open_stage_id,
        )
    )
    db_session.flush()

    assert _resolve(db_session, "ada@acme.it") == (EntityRef("person", person.id),)


def test_resolving_is_empty_for_an_address_nobody_owns(db_session: Session) -> None:
    _customer(db_session, legal_name="Acme", email="info@acme.it")
    db_session.flush()

    assert _resolve(db_session, "stranger@example.com") == ()


def test_resolving_of_a_blank_address_touches_nothing(db_session: Session) -> None:
    """A message with an empty From is not a message about everybody. resolving ""
    must not fall through to a `lower(email) = ''` that would match a row somebody
    saved with an empty string instead of NULL."""
    _customer(db_session, legal_name="Vuoto", email="")
    db_session.flush()

    assert _resolve(db_session, "   ") == ()


def test_customers_email_is_indexed_like_people_email(db_session: Session) -> None:
    """people.email has had an index since slice 1; customers.email has not, and the
    relevance resolution queries both on every message. Same query shape, same cost,
    so the same index."""
    columns = {
        tuple(index["column_names"])
        for index in inspect(db_session.get_bind()).get_indexes("customers")
    }
    assert ("email",) in columns


def _statements(session: Session) -> list[str]:
    """A recorder of the SQL the session's connection runs from now on."""
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *args: object) -> None:
        seen.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", record)
    return seen


def test_resolving_many_addresses_costs_a_fixed_number_of_queries(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    """One `resolve` per address cost up to four queries *each*, so a thread with thirty
    people copied on it was a hundred round trips. Batched, it is four whatever the
    number, and the answer for each address is the one the single lookup gave."""
    customer = _customer(db_session, legal_name="Acme", email="info@acme.it")
    db_session.add(
        Deal(name="Rinnovo", customer_id=customer.id, pipeline_stage_id=seeded_open_stage_id)
    )
    people = [
        Person(first_name=f"P{i}", last_name="X", email=f"p{i}@acme.it", customer_id=customer.id)
        for i in range(30)
    ]
    db_session.add_all(people)
    db_session.flush()
    addresses = [person.email for person in people if person.email]
    statements = _statements(db_session)

    resolved = AddressRoster(db_session).resolve_many([*addresses, "stranger@example.com"])

    assert len(statements) <= 4
    assert set(resolved) == set(addresses)
    for person in people:
        refs = set(resolved[address_key(person.email or "")])
        assert EntityRef("person", person.id) in refs
        assert EntityRef("customer", customer.id) in refs
    assert resolved["p0@acme.it"] != resolved["p1@acme.it"]


def test_resolving_nothing_runs_no_query(db_session: Session) -> None:
    statements = _statements(db_session)

    assert AddressRoster(db_session).resolve_many(["", "   "]) == {}
    assert statements == []


def test_known_among_answers_only_for_the_candidates_it_is_given(db_session: Session) -> None:
    customer = _customer(db_session, legal_name="Acme", email="Info@Acme.it")
    db_session.add(
        Person(first_name="Ada", last_name="B", email="ada@acme.it", customer_id=customer.id)
    )
    gone = _customer(db_session, legal_name="Gone", email="gone@acme.it")
    gone.deleted_at = datetime.now(UTC)
    db_session.flush()

    known = AddressRoster(db_session).known_among(
        ["INFO@acme.it", "ada@acme.it", "gone@acme.it", "nobody@acme.it", ""]
    )

    assert known == {"info@acme.it", "ada@acme.it"}


def test_domains_taken_are_the_domains_the_crm_files_under_a_customer(
    db_session: Session,
) -> None:
    """A customer's domain is the website's host, the email's domain, or a person's
    attached to a live customer: never a webmail one, never a deleted customer's, never an
    orphan person's."""
    acme = _customer(db_session, legal_name="Acme", email="info@acme.it")
    acme.website = "https://www.acme.com/chi-siamo"
    db_session.add(
        Person(first_name="Ada", last_name="B", email="ada@studio-rossi.it", customer_id=acme.id)
    )
    db_session.add(
        Person(first_name="Bob", last_name="B", email="bob@gmail.com", customer_id=acme.id)
    )
    orphan = Person(first_name="Zed", last_name="Z", email="zed@senza-cliente.it")
    db_session.add(orphan)
    gone = _customer(db_session, legal_name="Gone", email="gone@archiviata.it")
    gone.deleted_at = datetime.now(UTC)
    db_session.flush()
    roster = AddressRoster(db_session)
    candidates = [
        "acme.it",
        "acme.com",
        "studio-rossi.it",
        "gmail.com",
        "senza-cliente.it",
        "archiviata.it",
        "mai-visto.it",
    ]

    taken = roster.domains_taken(candidates)

    assert taken == {"acme.it", "acme.com", "studio-rossi.it"}
