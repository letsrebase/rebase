"""Residuo R9, closed for four entities.

The `sort` parameter is a whitelist and not a column name, and that is a security
property, not tidiness: a column name taken from a query string and interpolated into
`ORDER BY` is an injection point, and one taken from a query string and passed to
`getattr` on a model is an information leak (`ORDER BY password_hash` orders by a secret
even though it never returns it).

Two of the tests below carry the weight, and neither is in the brief.

**Ties.** A keyset over a non-unique column is only correct because of the
`col == value AND id <dir> row_id` arm of the predicate. A fixture whose sort values are
all distinct never reaches that arm, so it cannot fail when the arm is missing, when its
comparison is inverted, or when the tie-break direction does not follow the scan
direction. `test_paging_over_a_tied_sort_column_...` deliberately gives four customers
two names between them, and runs `limit=1` so that every step of the walk is a resume
from inside a tie.

**Both directions, everywhere.** `desc` is not a mirror of `asc` here: `order_by` puts
nulls last in *both* directions, so the null tail is reached by the same
`OR col IS NULL` arm going forwards and backwards, while the id tie-break flips. Every
paging test is parametrised over the direction for that reason.
"""

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.schemas import (
    CUSTOMER_SORTS,
    CustomerCreate,
    CustomerListQuery,
    CustomerUpdate,
)
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import SortDirection, decode_cursor
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.documents.schemas import DocumentListQuery
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.schemas import PersonListQuery
from pigrocrm.core.people.service import PersonService

ADMIN = Actor.system()

BOTH_DIRECTIONS = pytest.mark.parametrize("direction", ["asc", "desc"])


def _customer(service: CustomerService, ragione_sociale: str) -> UUID:
    return service.create(CustomerCreate(ragione_sociale=ragione_sociale), ADMIN).id


def _walk(
    service: CustomerService, sort: str, direction: SortDirection, limit: int
) -> list[tuple[str, UUID]]:
    """Every page of an ordered scan, flattened. The walk is the test: a keyset defect
    shows up as a repeated row or a missing one, never as a wrong single page."""
    seen: list[tuple[str, UUID]] = []
    cursor: str | None = None
    while True:
        page = service.list(
            CustomerListQuery(sort=sort, dir=direction, limit=limit, cursor=cursor), ADMIN
        )
        seen.extend((c.ragione_sociale, c.id) for c in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
        # A cursor that never advances would otherwise spin here forever rather than
        # fail, and a hung suite is a worse diagnostic than a red one.
        assert len(seen) <= 100, "the cursor is not advancing"
    return seen


def test_default_sort_reproduces_creation_order(db_session: Session) -> None:
    """The compatibility guarantee that keeps this a cursor-type change and nothing
    more: a caller that sends no `sort` sees exactly the page it saw before."""
    service = CustomerService(db_session)
    for name in ("Terza", "Prima", "Seconda"):
        _customer(service, name)

    page = service.list(CustomerListQuery(limit=10), ADMIN)
    assert [c.ragione_sociale for c in page.items] == ["Terza", "Prima", "Seconda"]


def test_sorting_by_the_identifying_column_ascending_and_descending(
    db_session: Session,
) -> None:
    service = CustomerService(db_session)
    for name in ("Gamma", "Alfa", "Beta"):
        _customer(service, name)

    ascending = service.list(CustomerListQuery(sort="ragione_sociale", dir="asc", limit=10), ADMIN)
    descending = service.list(
        CustomerListQuery(sort="ragione_sociale", dir="desc", limit=10), ADMIN
    )

    assert [c.ragione_sociale for c in ascending.items] == ["Alfa", "Beta", "Gamma"]
    assert [c.ragione_sociale for c in descending.items] == ["Gamma", "Beta", "Alfa"]


def test_an_unknown_sort_key_is_refused_by_name(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as caught:
        CustomerService(db_session).list(CustomerListQuery(sort="note", limit=10), ADMIN)
    assert caught.value.details["field"] == "sort"
    assert "ragione_sociale" in caught.value.details["expected"]


def test_a_sort_key_that_is_a_real_column_but_not_whitelisted_is_still_refused(
    db_session: Session,
) -> None:
    """`note` above could be dismissed as a typo. `deleted_at` is a real column on the
    model, so this is the test that proves the check is a whitelist and not a
    `hasattr`."""
    with pytest.raises(ValidationFailed) as caught:
        CustomerService(db_session).list(CustomerListQuery(sort="deleted_at", limit=10), ADMIN)
    assert caught.value.details["field"] == "sort"


def test_next_cursor_is_an_opaque_string_carrying_the_sort_value(
    db_session: Session,
) -> None:
    service = CustomerService(db_session)
    for name in ("Alfa", "Beta", "Gamma"):
        _customer(service, name)

    first = service.list(CustomerListQuery(sort="ragione_sociale", dir="asc", limit=2), ADMIN)
    assert first.next_cursor is not None
    assert isinstance(first.next_cursor, str)

    spec = CUSTOMER_SORTS.resolve("ragione_sociale")
    value, row_id = decode_cursor(spec, first.next_cursor)
    assert value == "Beta"
    assert row_id == first.items[-1].id


def test_a_cursor_from_one_sort_is_refused_when_the_caller_changes_sort(
    db_session: Session,
) -> None:
    """Replaying `next_cursor` against a different `sort` would otherwise compare a
    company name against a timestamp and return an arbitrary page. The check lives in
    `decode_cursor`; this asserts the service actually routes through it."""
    service = CustomerService(db_session)
    for name in ("Alfa", "Beta", "Gamma"):
        _customer(service, name)

    first = service.list(CustomerListQuery(sort="ragione_sociale", limit=2), ADMIN)
    assert first.next_cursor is not None

    with pytest.raises(ValidationFailed) as caught:
        service.list(CustomerListQuery(sort="created_at", limit=2, cursor=first.next_cursor), ADMIN)
    assert caught.value.details["field"] == "cursor"


@BOTH_DIRECTIONS
def test_paging_with_the_cursor_returns_the_rest_exactly_once(
    db_session: Session, direction: SortDirection
) -> None:
    service = CustomerService(db_session)
    for name in ("Alfa", "Beta", "Gamma", "Delta", "Epsilon"):
        _customer(service, name)

    seen = [name for name, _ in _walk(service, "ragione_sociale", direction, limit=2)]

    expected = ["Alfa", "Beta", "Delta", "Epsilon", "Gamma"]
    assert seen == (expected if direction == "asc" else list(reversed(expected)))


@BOTH_DIRECTIONS
def test_paging_over_a_tied_sort_column_returns_every_row_exactly_once(
    db_session: Session, direction: SortDirection
) -> None:
    """The test the brief does not have, and the only one that can fail when the
    `col == value AND id <dir> row_id` arm of `keyset_predicate` is missing or is
    comparing in the wrong direction.

    `ragione_sociale` is not unique, and two customers sharing a name is the ordinary
    case, not the pathological one -- a franchise, or the same company entered twice.
    `limit=1` makes every single step of the walk a resume from inside a tie.
    """
    service = CustomerService(db_session)
    ids = [_customer(service, name) for name in ("Rossi", "Rossi", "Bianchi", "Bianchi")]
    rossi_first, rossi_second, bianchi_first, bianchi_second = ids

    seen = _walk(service, "ragione_sociale", direction, limit=1)

    if direction == "asc":
        assert seen == [
            ("Bianchi", bianchi_first),
            ("Bianchi", bianchi_second),
            ("Rossi", rossi_first),
            ("Rossi", rossi_second),
        ]
    else:
        # The tie-break follows the direction of travel, so the two Bianchi come back
        # newest-first too -- not ascending-by-id inside a descending scan.
        assert seen == [
            ("Rossi", rossi_second),
            ("Rossi", rossi_first),
            ("Bianchi", bianchi_second),
            ("Bianchi", bianchi_first),
        ]


@BOTH_DIRECTIONS
def test_paging_a_nullable_sort_column_reaches_the_null_tail(
    db_session: Session, direction: SortDirection
) -> None:
    """`people.cognome` is nullable, and the null tail is the half that gets lost:
    without the `OR col IS NULL` arm the walk stops at the last non-null row and the
    two nameless people are simply never returned. Nulls come last in *both*
    directions, which is what `order_by` declares and what the descending index exists
    to serve."""
    for cognome in ("Bianchi", None, "Rossi", None):
        db_session.add(Person(nome="Marco", cognome=cognome, custom_fields={}))
    db_session.flush()

    service = PersonService(db_session)
    seen: list[str | None] = []
    cursor: str | None = None
    while True:
        page = service.list(
            PersonListQuery(sort="cognome", dir=direction, limit=1, cursor=cursor), ADMIN
        )
        seen.extend(p.cognome for p in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
        assert len(seen) <= 100, "the cursor is not advancing"

    named = ["Bianchi", "Rossi"] if direction == "asc" else ["Rossi", "Bianchi"]
    assert seen == [*named, None, None]


def test_sorting_by_updated_at_puts_the_most_recently_touched_row_first(
    db_session: Session,
) -> None:
    """`updated_at` is the only whitelisted column the caller does not write directly,
    so the assertion is on a real edit rather than on a hand-set timestamp: touching
    the *oldest* row must move it to the front of a descending scan."""
    service = CustomerService(db_session)
    oldest = _customer(service, "Alfa")
    _customer(service, "Beta")
    _customer(service, "Gamma")

    service.update(oldest, CustomerUpdate(telefono="toccato"), ADMIN)

    newest_first = service.list(CustomerListQuery(sort="updated_at", dir="desc", limit=10), ADMIN)
    assert newest_first.items[0].id == oldest


def test_documents_can_be_searched_by_title(db_session: Session) -> None:
    """The branch spec §8.1 assumes exists and `DocumentRepository.list` did not have.
    Task A13's "vedi tutti" link lands on it."""
    customer = Customer(ragione_sociale="Cliente", nazione="IT", custom_fields={})
    db_session.add(customer)
    db_session.flush()
    for titolo in ("Offerta impianti 2026", "Verbale riunione", "Offerta_speciale"):
        db_session.add(
            Document(
                customer_id=customer.id,
                tipo="documento",
                titolo=titolo,
                versione_corrente=1,
                custom_fields={},
            )
        )
    db_session.flush()

    repo = DocumentRepository(db_session)
    found = repo.list(DocumentListQuery(search="offerta", limit=10))
    assert sorted(d.titolo for d in found) == ["Offerta impianti 2026", "Offerta_speciale"]

    # `_` is a LIKE metacharacter: unescaped, this term would also match
    # "Offerta speciale" or "OffertaXspeciale". `escape_like` is what stops that, and
    # task A2 measured that keeping the `ESCAPE` clause costs the trigram index
    # nothing -- the planner folds it into the same constant pattern.
    literal = repo.list(DocumentListQuery(search="offerta_speciale", limit=10))
    assert [d.titolo for d in literal] == ["Offerta_speciale"]

    # The metacharacter is neutralised, not stripped: a title that really does contain
    # a space where the search term has an underscore must *not* match.
    db_session.add(
        Document(
            customer_id=customer.id,
            tipo="documento",
            titolo="Offerta speciale",
            versione_corrente=1,
            custom_fields={},
        )
    )
    db_session.flush()
    still_literal = repo.list(DocumentListQuery(search="offerta_speciale", limit=10))
    assert [d.titolo for d in still_literal] == ["Offerta_speciale"]


def test_documents_are_ordered_and_paged_like_the_other_three(db_session: Session) -> None:
    """`documents` is the entity whose `list` had neither a search branch nor an
    ordering, so its whitelist is the one most likely to have been wired to the wrong
    model's sorts."""
    customer = Customer(ragione_sociale="Cliente", nazione="IT", custom_fields={})
    db_session.add(customer)
    db_session.flush()
    for titolo in ("Gamma", "Alfa", "Beta"):
        db_session.add(
            Document(
                customer_id=customer.id,
                tipo="documento",
                titolo=titolo,
                versione_corrente=1,
                custom_fields={},
            )
        )
    db_session.flush()

    repo = DocumentRepository(db_session)
    ordered = repo.list(DocumentListQuery(sort="titolo", dir="asc", limit=10))
    assert [d.titolo for d in ordered] == ["Alfa", "Beta", "Gamma"]

    with pytest.raises(ValidationFailed) as caught:
        repo.list(DocumentListQuery(sort="ragione_sociale", limit=10))
    assert caught.value.details["field"] == "sort"
