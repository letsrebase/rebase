"""**Criterion 13.** Ordered pagination loses no row and repeats none.

Two halves, and neither one alone is the criterion.

**The walk.** Every whitelisted sort key of all four listed entities, in both directions,
paged one row at a time and two rows at a time, and the concatenation of the pages
asserted to equal -- element for element, not as a set -- both a single unpaged query and
an order computed here in Python from the seeded values. The union alone is not enough:
a predicate that returns every row in the wrong order still has the right union. The
unpaged query alone is not enough either, because a wrong `order_by` is wrong identically
in both, so the Python-side expectation is what pins the order down independently.

The corpus is built so that the walk can fail. Its sort values **collide** -- three rows
share a surname, three share a timestamp -- because a keyset over a non-unique column is
exactly what the composite cursor exists for, and a fixture whose values are all distinct
cannot catch a tie-breaking bug: a bare-id cursor pages it correctly by accident. They are
also deliberately *not* in insertion order, so an implementation that quietly ordered by
`id` alone would be visible. And `people.last_name` is null on four of the nine rows, so a
one-row page has to cross the null boundary in both directions -- the boundary where
`NULLS FIRST`/`NULLS LAST` and the descending expression disagree, and where task A3's
`desc(nullslast(...))`/`nullslast(desc(...))` defect lived.

**The race.** The walk above runs inside one transaction, where nothing moves. The
property the criterion actually claims is about a scan that overlaps a writer, so the
second half opens two real sessions on `db_engine`, commits inserts from one while the
other pages, and asserts what offset pagination gets wrong by construction: inserting a
row that sorts before the current page shifts every later row down by one, so page two
re-reads the last row of page one and page three skips one entirely.

**Why it needs its own sessions.** `db_session` hands out one savepoint-backed session on
one connection, so a "concurrent" insert made through it is not concurrent at all -- it is
the same transaction. Slice 3's plan records the same limitation for its numbering race.
This test opens two sessions from `db_engine`, commits from one while the other pages, and
cleans up in a `finally`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.schemas import CustomerListQuery
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db import SortDirection, session_factory
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import DocumentListQuery
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.people.models import Person
from pigrocrm.core.people.schemas import PersonListQuery
from pigrocrm.core.people.service import PersonService
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")

# --- the corpus ---------------------------------------------------------------------
#
# Nine rows. Every column below is a tie generator, and none of the three is monotonic in
# insertion order -- that is the whole design. `Alfa`/`Bianchi`/`Rossi`/`Zeta` are single
# ASCII words of one case on purpose: they sort identically under the C collation and
# under any ICU one, so the Python-side expectation below is not quietly asserting the
# container's `lc_collate` instead of this project's ordering contract.

_TEXT_VALUES: tuple[str, ...] = (
    "Rossi",
    "Bianchi",
    "Rossi",
    "Alfa",
    "Bianchi",
    "Rossi",
    "Zeta",
    "Alfa",
    "Bianchi",
)

# The same shape with a null tail, for the one nullable sort column in the whitelist.
# Four nulls and two of them adjacent: a page of one has to enter the tail, walk inside
# it and leave it, in both directions.
_NULLABLE_VALUES: tuple[str | None, ...] = (
    "Rossi",
    None,
    "Bianchi",
    None,
    "Rossi",
    None,
    "Zeta",
    "Bianchi",
    None,
)

# Days back from a fixed instant, so `created_at` and `updated_at` order the corpus
# differently from each other *and* differently from `id`. Set explicitly rather than
# left to `server_default=func.now()`, which is the transaction's start time and would
# make all nine rows one single tie -- a corpus that could not tell an ordering by
# `created_at` from an ordering by `updated_at`.
_CREATED_DAYS: tuple[int, ...] = (0, 3, 0, 7, 3, 0, 1, 7, 3)
_UPDATED_DAYS: tuple[int, ...] = (2, 2, 5, 0, 9, 5, 0, 2, 9)

# Derived from the Italian clock, never `datetime.now()`: no literal year appears in a
# test in this repository. The time of day is arbitrary; only the differences matter.
_ANCHOR = datetime.combine(today_local(), time(12, 0), tzinfo=UTC)

_ROWS = len(_TEXT_VALUES)

# `(id, {sort key: sort value})` for one seeded row.
Seeded = tuple[UUID, dict[str, object | None]]
Pager = Callable[
    [Session, LocalFileStorage, str, SortDirection, int, str | None],
    tuple[list[UUID], str | None],
]


def _stamps(index: int) -> tuple[datetime, datetime]:
    return (
        _ANCHOR - timedelta(days=_CREATED_DAYS[index]),
        _ANCHOR - timedelta(days=_UPDATED_DAYS[index]),
    )


def _seed_customers(session: Session, storage: LocalFileStorage) -> list[Seeded]:
    seeded: list[Seeded] = []
    for index, value in enumerate(_TEXT_VALUES):
        created, updated = _stamps(index)
        row = Customer(
            legal_name=value,
            country="IT",
            custom_fields={},
            created_at=created,
            updated_at=updated,
        )
        session.add(row)
        session.flush()
        seeded.append((row.id, {"legal_name": value, "created_at": created, "updated_at": updated}))
    return seeded


def _seed_people(session: Session, storage: LocalFileStorage) -> list[Seeded]:
    seeded: list[Seeded] = []
    for index, last_name in enumerate(_NULLABLE_VALUES):
        created, updated = _stamps(index)
        row = Person(
            first_name="Marco",
            last_name=last_name,
            custom_fields={},
            created_at=created,
            updated_at=updated,
        )
        session.add(row)
        session.flush()
        seeded.append(
            (row.id, {"last_name": last_name, "created_at": created, "updated_at": updated})
        )
    return seeded


def _seed_deals(session: Session, storage: LocalFileStorage) -> list[Seeded]:
    customer = Customer(legal_name="Committente Srl", country="IT", custom_fields={})
    stage = PipelineStage(name=f"Aperto {uuid4()}", position=0, default_probability=10, type="open")
    session.add_all([customer, stage])
    session.flush()

    seeded: list[Seeded] = []
    for index, value in enumerate(_TEXT_VALUES):
        created, updated = _stamps(index)
        row = Deal(
            name=value,
            customer_id=customer.id,
            pipeline_stage_id=stage.id,
            probability=10,
            created_at=created,
            updated_at=updated,
        )
        session.add(row)
        session.flush()
        seeded.append((row.id, {"name": value, "created_at": created, "updated_at": updated}))
    return seeded


def _seed_documents(session: Session, storage: LocalFileStorage) -> list[Seeded]:
    customer = Customer(legal_name="Committente Srl", country="IT", custom_fields={})
    session.add(customer)
    session.flush()

    seeded: list[Seeded] = []
    for index, title in enumerate(_TEXT_VALUES):
        created, updated = _stamps(index)
        row = Document(
            customer_id=customer.id,
            type="document",
            title=title,
            current_version=0,
            custom_fields={},
            created_at=created,
            updated_at=updated,
        )
        session.add(row)
        session.flush()
        seeded.append((row.id, {"title": title, "created_at": created, "updated_at": updated}))
    return seeded


def _page_customers(
    session: Session,
    storage: LocalFileStorage,
    sort: str,
    direction: SortDirection,
    limit: int,
    cursor: str | None,
) -> tuple[list[UUID], str | None]:
    page = CustomerService(session).list(
        CustomerListQuery(sort=sort, dir=direction, limit=limit, cursor=cursor), ADMIN
    )
    return [item.id for item in page.items], page.next_cursor


def _page_people(
    session: Session,
    storage: LocalFileStorage,
    sort: str,
    direction: SortDirection,
    limit: int,
    cursor: str | None,
) -> tuple[list[UUID], str | None]:
    page = PersonService(session).list(
        PersonListQuery(sort=sort, dir=direction, limit=limit, cursor=cursor), ADMIN
    )
    return [item.id for item in page.items], page.next_cursor


def _page_deals(
    session: Session,
    storage: LocalFileStorage,
    sort: str,
    direction: SortDirection,
    limit: int,
    cursor: str | None,
) -> tuple[list[UUID], str | None]:
    page = DealService(session).list(
        DealListQuery(sort=sort, dir=direction, limit=limit, cursor=cursor), ADMIN
    )
    return [item.id for item in page.items], page.next_cursor


def _page_documents(
    session: Session,
    storage: LocalFileStorage,
    sort: str,
    direction: SortDirection,
    limit: int,
    cursor: str | None,
) -> tuple[list[UUID], str | None]:
    page = DocumentService(session, storage).list(
        DocumentListQuery(sort=sort, dir=direction, limit=limit, cursor=cursor), ADMIN
    )
    return [item.id for item in page.items], page.next_cursor


# Every entity that has a sort whitelist, with every key that whitelist admits. Kept as
# literals rather than read off `CUSTOMER_SORTS.keys()` and friends: a key silently
# dropped from a whitelist would then silently drop its own test too.
_ENTITIES: dict[
    str, tuple[Callable[[Session, LocalFileStorage], list[Seeded]], Pager, tuple[str, ...]]
] = {
    "customers": (
        _seed_customers,
        _page_customers,
        ("created_at", "updated_at", "legal_name"),
    ),
    "people": (_seed_people, _page_people, ("created_at", "updated_at", "last_name")),
    "deals": (_seed_deals, _page_deals, ("created_at", "updated_at", "name")),
    "documents": (_seed_documents, _page_documents, ("created_at", "updated_at", "title")),
}

_CASES: tuple[tuple[str, str, SortDirection], ...] = tuple(
    (entity, sort, direction)
    for entity, (_, _, sorts) in _ENTITIES.items()
    for sort in sorts
    for direction in ("asc", "desc")
)


def _expected_order(seeded: list[Seeded], sort: str, direction: SortDirection) -> list[UUID]:
    """`col <dir> NULLS LAST, id <dir>`, computed here rather than asked of the database.

    Nulls last in *both* directions, and the tie-break travelling in the same direction as
    the column: that is `db/sort.py`'s contract, and reproducing it independently is what
    makes the assertion a check on the implementation instead of a mirror of it.
    """
    present: list[tuple[Any, UUID]] = [
        (row[1][sort], row[0]) for row in seeded if row[1][sort] is not None
    ]
    absent = [row[0] for row in seeded if row[1][sort] is None]
    reverse = direction == "desc"
    # Reversing the *pair* is what puts the tie-break in the direction of travel: at
    # `desc` equal values come back id-descending, not id-ascending.
    ordered = sorted(present, key=lambda pair: (pair[0], pair[1]), reverse=reverse)
    return [pair[1] for pair in ordered] + sorted(absent, reverse=reverse)


def _walk(
    session: Session,
    storage: LocalFileStorage,
    entity: str,
    sort: str,
    direction: SortDirection,
    page_size: int,
) -> list[UUID]:
    """Every page, concatenated, with a hard bound on the number of pages.

    The bound is not defensive tidiness: a keyset predicate written `>=` instead of `>`
    returns the cursor row again on every page, and at `page_size=1` that never advances
    at all. Without the bound the suite hangs instead of failing.
    """
    _, pager, _ = _ENTITIES[entity]
    walked: list[UUID] = []
    cursor: str | None = None
    for _ in range(_ROWS + 5):
        ids, cursor = pager(session, storage, sort, direction, page_size, cursor)
        walked.extend(ids)
        if cursor is None:
            return walked
    pytest.fail(
        f"{entity}/{sort}/{direction} did not terminate at page size {page_size}: the "
        "keyset predicate is probably inclusive (>=) where it must be exclusive (>)"
    )


@pytest.mark.parametrize(("entity", "sort", "direction"), _CASES)
@pytest.mark.parametrize("page_size", [1, 2])
def test_a_paged_walk_is_the_unpaged_order_exactly(
    db_session: Session,
    local_storage: LocalFileStorage,
    entity: str,
    sort: str,
    direction: SortDirection,
    page_size: int,
) -> None:
    seed, pager, _ = _ENTITIES[entity]
    seeded = seed(db_session, local_storage)

    unpaged, unpaged_cursor = pager(db_session, local_storage, sort, direction, 200, None)
    expected = _expected_order(seeded, sort, direction)

    # The corpus is the whole table for this entity inside this transaction, so an
    # unpaged read has to return all of it and nothing else. Asserted first: every
    # comparison below would pass vacuously against an empty result.
    assert unpaged_cursor is None
    assert len(unpaged) == _ROWS
    assert set(unpaged) == {row[0] for row in seeded}
    assert unpaged == expected

    walked = _walk(db_session, local_storage, entity, sort, direction, page_size)

    assert len(walked) == len(set(walked)), (
        f"{len(walked) - len(set(walked))} row(s) were returned more than once"
    )
    assert walked == expected


def test_the_corpus_could_actually_catch_a_tie_breaking_bug() -> None:
    """The fixture's own guard.

    A walk over rows whose sort values are all distinct proves nothing about the composite
    cursor: a bare-id cursor pages it correctly by accident, and so does an `ORDER BY` with
    no tie-break at all. Every column above has to keep colliding, and the null tail has to
    keep existing, or the tests that use them quietly stop testing anything.
    """
    assert len(set(_TEXT_VALUES)) < _ROWS
    assert len(set(_NULLABLE_VALUES)) < _ROWS
    assert _NULLABLE_VALUES.count(None) >= 2
    assert None in _NULLABLE_VALUES and any(v is not None for v in _NULLABLE_VALUES)
    assert len(set(_CREATED_DAYS)) < _ROWS
    assert len(set(_UPDATED_DAYS)) < _ROWS
    # ...and none of them may follow insertion order, or an implementation ordering by
    # `id` alone would pass every case above.
    assert list(_TEXT_VALUES) != sorted(_TEXT_VALUES)
    assert list(_CREATED_DAYS) != sorted(_CREATED_DAYS, reverse=True)
    assert list(_UPDATED_DAYS) != sorted(_UPDATED_DAYS, reverse=True)
    assert _CREATED_DAYS != _UPDATED_DAYS


# --- the race -----------------------------------------------------------------------

_PAGE = 25
_INITIAL = 300


@pytest.fixture
def committed_customers(db_engine: Engine) -> Iterator[set[UUID]]:
    """Real committed rows on their own connections, removed afterwards.

    Not the `db_session` fixture: its outer transaction makes two sessions on one
    connection, and nothing committed from one would be a concurrent write to the other.
    """
    factory = session_factory(db_engine)
    created: set[UUID] = set()
    with factory() as session:
        for index in range(_INITIAL):
            row = Customer(legal_name=f"KEYSET {index:05d} Srl", country="IT", custom_fields={})
            session.add(row)
            session.flush()
            created.add(row.id)
        session.commit()
    try:
        yield created
    finally:
        with factory() as session:
            session.execute(delete(Customer).where(Customer.legal_name.like("KEYSET %")))
            session.commit()


def _all_keyset_ids(session: Session) -> set[UUID]:
    return set(
        session.scalars(select(Customer.id).where(Customer.legal_name.like("KEYSET %"))).all()
    )


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_paging_under_concurrent_inserts_loses_nothing_and_repeats_nothing(
    db_engine: Engine, committed_customers: set[UUID], direction: SortDirection
) -> None:
    """Both directions, because `desc` is a genuinely different arm of `keyset_predicate`
    and a backward index scan in the planner.

    The assertions are deliberately weaker than "the union equals the final table": a row
    inserted after the scan passed its position legitimately may or may not appear. What
    must hold is exactly what the criterion states -- no duplicates, and every row present
    both before and after the scan is in the union.
    """
    factory = session_factory(db_engine)
    reader = factory()
    writer = factory()
    try:
        present_at_start = _all_keyset_ids(reader)
        assert len(present_at_start) == _INITIAL

        service = CustomerService(reader)
        seen: list[UUID] = []
        cursor: str | None = None
        inserted = 0
        while True:
            page = service.list(
                CustomerListQuery(
                    search="KEYSET",
                    sort="legal_name",
                    dir=direction,
                    limit=_PAGE,
                    cursor=cursor,
                ),
                ADMIN,
            )
            seen.extend(item.id for item in page.items)

            # A concurrent insert between every pair of pages, half of them sorting
            # *before* the page just read -- the case offset pagination gets wrong.
            if inserted < 8:
                prefix = "KEYSET 00000" if inserted % 2 == 0 else "KEYSET 99999"
                writer.add(
                    Customer(
                        legal_name=f"{prefix} intruso {direction} {inserted} Srl",
                        country="IT",
                        custom_fields={},
                    )
                )
                writer.commit()
                inserted += 1

            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert inserted == 8, "the writer never got between two pages: the scan was one page"
        reader.rollback()  # start a fresh snapshot before the closing read
        present_at_end = _all_keyset_ids(reader)

        assert len(seen) == len(set(seen)), (
            f"{len(seen) - len(set(seen))} row(s) were returned more than once"
        )
        stable = present_at_start & present_at_end
        missing = stable - set(seen)
        assert not missing, f"{len(missing)} row(s) present throughout were never returned"
    finally:
        reader.close()
        writer.close()


def test_the_scan_terminates_rather_than_looping(
    db_engine: Engine, committed_customers: set[UUID]
) -> None:
    """A keyset predicate that is `>=` instead of `>` returns the cursor row again on
    every page; at a page size of one it never advances at all and the loops above never
    end. Bounding the page count turns that into a named failure rather than a hung suite.

    A page size of one is the point: at 25 an inclusive predicate still limps forward 24
    rows at a time -- it repeats a row per page but it does terminate, so a bound checked
    at that page size would pass against the very bug it is here to catch.
    """
    ten_of_them = "KEYSET 0000"  # matches KEYSET 00000..00009 and nothing else
    with session_factory(db_engine)() as reader:
        service = CustomerService(reader)
        cursor: str | None = None
        for _ in range(15):
            page = service.list(
                CustomerListQuery(search=ten_of_them, sort="legal_name", limit=1, cursor=cursor),
                ADMIN,
            )
            if page.next_cursor is None:
                assert len(page.items) == 1
                return
            cursor = page.next_cursor
    pytest.fail(
        "pagination did not terminate: the keyset predicate is probably inclusive (>=) "
        "where it must be exclusive (>)"
    )
