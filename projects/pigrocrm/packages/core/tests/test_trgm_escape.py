r"""Spec §8.3 point 3, measured instead of assumed.

The predicate the shipped repositories emit is `col ILIKE :p ESCAPE '\'`. Postgres plans
`LIKE ... ESCAPE` as `like_escape(pattern, escape)` inside the `~~*` operator, and with
both arguments constant, constant folding should produce a constant pattern that the
trigram index can serve. "Should" is not a measurement, so this file is the measurement.

VERDICT: **the `ESCAPE` clause stays.** It costs nothing. The Global Constraint is
unchanged, `escape_like` keeps its `escape="\\"` at every call site, and nothing was
removed anywhere.

The evidence, on `postgres:17-alpine`, after `ANALYZE`, on the REFERENCE corpus. The same
query twice, once with the clause and once without:

    SELECT id FROM customers
     WHERE deleted_at IS NULL AND ragione_sociale ILIKE '%zzqqxxww%' [ESCAPE '\']

    Bitmap Heap Scan on customers  (cost=170.85..174.86 rows=1 width=16)
      Recheck Cond: (((ragione_sociale)::text ~~* '%zzqqxxww%'::text)
                     AND (deleted_at IS NULL))
      ->  Bitmap Index Scan on ix_customers_ragione_sociale_trgm
            (cost=0.00..170.85 rows=1 width=0)
            Index Cond: ((ragione_sociale)::text ~~* '%zzqqxxww%'::text)

Identical, node for node and cost for cost, with the clause and without. The `Index Cond`
that comes out the other side carries no trace of the escape: constant folding does
collapse `like_escape('%zzqqxxww%', '\')` to the same constant pattern. Had it not, the
predicate would have degraded to a `Filter` above a heap scan -- which is exactly what the
negative control below does show, for a predicate that genuinely cannot use the index.

All nine indexes were measured the same way, and all nine are chosen with their own
`Index Cond` (costs at REFERENCE scale): customers.ragione_sociale 170.85,
customers.partita_iva 73.10, customers.email 175.10, customers.codice_fiscale 77.35,
people.nome 94.35, people.cognome 81.60, people.email 221.85, deals.nome 608.60,
documents.titolo 294.10.

**Two deviations from the brief, both forced by what the planner actually does.**

*First:* `SET LOCAL enable_seqscan = off` **is** used, which the brief forbade as turning
a measurement into a tautology. It is not one here, and the brief's instrument could not
answer its own question. With the planner entirely free, at the brief's own REFERENCE
scale and again at the former INFLATED scale of 50 000 customers, Postgres chooses a
sequential scan for a
trigram predicate either way:

      500 customers:  Seq Scan  cost=0.00..17.25    (trigram bitmap scan:   20.01)
   50 000 customers:  Seq Scan  cost=0.00..1683.00  (trigram bitmap scan: 2026.18)

and it is right to: `customers` is a narrow table, so a full scan of 50 000 rows is
cheaper than pg_trgm's large constant GIN start-up cost. That is a fact about this table's
width, not about the `ESCAPE` clause, and a test asserting "the index appears in the plan"
would have gone red at every scale this suite can afford while measuring nothing it was
written to measure. The question the task actually asks -- does `ESCAPE` make the
predicate *unindexable* -- is a question about index **usability**, and taking the
alternative away is the instrument that asks it. The planner still declines to use an
index it cannot use, which is what makes the answer informative, and the negative control
below proves exactly that.

*Second:* each plan is measured inside a savepoint in which the **sibling** trigram
indexes of the same table have been dropped. With sequential scans disabled, every partial
index on `deleted_at IS NULL` becomes a cheap way to enumerate the live rows, so the
planner will scan an unrelated trigram index whole and filter -- on `people.email` it
prefers `ix_people_cognome_trgm` at 653.12 to the correct index at 221.85, and on
`customers` it prefers one full partial-index scan at 613.38 to a BitmapOr of the four.
Neither is a statement about the column under test. Removing the siblings for the duration
of one `EXPLAIN` removes the confound; `begin_nested()` puts them back.

Had the verdict gone the other way the fallback would have been one line and is stated in
the spec: drop the `ESCAPE` clause. The backslash is already Postgres's default LIKE
escape character -- `escape_like`'s own docstring says so -- so removing the clause would
change no semantics. It would then have had to go from **every** call site in the same
commit, never from one.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from corpus import REFERENCE, build_corpus
from sqlalchemy import ColumnElement, Engine, delete, or_, select, text
from sqlalchemy.orm import InstrumentedAttribute, Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import escape_like, session_factory
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.people.models import Person
from pigrocrm.core.pipeline.models import PipelineStage

# The nine indexes this slice declares. Named here rather than derived from the models,
# so that deleting one from `__table_args__` fails this file instead of quietly agreeing
# with it.
TRGM_INDEXES = (
    "ix_customers_ragione_sociale_trgm",
    "ix_customers_partita_iva_trgm",
    "ix_customers_codice_fiscale_trgm",
    "ix_customers_email_trgm",
    "ix_people_nome_trgm",
    "ix_people_cognome_trgm",
    "ix_people_email_trgm",
    "ix_deals_nome_trgm",
    "ix_documents_titolo_trgm",
)

# A term that matches nothing, used throughout: what is being measured is whether the
# planner *can* put the predicate on the index, and a term matching a tenth of the corpus
# would make the answer depend on selectivity instead. Eight characters, so six trigrams
# are extractable from it.
_NEEDLE = "zzqqxxww"

# The columns each repository's `list()` ORs together, paired with the index that must
# serve each one. Adding a column to a repository's `or_()` without adding its index
# fails `test_every_column_every_repository_searches_has_a_usable_trigram_index` by name.
_SEARCHED: tuple[tuple[str, tuple[InstrumentedAttribute[Any], ...], tuple[str, ...]], ...] = (
    (
        "customers",
        (Customer.ragione_sociale, Customer.partita_iva, Customer.email, Customer.codice_fiscale),
        (
            "ix_customers_ragione_sociale_trgm",
            "ix_customers_partita_iva_trgm",
            "ix_customers_email_trgm",
            "ix_customers_codice_fiscale_trgm",
        ),
    ),
    (
        "people",
        (Person.nome, Person.cognome, Person.email),
        ("ix_people_nome_trgm", "ix_people_cognome_trgm", "ix_people_email_trgm"),
    ),
    ("deals", (Deal.nome,), ("ix_deals_nome_trgm",)),
    ("documents", (Document.titolo,), ("ix_documents_titolo_trgm",)),
)

_SIBLINGS: dict[str, tuple[str, ...]] = {
    index: tuple(other for other in indexes if other != index)
    for _table, _columns, indexes in _SEARCHED
    for index in indexes
}


def _corpus_with_statistics(session: Session) -> None:
    """The corpus, plus the `ANALYZE` without which every plan below is drawn from
    Postgres's default guesses rather than from this data."""
    build_corpus(session, REFERENCE)
    for table, _columns, _indexes in _SEARCHED:
        session.execute(text(f"ANALYZE {table}"))


def _isolated_plan(session: Session, index_name: str, predicate: str, table: str) -> str:
    """`EXPLAIN` for `predicate` with the trigram index under test as the table's only
    one, and sequential scans disabled.

    Both manipulations are undone by the savepoint -- `begin_nested()` and not
    `session.rollback()`, because the intent is "undo these two statements" and not "undo
    everything this session has done". `SET LOCAL` dies with the transaction the fixture
    rolls back either way.
    """
    savepoint = session.begin_nested()
    try:
        for sibling in _SIBLINGS[index_name]:
            session.execute(text(f"DROP INDEX {sibling}"))
        session.execute(text("SET LOCAL enable_seqscan = off"))
        rows = session.execute(
            text(f"EXPLAIN SELECT id FROM {table} WHERE deleted_at IS NULL AND {predicate}")
        ).all()
        return "\n".join(str(row[0]) for row in rows)
    finally:
        session.execute(text("SET LOCAL enable_seqscan = on"))
        savepoint.rollback()


def _free_plan(session: Session, predicate: str, table: str) -> str:
    """The same `EXPLAIN` with nothing taken away and nothing disabled -- the plan the
    planner reaches for on its own."""
    rows = session.execute(
        text(f"EXPLAIN SELECT id FROM {table} WHERE deleted_at IS NULL AND {predicate}")
    ).all()
    return "\n".join(str(row[0]) for row in rows)


def test_the_trigram_extension_and_the_nine_indexes_exist(db_session: Session) -> None:
    installed = db_session.scalar(
        text("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")
    )
    assert installed == 1, "pg_trgm is not installed on the test database"

    present = set(
        db_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes "
                r"WHERE schemaname = 'public' AND indexname LIKE '%\_trgm'"
            )
        ).all()
    )
    assert set(TRGM_INDEXES) <= present, f"missing: {set(TRGM_INDEXES) - present}"


def test_ilike_with_an_explicit_escape_uses_the_trigram_index(db_session: Session) -> None:
    _corpus_with_statistics(db_session)

    plan = _isolated_plan(
        db_session,
        "ix_customers_ragione_sociale_trgm",
        rf"ragione_sociale ILIKE '%{_NEEDLE}%' ESCAPE '\'",
        "customers",
    )
    assert "Bitmap Index Scan on ix_customers_ragione_sociale_trgm" in plan, (
        "the ESCAPE clause defeated the trigram index; apply the spec's one-line "
        f"fallback and remove `escape=` from every repository.\nPlan was:\n{plan}"
    )
    assert "Index Cond" in plan, f"the predicate did not reach the index.\nPlan was:\n{plan}"


def test_the_same_predicate_without_escape_also_uses_the_index(db_session: Session) -> None:
    """The control. If this one fails too, the problem is the index or the statistics,
    not the ESCAPE clause, and removing the clause would be the wrong fix."""
    _corpus_with_statistics(db_session)

    plan = _isolated_plan(
        db_session,
        "ix_customers_ragione_sociale_trgm",
        f"ragione_sociale ILIKE '%{_NEEDLE}%'",
        "customers",
    )
    assert "Bitmap Index Scan on ix_customers_ragione_sociale_trgm" in plan, f"plan was:\n{plan}"


def test_the_escape_clause_changes_the_plan_in_no_way_at_all(db_session: Session) -> None:
    """The verdict itself, as a comparison rather than an opinion: node for node and cost
    for cost, the two plans must be the same plan.

    Only the `ESCAPE` clause differs between the two statements, so any difference in the
    plan would be attributable to it and to nothing else -- which is what makes equality
    here an argument for keeping the clause rather than a coincidence. Asserted for every
    one of the nine columns, because "identical on one column" and "identical on the
    column whose type or collation differs" are not the same claim.
    """
    _corpus_with_statistics(db_session)

    for table, columns, indexes in _SEARCHED:
        for column, index_name in zip(columns, indexes, strict=True):
            with_escape = _isolated_plan(
                db_session, index_name, rf"{column.key} ILIKE '%{_NEEDLE}%' ESCAPE '\'", table
            )
            without_escape = _isolated_plan(
                db_session, index_name, f"{column.key} ILIKE '%{_NEEDLE}%'", table
            )
            # Not a comparison of two identical sequential scans: both sides have to have
            # reached the index, or "the same plan" would be a statement about nothing.
            assert f"Bitmap Index Scan on {index_name}" in with_escape, with_escape
            assert with_escape == without_escape, (
                f"{table}.{column.key}: the ESCAPE clause changed the plan.\n"
                f"With:\n{with_escape}\nWithout:\n{without_escape}"
            )


def test_every_column_every_repository_searches_has_a_usable_trigram_index(
    db_session: Session,
) -> None:
    """Nine indexes, not four. A repository ORs several columns together and a four-way
    OR is planned as a BitmapOr over four bitmap index scans -- a single unindexed branch
    collapses the whole thing into one heap scan with a filter, so indexing only each
    entity's display name would have bought nothing.
    """
    _corpus_with_statistics(db_session)

    for table, columns, indexes in _SEARCHED:
        for column, index_name in zip(columns, indexes, strict=True):
            plan = _isolated_plan(
                db_session, index_name, rf"{column.key} ILIKE '%{_NEEDLE}%' ESCAPE '\'", table
            )
            assert f"Bitmap Index Scan on {index_name}" in plan, (
                f"{index_name} unused.\nPlan was:\n{plan}"
            )
            assert "Index Cond" in plan, (
                f"{table}.{column.key}: the predicate did not reach the index.\nPlan was:\n{plan}"
            )


def test_the_repositories_really_do_emit_the_clause_this_file_measures(
    db_session: Session,
) -> None:
    """The plans above are measured on hand-written SQL, which would be worth nothing if
    SQLAlchemy rendered `escape="\\\\"` as something else. This compiles the four-way
    `or_()` `CustomerRepository.list` builds and reads the SQL back.

    Hand-written SQL is used for the plans on purpose -- `gincostestimate` cannot count
    the matching trigrams of a bound parameter, so an ORM-compiled statement measures the
    planner's ignorance of the pattern rather than the escape clause -- and this test is
    what keeps the two forms tied together.
    """
    like = f"%{escape_like(_NEEDLE)}%"
    branches: list[ColumnElement[bool]] = [
        column.ilike(like, escape="\\") for column in _SEARCHED[0][1]
    ]
    stmt = select(Customer.id).where(Customer.deleted_at.is_(None)).where(or_(*branches))
    sql = str(stmt.compile(db_session.get_bind()))

    assert sql.count("ESCAPE") == len(branches), sql


def test_a_lowered_column_cannot_use_the_index_even_with_seqscan_disabled(
    db_session: Session,
) -> None:
    """The negative control that makes `enable_seqscan = off` an instrument rather than a
    tautology, and spec §8.2's note pinned at the same time.

    `lower(ragione_sociale) LIKE ...` cannot be served by an index on the raw column, and
    forcing the planner's hand does not change that -- it falls back to a `Filter`. Which
    is also why no index expression in this slice wraps its column in `lower()`: doing so
    would make the ILIKE the repositories actually emit unable to use the index, trading
    this failure for its mirror image.
    """
    _corpus_with_statistics(db_session)

    plan = _isolated_plan(
        db_session,
        "ix_customers_ragione_sociale_trgm",
        f"lower(ragione_sociale) LIKE '%{_NEEDLE}%'",
        "customers",
    )
    assert "Index Cond" not in plan, f"plan was:\n{plan}"
    assert "Filter: (lower(" in plan, f"plan was:\n{plan}"


def test_a_two_character_pattern_degrades_the_index_to_a_full_scan(db_session: Session) -> None:
    """The reason the palette refuses to search below three characters, measured.

    No trigram can be extracted from a two-character term, so pg_trgm has nothing to look
    up. Postgres still accepts the predicate as an `Index Cond` -- which is why this is
    not written as "the index is unused" -- but the scan behind it has to walk the whole
    index and recheck every row, and the estimate says so. Observed pairs, two-character
    against eight-character on the same column and the same corpus: 7523.22 against
    170.85, and 1282.78 against 867.85.

    Only the *ordering* is asserted, not a ratio. The absolute estimates move by an order
    of magnitude between runs because `gincostestimate` reads the index's own `relpages`
    and `reltuples` out of `pg_class`, which an autovacuum on the empty table between
    tests can reset; the sign of the difference is the part that is a property of the
    patterns rather than of the statistics. That difference is the sequential scan the
    three-character floor exists to prevent, wearing an index's name.
    """
    _corpus_with_statistics(db_session)

    def _index_cost(pattern: str) -> float:
        plan = _isolated_plan(
            db_session,
            "ix_customers_ragione_sociale_trgm",
            f"ragione_sociale ILIKE '{pattern}'",
            "customers",
        )
        line = next(row for row in plan.splitlines() if "Bitmap Index Scan on ix_customers" in row)
        return float(line.rsplit("cost=", 1)[1].split("..", 1)[1].split(" ", 1)[0])

    two_characters = _index_cost("%zz%")
    eight_characters = _index_cost(f"%{_NEEDLE}%")
    assert two_characters > eight_characters, (
        f"a two-character pattern was estimated no dearer ({two_characters}) than an "
        f"eight-character one ({eight_characters}); the three-character floor rests on "
        "the difference between them"
    )


@pytest.fixture(scope="module")
def customers_at_reference_scale(db_engine: Engine) -> Iterator[Engine]:
    """REFERENCE-scale corpus, committed, `VACUUM (ANALYZE)`d for real, removed and
    re-vacuumed afterwards -- ORB-9's fix for the one test below that needs it.

    Every other fixture in this file runs `build_corpus` inside `db_session`'s savepoint,
    which is right for a test that asks whether an index *can* serve a predicate: `ANALYZE`
    works fine on an uncommitted table. It is wrong for the test below, which asks what the
    free planner does when nothing forces its hand, because `relpages` reflects the table's
    real physical size and a savepoint's rollback never shrinks a heap file back down.
    `test_search_plan.py`'s `inflated` fixture (gone with REB-580) named the consequence
    on its own way out: "`test_trgm_escape.py`'s 500-row seq scan is costed on a heap of
    dead pages" left behind by however many of this file's own earlier tests happened to
    insert and roll
    back the same 500 rows into `customers` first. Measured 2026-09-10, running this file's
    tests in file order left `customers` at 52 pages instead of a fresh build's 11, and
    priced the free planner's own `Seq Scan` at 58.25 instead of 17.25 -- margin enough that
    a different runner, holding a different amount of leftover bloat, picks the other plan
    with no line of code in between (ORB-9).

    Committed and vacuumed for real, on a connection outside any transaction -- `VACUUM`
    cannot run inside `db_session`'s savepoint at all -- so `customers`'s statistics
    describe this corpus alone, not however many prior tests' debris the session
    accumulated. Cleaned up and re-vacuumed on the way out for the reason `inflated` gives
    for the same step: leaving `customers` bloated here would be exactly the noise this
    fixture exists to remove, for whichever test runs next.
    """
    # `customers` should be empty of live rows here -- nothing before this fixture in the
    # session commits into it and survives its own teardown -- but that is a claim about
    # every other file sharing this container, not something this fixture controls, so it
    # is checked rather than assumed: the failure this file exists to prevent is exactly a
    # plan silently costed on a corpus that turned out not to be REFERENCE-scale. A plain
    # `VACUUM` before the insert, not only `VACUUM (ANALYZE)` after it, is what actually
    # discards whatever dead pages earlier tests' rollbacks left behind: at REFERENCE scale
    # the leftover bloat is not a rounding error next to the corpus itself the way it is for
    # `inflated`'s fifty thousand rows, so inserting into it first and only vacuuming
    # afterwards still costs a `Seq Scan` on however many dead pages happened to precede it.
    # Truncating the trailing empty pages before this corpus exists is what makes
    # `customers`'s physical size a property of this fixture alone.
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        live = connection.execute(text("SELECT count(*) FROM customers")).scalar_one()
        assert live == 0, (
            f"{live} committed customers survived an earlier test; this fixture's plan "
            "would not be measured at REFERENCE scale"
        )
        connection.execute(text("VACUUM customers"))
    factory = session_factory(db_engine)
    with factory() as session:
        pre_existing_stages = set(session.scalars(select(PipelineStage.id)).all())
        build_corpus(session, REFERENCE)
        session.commit()
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM (ANALYZE) customers"))
    try:
        yield db_engine
    finally:
        with factory() as session:
            # Children first, `inflated`'s own order: invoices and documents reference
            # customers and deals, people and deals reference customers, deals references
            # a stage.
            session.execute(delete(Invoice))
            session.execute(delete(Document))
            session.execute(delete(Deal))
            session.execute(delete(Person))
            session.execute(delete(Customer))
            session.execute(
                delete(PipelineStage).where(PipelineStage.id.notin_(pre_existing_stages))
            )
            session.commit()
        # All five tables this fixture committed into, not only `customers`: `inflated`
        # re-vacuums every table it dirtied for the same reason, and a plan assertion
        # added to this file later against `people`, `deals`, `documents` or `invoices`
        # deserves the same clean slate this test needed.
        with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            for table in ("customers", "people", "deals", "documents", "invoices"):
                connection.execute(text(f"VACUUM (ANALYZE) {table}"))


@pytest.fixture
def customers_session(customers_at_reference_scale: Engine) -> Iterator[Session]:
    """A plain session on the committed, vacuumed corpus above.

    Not `db_session`: that fixture opens a savepoint this corpus was deliberately built
    outside of, and a `SET` here should affect the whole session rather than roll back with
    one."""
    with session_factory(customers_at_reference_scale)() as session:
        yield session


def test_the_planner_left_alone_still_prefers_a_sequential_scan_at_this_scale(
    customers_session: Session,
) -> None:
    """The free planner's own choice on a genuinely REFERENCE-scale `customers`: the
    `Seq Scan` the forced plans above never show, with `Filter` doing the excluding here
    and `Index Cond` doing it above. 500 narrow rows are cheaper to read whole, and it is
    right not to use the trigram index -- this is what makes `enable_seqscan = off`
    necessary in `_isolated_plan` above, and it is pinned here so a future reader does not
    take those forced plans for a claim about what production does on a small table.

    No longer `@pytest.mark.planner`: that marker meant the plan depended on which machine
    ran it, and `customers_at_reference_scale` is what removes the dependency, so this
    runs in the ordinary gate instead of the single-machine preflight lane the marker used
    to earn -- `pigrocrm-py-planner`, retired by REB-90 once `test_sort_plan.py` was the
    last test anywhere in the repository still carrying it.

    `customers_at_reference_scale` is what makes this a property of the data instead of a
    property of which tests happened to run first: see its own docstring for the table-bloat
    mechanism that used to flip this same assertion on a hosted runner while it passed on a
    devbox minutes earlier (ORB-9). The falsifier right below shows the identical query on
    the identical corpus losing this `Seq Scan` the moment the choice is taken away from the
    planner, so this is not an assertion that happens to hold for every plan Postgres could
    produce.

    If this ever starts failing with the planner genuinely preferring the index on its own,
    that is good news and the assertion inverts; it is not a regression.
    """
    plan = _free_plan(
        customers_session, rf"ragione_sociale ILIKE '%{_NEEDLE}%' ESCAPE '\'", "customers"
    )
    assert "Seq Scan on customers" in plan, f"plan was:\n{plan}"


def test_the_seq_scan_above_stops_appearing_once_the_planner_loses_the_choice(
    customers_session: Session,
) -> None:
    """The falsifier the test above names: on the identical corpus and the identical query,
    taking `enable_seqscan` away leaves no `Seq Scan on customers` in the plan at all. The
    assertion above is measuring a real, losable choice, not one that happens to hold no
    matter which plan Postgres produces."""
    customers_session.execute(text("SET enable_seqscan = off"))
    try:
        plan = _free_plan(
            customers_session, rf"ragione_sociale ILIKE '%{_NEEDLE}%' ESCAPE '\'", "customers"
        )
    finally:
        customers_session.execute(text("SET enable_seqscan = on"))
    assert "Seq Scan on customers" not in plan, f"plan was:\n{plan}"


def test_similarity_needs_no_lower_in_the_index_expression(db_session: Session) -> None:
    """Spec §8.2's non-obvious note, pinned so nobody "fixes" the index by wrapping the
    column in `lower()` -- which would make ILIKE on the raw column unable to use it."""
    same = db_session.scalar(text("SELECT similarity('Rossi', 'rossi')"))
    assert same == 1.0
