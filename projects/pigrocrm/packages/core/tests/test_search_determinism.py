"""**Criterion 4.** The same database and the same term produce a byte-identical response
on twenty runs, and the order that makes that true is total.

The third sort key (`id DESC`) exists for this and only this: `punteggio DESC,
updated_at DESC` is not a total order, and on this schema it is not even close to one.
`TimestampMixin` gives `updated_at` a `server_default` of `func.now()`, which Postgres
resolves to `transaction_timestamp()` -- constant for a whole transaction -- so *every* row
a corpus builder inserts shares the value to the microsecond. Add the fact that the §8.5
score is a four-place decimal drawn from a ladder of four rungs and the ties are not an edge
case, they are the normal state of the data. Without a total order Postgres is free to
return the same set in a different sequence each time, and a palette whose rows reorder
between identical keystrokes cannot be driven with the arrow keys, which is the only way a
palette is driven.

**Twenty identical runs prove almost nothing on their own, so this file does not rest on
them.** Two executions of one statement, on one connection, over data nobody touched in
between, will produce the same row order whether or not the order is total -- the planner
picks the same plan and the heap has not moved. Observing that is observing the absence of
a reason to differ, not the presence of a guarantee. So the property is established three
ways instead:

1. *By construction.* The last ordering key is asserted to be the table's primary key, on
   every branch. A primary key is unique, so no two rows can tie on every key, so the order
   is total -- that is an argument, not a sample.
2. *By exhibiting the ties.* The first two keys are read off the same predicate and shown
   to collide, in bulk. If they did not, the third key would be decoration and every
   assertion about it would be unfalsifiable.
3. *By taking the reason to agree away.* Between every pair of runs the heap is reshuffled:
   forty rows per searched table are rewritten with their own values, which changes nothing
   a search reads and moves every one of them to the end of the table. The runs also rotate
   through four access paths. Measured, with `id DESC` deleted from `_scored`: the four
   access paths alone still gave twenty identical responses -- on a table this size Postgres
   reaches the rows in storage order however it is told to find them -- and the reshuffle
   parted them at once. So the reshuffle is the falsifier and the access-path rotation is
   insurance; recording which is which is the point of saying so here.

`model_dump_json().encode()` and not `model_dump()`: the comparison is on the bytes the
client receives. A `Decimal` and a `float` that compare equal in Python serialise
differently, and a dict that iterates in a different order compares equal as a mapping.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

import pytest
from corpus import KNOWN_PARTITA_IVA, KNOWN_RAGIONE_SOCIALE, SMALL, build_corpus
from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.people.models import Person
from pigrocrm.core.search.repository import (
    CUSTOMER_FIELDS,
    DEAL_FIELDS,
    DOCUMENT_FIELDS,
    INVOICE_FIELDS,
    PERSON_FIELDS,
    SearchRepository,
)
from pigrocrm.core.search.schemas import SearchQuery
from pigrocrm.core.search.scoring import row_score
from pigrocrm.core.search.service import SearchService

READONLY = Actor(id=uuid7(), type="user", role="readonly")
# Five, not the twenty criterion 4 names. The runs are the sample, not the guarantee: the
# module docstring records that twenty identical runs prove almost nothing on their own,
# that the guarantee is the argument by construction below (the last ordering key is the
# primary key), and that the heap reshuffle is the one disturbance measured to part the
# runs once the tie-break was removed. Five reshuffled runs falsify what twenty did, the
# runs at a quarter of their cost (REB-590); the corpus build the test pays either way.
_RUNS = 5

# The four searched branches, as `(table, model, fields)`. Every assertion about the
# ordering is made on all four: `deals` and `documents` search a single column each, and a
# third sort key silently dropped from one of the two would otherwise never be noticed.
# The trigram branches, which is every branch's scored query. `invoices` is here for that
# half of it; its *equality* half orders by `(anno DESC, id DESC)` and never reaches
# `_scored` at all, so its totality is argued where it lives -- `test_search_invoices.py`'s
# `test_the_order_is_total_so_two_identical_searches_agree`, which runs both paths.
_BRANCHES = (
    ("customers", Customer, CUSTOMER_FIELDS),
    ("people", Person, PERSON_FIELDS),
    ("deals", Deal, DEAL_FIELDS),
    ("documents", Document, DOCUMENT_FIELDS),
    ("invoices", Invoice, INVOICE_FIELDS),
)

# Four planner configurations, rotated across the `_RUNS` runs. Not a trick: a term that
# becomes more selective, an autovacuum that refreshes a GIN index's metapage statistics
# (task A9), or simply a bigger table all make Postgres change access path in production,
# and the natural order of a bitmap heap scan, a sequential scan and a plain index scan are
# three different orders. Taking that variation away is what would make this test a
# tautology.
_ACCESS_PATHS = (
    ("planner free", "on", "on", "on"),
    ("no seq scan", "off", "on", "on"),
    ("no index or bitmap scan", "on", "off", "off"),
    ("no bitmap scan", "off", "on", "off"),
)

_FIXED_GROUP_ORDER = ["customer", "person", "deal", "document", "invoice"]


def _set_access_path(session: Session, seqscan: str, indexscan: str, bitmapscan: str) -> None:
    """`SET LOCAL`, so the setting dies with the transaction the `db_session` fixture rolls
    back and cannot leak into another test through a pooled connection."""
    session.execute(text(f"SET LOCAL enable_seqscan = {seqscan}"))
    session.execute(text(f"SET LOCAL enable_indexscan = {indexscan}"))
    session.execute(text(f"SET LOCAL enable_bitmapscan = {bitmapscan}"))


def _reshuffle(session: Session) -> int:
    """Move the head of each searched table to the end of its heap, answer unchanged.

    Each row's own value is written back over itself, so nothing a search reads changes:
    not the searched text, not the score, and -- because this is raw SQL rather than an ORM
    flush -- not `updated_at`, whose `onupdate` is a Python-side default that only an ORM
    `update()` construct fires. What *does* change is where the rows live: an `UPDATE` in
    Postgres writes a new tuple version, so the forty rows a sequential scan would have
    returned first are now the forty it returns last.

    `ORDER BY ctid` rather than a `WHERE` on the term: it selects rows by storage position,
    which is exactly the ordering being taken away, and it keeps selecting different rows
    every round as the ones it moved go to the back.
    """
    moved = 0
    for table, column in (
        ("customers", "nazione"),
        ("people", "nome"),
        ("deals", "nome"),
        ("documents", "titolo"),
    ):
        moved += session.execute(
            text(
                f"UPDATE {table} SET {column} = {column} WHERE id IN ("
                f"  SELECT id FROM {table} WHERE deleted_at IS NULL ORDER BY ctid LIMIT 40)"
            )
        ).rowcount
    return moved


def _renderings(session: Session, term: str, limite: int = 5) -> Counter[bytes]:
    """`_RUNS` responses, as bytes, under a rotating access path and a moving heap.

    Both disturbances are here because one of them is not enough, and that is a measurement
    rather than a precaution: with `id DESC` removed from `_scored`, the four access paths
    alone still produced twenty identical responses -- the planner reaches the same rows in
    the same physical order however it is told to find them, on a table this size. The heap
    reshuffle is what parts them. The access-path rotation stays because it costs nothing and
    covers the case where a future plan (a merge join, a sort node the planner elides)
    changes the order for a different reason.
    """
    service = SearchService(session)
    query = SearchQuery(termine=term, limite=limite)
    seen: Counter[bytes] = Counter()
    for run in range(_RUNS):
        _, seqscan, indexscan, bitmapscan = _ACCESS_PATHS[run % len(_ACCESS_PATHS)]
        _set_access_path(session, seqscan, indexscan, bitmapscan)
        seen[service.search_everything(query, READONLY).model_dump_json().encode()] += 1
        assert _reshuffle(session) > 0, "nothing moved, so the runs were never disturbed"
    _set_access_path(session, "on", "on", "on")
    return seen


def _assert_one_rendering(seen: Counter[bytes], term: str) -> None:
    assert len(seen) == 1, (
        f"{len(seen)} distinct responses across {_RUNS} runs of {term!r} -- the order is "
        "not total. Check that every branch orders by punteggio DESC, updated_at DESC, "
        "id DESC.\n"
        + "\n".join(
            f"  seen {count}x: {rendering.decode()[:400]}" for rendering, count in seen.items()
        )
    )


def _assert_the_response_is_worth_comparing(rendering: bytes) -> None:
    """Twenty identical *empty* responses would satisfy every assertion in this file.

    The determinism claim is about something only if there is something to be deterministic
    about, so every comparison here first reads the single rendering back and checks that it
    carries hits at all -- ordered hits being the only part of the payload a lost sort key
    could move.
    """
    payload = json.loads(rendering)
    groups = payload["gruppi"]
    assert [group["entity"] for group in groups] == _FIXED_GROUP_ORDER, payload
    hits = [hit for group in groups for hit in group["hits"]]
    assert len(hits) > 1, (
        "the compared responses carry fewer than two hits between them, so nothing in them "
        f"could have been ordered differently:\n{rendering.decode()}"
    )
    assert all(hit["etichetta"] and hit["punteggio"] for hit in hits), rendering.decode()


def test_repeated_runs_under_four_access_paths_produce_one_byte_identical_response(
    db_session: Session,
) -> None:
    build_corpus(db_session, SMALL)

    seen = _renderings(db_session, "Ingegneria")

    _assert_one_rendering(seen, "Ingegneria")
    rendering = next(iter(seen))
    _assert_the_response_is_worth_comparing(rendering)
    assert sum(seen.values()) == _RUNS


def test_the_last_ordering_key_is_the_primary_key_so_no_two_rows_can_tie(
    db_session: Session,
) -> None:
    """Totality argued rather than sampled.

    Twenty matching runs say the rows did not reorder; they cannot say the rows *could not*
    reorder. This can: the final `ORDER BY` term of every branch's statement is that table's
    primary key, a primary key is unique by definition, so no two rows tie on the whole key
    and the sequence Postgres may return is exactly one.

    Read off the compiled statement rather than off SQLAlchemy's private `_order_by_clauses`,
    because what settles the question is the SQL the server is asked to run.
    """
    repo = SearchRepository(db_session)
    for table, model, fields in _BRANCHES:
        primary_key = list(model.__table__.primary_key.columns)
        assert [column.name for column in primary_key] == ["id"], (
            f"{table} does not have `id` alone as its primary key, so ordering by it last "
            "does not make the order total"
        )

        compiled = str(
            repo._scored(model, fields, "Ingegneria", 5).compile(
                db_session.get_bind(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "ORDER BY" in compiled, f"{table} is not ordered at all:\n{compiled}"
        order_by = compiled.split("ORDER BY", 1)[1]
        # The score expression carries commas of its own, so the keys are matched by name
        # and by what follows them rather than split on `,`.
        assert re.search(rf"\b{table}\.id DESC\s*(\n\s*)?(LIMIT|$)", order_by), (
            f"{table}'s last ordering key is not its primary key:\n{order_by}"
        )
        assert f"{table}.updated_at DESC" in order_by, (
            f"{table} lost §8.5's second ordering key:\n{order_by}"
        )


def test_the_first_two_ordering_keys_really_do_tie(db_session: Session) -> None:
    """The other half of the argument above: the third key is load-bearing, not decoration.

    If `punteggio, updated_at` were already unique, `id DESC` would never break anything and
    every assertion about it would pass no matter what it said. They are not: the score is a
    four-place decimal off a four-rung ladder, and `updated_at` defaults to
    `transaction_timestamp()`, which is one value for the whole build. Both halves are
    asserted here -- collisions exist, and adding `id` removes them all.
    """
    build_corpus(db_session, SMALL)
    repo = SearchRepository(db_session)

    keys = db_session.execute(
        select(row_score(CUSTOMER_FIELDS, "Ingegneria"), Customer.updated_at, Customer.id).where(
            *repo._predicate(Customer, CUSTOMER_FIELDS, "Ingegneria")
        )
    ).all()

    assert len(keys) > 10, (
        f"only {len(keys)} customers matched, which is not enough rows for a tie to be meaningful"
    )
    first_two = Counter((row[0], row[1]) for row in keys)
    collisions = {pair: count for pair, count in first_two.items() if count > 1}
    assert collisions, (
        "no two matching rows shared a score and an updated_at, so this corpus cannot show "
        f"whether the third sort key does anything: {first_two}"
    )
    assert len({(row[0], row[1], row[2]) for row in keys}) == len(keys), (
        "two rows tied on score, updated_at *and* id -- the order is not total even with "
        "the third key"
    )


def test_the_property_holds_for_a_term_with_nothing_but_ties(db_session: Session) -> None:
    """The adversarial version. Two hundred rows with the *same* name, inserted in one
    transaction so they share `updated_at` to the microsecond: score and second key are both
    ties for every pair, and only `id DESC` can order them.

    `partita_iva`, `codice_fiscale` and `email` are left NULL on purpose. `field_score`
    scores a NULL column zero and `GREATEST` then hides it behind the name's score, so all
    two hundred rows carry exactly one non-zero field score and it is the same number.
    """
    db_session.execute(
        insert(Customer),
        [
            {
                "id": uuid7(),
                "ragione_sociale": "Identica Srl",
                "nazione": "IT",
                "custom_fields": {},
            }
            for _ in range(200)
        ],
    )
    db_session.flush()

    seen = _renderings(db_session, "Identica")

    _assert_one_rendering(seen, "Identica")
    rendering = next(iter(seen))
    _assert_the_response_is_worth_comparing(rendering)
    # And the group really did fill up, so the five that were compared were five chosen out
    # of two hundred rather than the only five there were.
    results = SearchService(db_session).search_everything(
        SearchQuery(termine="Identica", limite=5), READONLY
    )
    customers = next(group for group in results.gruppi if group.entity == "customer")
    assert len(customers.hits) == 5
    assert customers.totale == 200


def test_the_property_survives_the_rows_being_physically_reordered(db_session: Session) -> None:
    """The strongest falsifier in the file, and the one that does not depend on the planner.

    Rewriting half the matching rows moves their heap tuples to the end of the table, so the
    order a sequential scan hands back is no longer the order it handed back a moment ago.
    Nothing about the *answer* changes: the `UPDATE` writes each row's own `nazione` back
    over itself, and it is issued as raw SQL, so SQLAlchemy's `onupdate` for `updated_at`
    does not fire and every ordering key is untouched. If the sequence were coming from the
    heap rather than from `ORDER BY`, it would move here.
    """
    db_session.execute(
        insert(Customer),
        [
            {
                "id": uuid7(),
                "ragione_sociale": f"Riordinabile Srl {index:03d}",
                "nazione": "IT",
                "custom_fields": {},
            }
            for index in range(200)
        ],
    )
    db_session.flush()

    service = SearchService(db_session)
    query = SearchQuery(termine="Riordinabile", limite=5)
    before = service.search_everything(query, READONLY).model_dump_json().encode()

    moved = db_session.execute(
        text(
            "UPDATE customers SET nazione = nazione "
            "WHERE ragione_sociale LIKE 'Riordinabile Srl %%' "
            "AND right(ragione_sociale, 1) IN ('0', '2', '4', '6', '8')"
        )
    ).rowcount
    assert moved == 100, f"the reshuffle moved {moved} rows, not the half it meant to"

    after = service.search_everything(query, READONLY).model_dump_json().encode()
    assert before == after, (
        "the response changed when the rows moved in the heap, so the sequence was coming "
        f"from the storage order and not from ORDER BY:\n{before.decode()}\n"
        f"{after.decode()}"
    )
    # Non-vacuity: five hits chosen out of two hundred, so there was a choice to get wrong.
    customers = next(
        group
        for group in service.search_everything(query, READONLY).gruppi
        if group.entity == "customer"
    )
    assert (len(customers.hits), customers.totale) == (5, 200)


def test_an_exact_vat_number_puts_that_customer_first(db_session: Session) -> None:
    """§16 criterion 4's first named expectation.

    A decoy is planted whose *name* contains the same eleven digits, so the group has more
    than one hit and "first" is a claim about ranking rather than about there being only one
    row. Without it the assertion would pass against any ordering whatsoever.
    """
    build_corpus(db_session, SMALL)
    db_session.add(
        Customer(
            ragione_sociale=f"Studio {KNOWN_PARTITA_IVA} Srl",
            nazione="IT",
            custom_fields={},
        )
    )
    db_session.flush()

    results = SearchService(db_session).search_everything(
        SearchQuery(termine=KNOWN_PARTITA_IVA), READONLY
    )
    customers = next(group for group in results.gruppi if group.entity == "customer")

    labels = [hit.etichetta for hit in customers.hits]
    assert len(labels) >= 2, f"only {labels} matched, so 'first' is not a ranking claim"
    assert labels[0] == KNOWN_RAGIONE_SOCIALE, (
        f"the exact P.IVA did not come first: {labels}, scores "
        f"{[str(hit.punteggio) for hit in customers.hits]}"
    )
    assert customers.hits[0].campo == "partita_iva"


def test_a_prefix_outranks_a_mid_word_match(db_session: Session) -> None:
    """§16 criterion 4's second named expectation, at the service level rather than the
    expression level (task A7 covers the expression)."""
    db_session.add(Customer(ragione_sociale="Vulcano Impianti Srl", nazione="IT", custom_fields={}))
    db_session.add(
        Customer(ragione_sociale="Grande Vulcanologia Spa", nazione="IT", custom_fields={})
    )
    db_session.flush()

    results = SearchService(db_session).search_everything(
        SearchQuery(termine="Vulcan", limite=5), READONLY
    )
    labels = [
        hit.etichetta
        for hit in next(group for group in results.gruppi if group.entity == "customer").hits
    ]

    # Both have to be shown before their order can mean anything: a floor that discarded the
    # mid-word match would otherwise turn this into `labels.index` raising ValueError, which
    # is a different failure than the one being tested for.
    assert set(labels) == {"Vulcano Impianti Srl", "Grande Vulcanologia Spa"}, labels
    assert labels.index("Vulcano Impianti Srl") < labels.index("Grande Vulcanologia Spa")


def test_the_group_order_is_fixed_and_not_by_count(db_session: Session) -> None:
    """A palette whose sections move between keystrokes cannot be used with the keyboard.

    The term is chosen so that ordering by count would give a *different* sequence; that
    inequality is asserted too, because on a term where the two coincide this test would
    pass against a service that sorted its groups by size.
    """
    build_corpus(db_session, SMALL)
    results = SearchService(db_session).search_everything(
        SearchQuery(termine="Ingegneria"), READONLY
    )

    assert [group.entity for group in results.gruppi] == _FIXED_GROUP_ORDER
    by_count = [group.entity for group in sorted(results.gruppi, key=lambda group: -group.totale)]
    assert by_count != _FIXED_GROUP_ORDER, (
        "on this term the fixed order and the by-count order coincide, so the assertion "
        f"above cannot tell them apart: {[(g.entity, g.totale) for g in results.gruppi]}"
    )


@pytest.mark.parametrize("branch", _BRANCHES, ids=[branch[0] for branch in _BRANCHES])
def test_every_branch_is_ordered_by_the_score_before_anything_else(
    db_session: Session, branch: tuple[str, Any, Any]
) -> None:
    """All three keys, in order, descending, on all four branches.

    A branch that ordered by `updated_at DESC, id DESC` alone would be perfectly
    deterministic and completely wrong, and every other test in this file would pass. So
    would one that ordered by the score ascending, which is the same mistake spelled with
    one word.

    The three keys are located by position rather than by splitting on one of them: a
    `split` on a separator that is not there returns the whole string, and an assertion
    about "what comes before the second key" then quietly becomes an assertion about the
    whole clause.
    """
    table, model, fields = branch
    compiled = str(
        SearchRepository(db_session)
        ._scored(model, fields, "Ingegneria", 5)
        .compile(db_session.get_bind(), compile_kwargs={"literal_binds": True})
    )
    order_by = compiled.split("ORDER BY", 1)[1]

    at_score = order_by.find("word_similarity")
    at_updated = order_by.find(f"{table}.updated_at DESC")
    at_id = order_by.find(f"{table}.id DESC")
    assert -1 not in (at_score, at_updated, at_id), (
        f"{table} is missing one of §8.5's three ordering keys (score {at_score}, "
        f"updated_at {at_updated}, id {at_id}):\n{order_by}"
    )
    assert at_score < at_updated < at_id, (
        f"{table}'s ordering keys are not score, then updated_at, then id:\n{order_by}"
    )
    assert order_by[:at_updated].rstrip().rstrip(",").endswith("DESC"), (
        f"{table} does not order by the score *descending*, so the best match is last:\n{order_by}"
    )
