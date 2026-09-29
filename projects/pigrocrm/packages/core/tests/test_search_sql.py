"""The search's ranking, its page and its bounded count are the database's, asserted on
the statements it sends rather than on the plan it gets.

Until REB-580 `test_search_plan.py` asserted these on `EXPLAIN ANALYZE` over a corpus of
fifty thousand rows per table, together with which index the planner picked. The CRM gives
every space its own database, so a space's tables hold hundreds of rows, where the planner
never reaches for those indexes because a sequential scan is cheaper; the plan assertions
measured a property no user of this product meets, and went. What is not about scale stays
here, over empty tables: the §8.5 score is what the database orders by, the page is
the database's own `LIMIT`, and the count stops at its ceiling. The statements are captured
off the engine, exactly as the repository sends them, so a repository that stopped using
its `LIMIT` or moved the ranking into Python cannot pass by having a test that never saw the
SQL.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event, insert
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.search.repository import SearchRepository
from pigrocrm.core.search.schemas import COUNT_CEILING, PER_CLASS_LIMIT, SearchGroup

# One term for every branch: what is asserted is the shape of the statements, not what
# they find, so the term only has to be one the branches accept. `invoices` reads a purely
# numeric term as a fiscal number and takes another path; a word keeps it on this one.
_TERM = "Ross"
# A term the saturation test plants on one row more than the ceiling admits, so what the
# ceiling does when it is reached is asserted on rows the test can count.
_TERM_PAST_THE_CEILING = "Srl"

_BRANCHES: dict[str, Callable[[SearchRepository], Callable[[str, int], SearchGroup]]] = {
    "customers": lambda repo: repo.customers,
    "people": lambda repo: repo.people,
    "deals": lambda repo: repo.deals,
    "documents": lambda repo: repo.documents,
    "invoices": lambda repo: repo.invoices,
}


def _capture(session: Session, run: Callable[[], object]) -> list[tuple[str, Any]]:
    """Every statement `run()` sends to the server, verbatim, with its parameters."""
    captured: list[tuple[str, Any]] = []

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        captured.append((statement, parameters))

    bind = session.get_bind()
    event.listen(bind, "before_cursor_execute", listener)
    try:
        run()
    finally:
        event.remove(bind, "before_cursor_execute", listener)
    return captured


def _bound_limits(statement: str, parameters: Any) -> list[int]:
    """The values bound to every `LIMIT` of a statement, in order of appearance.

    The driver's paramstyle is `%(name)s`; the name after each `LIMIT` is looked up in the
    parameters, so the assertion is about what the server was asked for, not about a
    literal in the SQL text.
    """
    values: list[int] = []
    for chunk in statement.split("LIMIT ")[1:]:
        name = chunk[chunk.index("(") + 1 : chunk.index(")")]
        values.append(int(parameters[name]))
    return values


@pytest.fixture
def captures(db_session: Session) -> dict[str, tuple[SearchGroup, list[tuple[str, Any]]]]:
    """Every branch's result and its captured statements, over empty tables.

    No corpus: what is asserted is the shape of the statements the repository sends, which
    is the same whether the tables hold nine thousand rows or none, and the tables here
    hold none. A REFERENCE build per test was 4 to 6s of the file's 12s for nothing it
    looked at (REB-597).
    """
    repo = SearchRepository(db_session)
    out: dict[str, tuple[SearchGroup, list[tuple[str, Any]]]] = {}
    for name, pick in _BRANCHES.items():
        out[name] = _run(db_session, pick(repo))
    return out


def _run(
    session: Session, method: Callable[[str, int], SearchGroup], term: str = _TERM
) -> tuple[SearchGroup, list[tuple[str, Any]]]:
    result: list[SearchGroup] = []
    captured = _capture(session, lambda: result.append(method(term, PER_CLASS_LIMIT)))
    return result[0], captured


def _scored(captured: list[tuple[str, Any]]) -> tuple[str, Any]:
    """The one statement that ranks: it orders by something and is not the count."""
    ranked = [(s, p) for s, p in captured if "ORDER BY" in s and not s.startswith("SELECT count")]
    assert len(ranked) == 1, "expected exactly one ranking statement, got:\n\n" + "\n\n".join(
        s for s, _ in captured
    )
    return ranked[0]


def _count(captured: list[tuple[str, Any]]) -> tuple[str, Any]:
    counts = [(s, p) for s, p in captured if s.startswith("SELECT count")]
    assert len(counts) == 1, "expected exactly one count statement, got:\n\n" + "\n\n".join(
        s for s, _ in captured
    )
    return counts[0]


def _limit_inside_the_counted_subquery(statement: str) -> bool:
    """A `LIMIT` outside `count(*)` would cap the number, not the scan that produced it."""
    opened = statement.index("FROM (")
    closed = statement.rindex(") AS ")
    limit = statement.index("LIMIT ")
    return opened < limit < closed


def test_a_limit_outside_the_count_is_not_mistaken_for_the_ceiling() -> None:
    """The helper's own falsifier: a `LIMIT` after the derived table caps the number, not
    the scan, and the bind's own parenthesis must not pass for the table's."""
    assert not _limit_inside_the_counted_subquery(
        "SELECT count(*) AS count_1 FROM (SELECT 1 FROM customers) AS anon_1 LIMIT %(p)s"
    )
    assert _limit_inside_the_counted_subquery(
        "SELECT count(*) AS count_1 FROM (SELECT 1 FROM customers LIMIT %(p)s) AS anon_1"
    )


def test_the_score_is_what_the_database_orders_by(
    captures: dict[str, tuple[SearchGroup, list[tuple[str, Any]]]],
) -> None:
    """A score computed in Python could not appear in the `ORDER BY`."""
    for branch, (_, captured) in captures.items():
        statement, _ = _scored(captured)
        order_by = statement.split("ORDER BY", 1)[1]
        assert "word_similarity" in order_by, (
            f"{branch}: the §8.5 score is not what the database ordered by; it was computed "
            f"somewhere else:\n{order_by}"
        )


def test_the_page_is_the_databases_limit(
    captures: dict[str, tuple[SearchGroup, list[tuple[str, Any]]]],
) -> None:
    """The page is cut by the server, not by slicing the rows it handed back."""
    for branch, (group, captured) in captures.items():
        statement, parameters = _scored(captured)
        assert _bound_limits(statement, parameters) == [PER_CLASS_LIMIT], (
            f"{branch}: the ranking statement does not ask the server for a page of "
            f"{PER_CLASS_LIMIT}:\n{statement}"
        )
        assert len(group.hits) <= PER_CLASS_LIMIT, branch


def test_the_count_stops_at_its_ceiling(
    captures: dict[str, tuple[SearchGroup, list[tuple[str, Any]]]],
) -> None:
    """`LIMIT 201` inside the count is what keeps a term matching thousands of rows as
    cheap as one matching two: the server stops reading after the ceiling and the result is
    declared a minimum rather than counted to the end."""
    for branch, (group, captured) in captures.items():
        statement, parameters = _count(captured)
        assert _bound_limits(statement, parameters) == [COUNT_CEILING + 1], (
            f"{branch}: the count is not bounded at {COUNT_CEILING + 1} on the server:\n{statement}"
        )
        assert _limit_inside_the_counted_subquery(statement), (
            f"{branch}: the limit caps the number rather than the rows counted:\n{statement}"
        )
        assert group.totale <= COUNT_CEILING, branch


def test_a_count_past_the_ceiling_is_the_ceiling_and_says_so(db_session: Session) -> None:
    """The saturated case, on one row more than the ceiling planted for it: the server stops
    at the ceiling and the result is declared a minimum, so a client reads "at least 200"
    and not a number nobody counted."""
    db_session.execute(
        insert(Customer),
        [{"ragione_sociale": f"Cliente {n} Srl"} for n in range(COUNT_CEILING + 1)],
    )
    db_session.flush()
    repo = SearchRepository(db_session)
    group, captured = _run(db_session, repo.customers, _TERM_PAST_THE_CEILING)
    statement, parameters = _count(captured)
    assert _bound_limits(statement, parameters) == [COUNT_CEILING + 1]
    assert _limit_inside_the_counted_subquery(statement), statement
    assert group.totale == COUNT_CEILING, group
    assert group.totale_e_un_minimo is True, group
