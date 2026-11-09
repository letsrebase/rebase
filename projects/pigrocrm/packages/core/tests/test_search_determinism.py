"""**Criterion 4.** The same database and the same term produce a byte-identical response
on twenty runs, and the order that makes that true is total.

The third sort key (`id DESC`) exists for this and only this: `score DESC,
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

1. *By ordering planted ties.* Every branch is given rows on three rungs of the ladder and
   dates spread over a few days, so that scores tie, `updated_at` ties within them, and
   only the third key can order what is left; the rows the repository returns must then be
   in non-increasing `(score, updated_at, id)` order. `id` is the primary key, unique by
   definition, so no two rows can tie on the whole key: that is an argument, and the
   planted ties are what show it is exercised.
2. *By exhibiting the ties.* The first two keys are read off the same rows and shown
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
from collections import Counter
from typing import Any

import pytest
from corpus import KNOWN_LEGAL_NAME, KNOWN_VAT_NUMBER, SMALL, build_corpus
from sqlalchemy import insert, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.people.models import Person
from pigrocrm.core.search.repository import (
    DEAL_FIELDS,
    DOCUMENT_FIELDS,
    INVOICE_FIELDS,
    ORGANIZATION_FIELDS,
    PERSON_FIELDS,
    SearchRepository,
)
from pigrocrm.core.search.schemas import SearchQuery
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
    ("organizations", Organization, ORGANIZATION_FIELDS),
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

_FIXED_GROUP_ORDER = ["organization", "person", "deal", "document", "invoice", "task"]


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
        ("organizations", "country"),
        ("people", "first_name"),
        ("deals", "name"),
        ("documents", "title"),
    ):
        moved += session.execute(
            text(
                f"UPDATE {table} SET {column} = {column} WHERE id IN ("
                f"  SELECT id FROM {table} WHERE deleted_at IS NULL ORDER BY ctid LIMIT 40)"
            )
        ).rowcount
    return moved


def _renderings(session: Session, term: str, limit: int = 5) -> Counter[bytes]:
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
    query = SearchQuery(term=term, limit=limit)
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
        "not total. Check that every branch orders by score DESC, updated_at DESC, "
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
    groups = payload["groups"]
    assert [group["entity"] for group in groups] == _FIXED_GROUP_ORDER, payload
    hits = [hit for group in groups for hit in group["hits"]]
    assert len(hits) > 1, (
        "the compared responses carry fewer than two hits between them, so nothing in them "
        f"could have been ordered differently:\n{rendering.decode()}"
    )
    assert all(hit["label"] and hit["score"] for hit in hits), rendering.decode()


def test_repeated_runs_under_four_access_paths_produce_one_byte_identical_response(
    db_session: Session,
) -> None:
    build_corpus(db_session, SMALL)

    seen = _renderings(db_session, "Ingegneria")

    _assert_one_rendering(seen, "Ingegneria")
    rendering = next(iter(seen))
    _assert_the_response_is_worth_comparing(rendering)
    assert sum(seen.values()) == _RUNS


# The term each branch is searched with in the ordering test: `Ingegneria` is in the sector
# of the corpus' organizations, deals, documents and invoices, and a surname is what people have.
_TERMS = {"people": "Rossi"}


def _plant_a_ladder(session: Session, table: str, column: str, term: str) -> None:
    """Six rows on each of three rungs of §8.5's ladder (the term itself, a prefix, a word
    inside), and `updated_at` spread over four days: score ties for `updated_at` to break,
    and `updated_at` ties for `id` to break. Raw SQL like `_reshuffle`, because an ORM
    flush would fire `onupdate` over the dates just set."""
    for offset, value in ((0, term), (6, f"{term} Plus"), (12, f"Gran {term}")):
        session.execute(
            text(
                f"UPDATE {table} SET {column} = :value WHERE id IN ("
                f"  SELECT id FROM {table} WHERE deleted_at IS NULL ORDER BY id "
                f"  LIMIT 6 OFFSET {offset})"
            ),
            {"value": value},
        )
    session.execute(
        text(
            f"UPDATE {table} SET updated_at = timestamptz '2020-01-01' "
            "+ (abs(hashtext(id::text)) % 4) * interval '1 day'"
        )
    )


@pytest.mark.parametrize("branch", _BRANCHES, ids=[branch[0] for branch in _BRANCHES])
def test_every_branch_orders_by_score_then_updated_at_then_id(
    db_session: Session, branch: tuple[str, Any, Any]
) -> None:
    """All three keys, in order, descending, on every trigram branch, read off the rows the
    repository returns.

    A branch that ordered by `updated_at DESC, id DESC` alone would be perfectly
    deterministic and completely wrong, so would one that ordered by the score ascending,
    and so would one that dropped the third key: each of them returns the planted rows in a
    different sequence from the one asserted here. The planted ties are what make that so,
    and the test says so itself rather than passing on a corpus where nothing ties.
    """
    table, model, fields = branch
    term = _TERMS.get(table, "Ingegneria")
    build_corpus(db_session, SMALL)
    _plant_a_ladder(db_session, table, fields[0].name, term)

    rows = db_session.execute(
        SearchRepository(db_session)._scored(model, fields, term, 10_000)
    ).all()

    keys = [(row[1], row[0].updated_at, row[0].id) for row in rows]
    assert len(keys) >= 18, f"{table}: only {len(keys)} rows matched the planted ladder"
    assert keys == sorted(keys, reverse=True), (
        f"{table}'s rows are not in non-increasing (score, updated_at, id) order"
    )
    pairs = list(zip(keys, keys[1:], strict=False))
    assert len({score for score, _, _ in keys}) >= 3, (
        f"{table}: the ladder gave fewer than 3 scores"
    )
    assert any(a[0] == b[0] and a[1] != b[1] for a, b in pairs), (
        f"{table}: no tie in the score was broken by updated_at, so that key was not exercised"
    )
    assert any(a[:2] == b[:2] and a[2] != b[2] for a, b in pairs), (
        f"{table}: no tie in score and updated_at was broken by id, so that key was not exercised"
    )


def test_the_first_two_ordering_keys_really_do_tie(db_session: Session) -> None:
    """The other half of the argument above: the third key is load-bearing, not decoration.

    If `score, updated_at` were already unique, `id DESC` would never break anything and
    every assertion about it would pass no matter what it said. They are not: the score is a
    four-place decimal off a four-rung ladder, and `updated_at` defaults to
    `transaction_timestamp()`, which is one value for the whole build. Both halves are
    asserted here -- collisions exist, and adding `id` removes them all.
    """
    build_corpus(db_session, SMALL)
    repo = SearchRepository(db_session)

    keys = [
        (row[1], row[0].updated_at, row[0].id)
        for row in db_session.execute(
            repo._scored(Organization, ORGANIZATION_FIELDS, "Ingegneria", 10_000)
        ).all()
    ]

    assert len(keys) > 10, (
        f"only {len(keys)} organizations matched, which is not enough rows for a tie to be meaningful"
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

    `vat_number`, `tax_code` and `email` are left NULL on purpose. `field_score`
    scores a NULL column zero and `GREATEST` then hides it behind the name's score, so all
    two hundred rows carry exactly one non-zero field score and it is the same number.
    """
    db_session.execute(
        insert(Organization),
        [
            {
                "id": uuid7(),
                "legal_name": "Identica Srl",
                "country": "IT",
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
        SearchQuery(term="Identica", limit=5), READONLY
    )
    organizations = next(group for group in results.groups if group.entity == "organization")
    assert len(organizations.hits) == 5
    assert organizations.total == 200


def test_the_property_survives_the_rows_being_physically_reordered(db_session: Session) -> None:
    """The strongest falsifier in the file, and the one that does not depend on the planner.

    Rewriting half the matching rows moves their heap tuples to the end of the table, so the
    order a sequential scan hands back is no longer the order it handed back a moment ago.
    Nothing about the *answer* changes: the `UPDATE` writes each row's own `country` back
    over itself, and it is issued as raw SQL, so SQLAlchemy's `onupdate` for `updated_at`
    does not fire and every ordering key is untouched. If the sequence were coming from the
    heap rather than from `ORDER BY`, it would move here.
    """
    db_session.execute(
        insert(Organization),
        [
            {
                "id": uuid7(),
                "legal_name": f"Riordinabile Srl {index:03d}",
                "country": "IT",
                "custom_fields": {},
            }
            for index in range(200)
        ],
    )
    db_session.flush()

    service = SearchService(db_session)
    query = SearchQuery(term="Riordinabile", limit=5)
    before = service.search_everything(query, READONLY).model_dump_json().encode()

    moved = db_session.execute(
        text(
            "UPDATE organizations SET country = country "
            "WHERE legal_name LIKE 'Riordinabile Srl %%' "
            "AND right(legal_name, 1) IN ('0', '2', '4', '6', '8')"
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
    organizations = next(
        group
        for group in service.search_everything(query, READONLY).groups
        if group.entity == "organization"
    )
    assert (len(organizations.hits), organizations.total) == (5, 200)


def test_an_exact_vat_number_puts_that_customer_first(db_session: Session) -> None:
    """§16 criterion 4's first named expectation.

    A decoy is planted whose *name* contains the same eleven digits, so the group has more
    than one hit and "first" is a claim about ranking rather than about there being only one
    row. Without it the assertion would pass against any ordering whatsoever.
    """
    build_corpus(db_session, SMALL)
    db_session.add(
        Organization(
            legal_name=f"Studio {KNOWN_VAT_NUMBER} Srl",
            country="IT",
            custom_fields={},
        )
    )
    db_session.flush()

    results = SearchService(db_session).search_everything(
        SearchQuery(term=KNOWN_VAT_NUMBER), READONLY
    )
    organizations = next(group for group in results.groups if group.entity == "organization")

    labels = [hit.label for hit in organizations.hits]
    assert len(labels) >= 2, f"only {labels} matched, so 'first' is not a ranking claim"
    assert labels[0] == KNOWN_LEGAL_NAME, (
        f"the exact P.IVA did not come first: {labels}, scores "
        f"{[str(hit.score) for hit in organizations.hits]}"
    )
    assert organizations.hits[0].field == "vat_number"


def test_a_prefix_outranks_a_mid_word_match(db_session: Session) -> None:
    """§16 criterion 4's second named expectation, at the service level rather than the
    expression level (task A7 covers the expression)."""
    db_session.add(Organization(legal_name="Vulcano Impianti Srl", country="IT", custom_fields={}))
    db_session.add(
        Organization(legal_name="Grande Vulcanologia Spa", country="IT", custom_fields={})
    )
    db_session.flush()

    results = SearchService(db_session).search_everything(
        SearchQuery(term="Vulcan", limit=5), READONLY
    )
    labels = [
        hit.label
        for hit in next(group for group in results.groups if group.entity == "organization").hits
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
    results = SearchService(db_session).search_everything(SearchQuery(term="Ingegneria"), READONLY)

    assert [group.entity for group in results.groups] == _FIXED_GROUP_ORDER
    by_count = [group.entity for group in sorted(results.groups, key=lambda group: -group.total)]
    assert by_count != _FIXED_GROUP_ORDER, (
        "on this term the fixed order and the by-count order coincide, so the assertion "
        f"above cannot tell them apart: {[(g.entity, g.total) for g in results.groups]}"
    )
