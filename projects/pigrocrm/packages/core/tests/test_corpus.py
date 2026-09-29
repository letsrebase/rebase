"""The corpus is a test fixture, so it gets a test: a generator that silently produces
400 rows instead of what its scale says turns every dashboard assertion on it into a
measurement of nothing.

REFERENCE is built once, for the row counts that are its definition; everything else the
generator promises holds at any scale and is checked on SMALL (REB-597).
"""

# Top-level, not `from .corpus import ...`: none of this repository's three test roots
# has an `__init__.py`, so a relative import has no parent package to resolve against.
# `orologio.py` and `periodo_fiscale.py` are the shipped precedent for a shared test
# helper module, and `corpus` is likewise unique across all three roots.
from corpus import KNOWN_PARTITA_IVA, KNOWN_RAGIONE_SOCIALE, REFERENCE, SMALL, build_corpus
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.people.models import Person


def test_build_corpus_produces_exactly_the_row_counts_it_claims(db_session: Session) -> None:
    build_corpus(db_session, REFERENCE)

    assert db_session.scalar(select(func.count()).select_from(Customer)) == 500
    assert db_session.scalar(select(func.count()).select_from(Person)) == 800
    assert db_session.scalar(select(func.count()).select_from(Deal)) == 2000
    assert db_session.scalar(select(func.count()).select_from(Document)) == 1000
    assert db_session.scalar(select(func.count()).select_from(Invoice)) == 5000
    # And `(anno, numero)` really is unique across them, which `uq_invoices_anno_numero`
    # would have refused anyway -- asserted here so the *reason* the generator divides for
    # the year and takes the remainder for the number is written down where it is checked,
    # rather than surfacing as an IntegrityError somebody has to diagnose.
    assert (
        db_session.scalar(
            select(func.count(func.distinct(func.concat(Invoice.anno, "/", Invoice.numero))))
        )
        == 5000
    )


def test_build_corpus_plants_the_one_customer_every_search_test_looks_for(
    db_session: Session,
) -> None:
    build_corpus(db_session, SMALL)

    row = db_session.scalar(select(Customer).where(Customer.partita_iva == KNOWN_PARTITA_IVA))
    assert row is not None
    assert row.ragione_sociale == KNOWN_RAGIONE_SOCIALE


def test_build_corpus_is_deterministic_for_a_given_seed(db_session: Session) -> None:
    """Criterion 4 asserts byte-identical JSON across runs; that is only meaningful if
    the data underneath is byte-identical too.

    The first corpus is undone with an explicit `begin_nested()` savepoint rather than
    the bare `session.rollback()` the brief proposed. The intent here is "undo this one
    build", and a savepoint says exactly that; a bare rollback says "undo everything
    this session has done", which happens to be survivable only because `db_session`
    joins its connection with `join_transaction_mode="create_savepoint"`. Naming the
    scope means the test keeps working if that fixture detail ever changes.
    """
    savepoint = db_session.begin_nested()
    build_corpus(db_session, SMALL, seed=7)
    first = list(
        db_session.scalars(
            select(Customer.ragione_sociale).order_by(Customer.ragione_sociale).limit(20)
        ).all()
    )
    savepoint.rollback()

    build_corpus(db_session, SMALL, seed=7)
    second = list(
        db_session.scalars(
            select(Customer.ragione_sociale).order_by(Customer.ragione_sociale).limit(20)
        ).all()
    )

    assert first == second
    # A determinism assertion that compared two empty lists would pass for the wrong
    # reason, so the corpus has to have produced something to compare in the first place.
    assert len(first) == 20


def test_build_corpus_varies_with_the_seed(db_session: Session) -> None:
    """The other half of determinism: `seed` has to actually reach the generator. A
    `build_corpus` that ignored its argument entirely would satisfy the test above.
    """
    savepoint = db_session.begin_nested()
    build_corpus(db_session, SMALL, seed=7)
    first = list(
        db_session.scalars(
            select(Customer.ragione_sociale).order_by(Customer.ragione_sociale).limit(20)
        ).all()
    )
    savepoint.rollback()

    build_corpus(db_session, SMALL, seed=8)
    second = list(
        db_session.scalars(
            select(Customer.ragione_sociale).order_by(Customer.ragione_sociale).limit(20)
        ).all()
    )

    assert first != second


def test_build_corpus_leaves_some_deals_without_a_value(db_session: Session) -> None:
    """Task B9 counts those rows separately and never sums them as zero; the corpus has
    to contain some or that branch is never executed."""
    build_corpus(db_session, SMALL)
    without = db_session.scalar(
        select(func.count()).select_from(Deal).where(Deal.valore_previsto.is_(None))
    )
    assert without is not None and without > 0


def test_build_corpus_leaves_some_people_without_a_surname(db_session: Session) -> None:
    """Task A3's `NULLS LAST` cursor has no exerciser without rows in the null tail."""
    build_corpus(db_session, SMALL)
    without = db_session.scalar(
        select(func.count()).select_from(Person).where(Person.cognome.is_(None))
    )
    assert without is not None and without > 0


def test_build_corpus_returns_the_ids_its_callers_need(db_session: Session) -> None:
    """`CorpusIds` is the only handle a search test has on the rows it just created;
    a build that returned empty lists would leave every downstream test filtering on
    nothing."""
    ids = build_corpus(db_session, SMALL)

    assert len(ids.customer_ids) == SMALL.customers
    assert len(ids.deal_ids) == SMALL.deals
    assert len({ids.stage_open_id, ids.stage_won_id, ids.stage_lost_id}) == 3
    assert db_session.get(Customer, ids.customer_ids[0]) is not None
    assert db_session.get(Deal, ids.deal_ids[-1]) is not None
