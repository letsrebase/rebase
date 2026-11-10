"""The search's ranking, its page and its bounded count are the database's, asserted on what
the database hands back rather than on the text of the statements.

Until REB-580 `test_search_plan.py` asserted these on `EXPLAIN ANALYZE` over a corpus of
fifty thousand rows per table, together with which index the planner picked. The CRM gives
every space its own database, so a space's tables hold hundreds of rows, where the planner
never reaches for those indexes because a sequential scan is cheaper; the plan assertions
measured a property no user of this product meets, and went. What is not about scale stays
here: the page is the database's own `LIMIT` over the database's own order, and the count
stops at its ceiling. The first is observed at the connection (no statement of a search
returns more rows than a page, though many more rows match), so a repository that fetched
the matches and ranked or sliced them in Python cannot pass; the ranking itself, the floor
and the tie-break are asserted on results in `test_search_scoring.py`,
`test_search_service.py` and `test_search_determinism.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from corpus import SMALL, build_corpus
from sqlalchemy import event, insert
from sqlalchemy.orm import Session

from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.search.repository import SearchRepository
from pigrocrm.core.search.schemas import COUNT_CEILING, PER_CLASS_LIMIT, SearchGroup

# A term that matches more rows than a page in the corpus, per branch: a sector for the
# entities that carry one in their name, the start of the email every generated person has.
# `invoices` reads a purely numeric term as a fiscal number and takes another path; a word
# keeps it on the trigram one.
_Pick = Callable[[SearchRepository], Callable[[str, int], SearchGroup]]
_BRANCHES: dict[str, tuple[str, _Pick]] = {
    "organizations": ("Ingegneria", lambda repo: repo.organizations),
    "people": ("persona", lambda repo: repo.people),
    "deals": ("Ingegneria", lambda repo: repo.deals),
    "documents": ("Ingegneria", lambda repo: repo.documents),
    "invoices": ("Ingegneria", lambda repo: repo.invoices),
}
# A term the saturation test plants on one row more than the ceiling admits, so what the
# ceiling does when it is reached is asserted on rows the test can count.
_TERM_PAST_THE_CEILING = "Srl"


def _rows_returned(
    session: Session, run: Callable[[], SearchGroup]
) -> tuple[SearchGroup, list[int]]:
    """The group `run()` answers and, for every statement it sent, how many rows the server
    returned."""
    counts: list[int] = []

    def listener(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        counts.append(cursor.rowcount)

    bind = session.get_bind()
    event.listen(bind, "after_cursor_execute", listener)
    try:
        group = run()
    finally:
        event.remove(bind, "after_cursor_execute", listener)
    return group, counts


def test_no_statement_of_a_search_returns_more_than_a_page(db_session: Session) -> None:
    """The page is cut by the server, not by slicing the rows it handed back: many more
    rows match than a page holds, and no statement hands more than a page over."""
    build_corpus(db_session, SMALL)
    repo = SearchRepository(db_session)

    for branch, (term, pick) in _BRANCHES.items():
        method = pick(repo)
        group, counts = _rows_returned(db_session, partial(method, term, PER_CLASS_LIMIT))

        assert group.total > PER_CLASS_LIMIT, f"{branch}: {term!r} matched too little to show it"
        assert len(group.hits) == PER_CLASS_LIMIT, branch
        assert max(counts) <= PER_CLASS_LIMIT, (
            f"{branch}: a statement returned {max(counts)} rows for a page of {PER_CLASS_LIMIT}"
        )


def test_the_page_holds_the_best_rows_and_not_the_first_ones(db_session: Session) -> None:
    """The server orders before it cuts: the exact match is written last, after twelve rows
    that only start with the term, and is still the one row of a page of one."""
    db_session.execute(
        insert(Organization),
        [{"legal_name": f"Vulcano Impianti {n} Srl"} for n in range(12)]
        + [{"legal_name": "Vulcano"}],
    )
    db_session.flush()

    group = SearchRepository(db_session).organizations("Vulcano", 1)

    assert [hit.label for hit in group.hits] == ["Vulcano"]
    assert group.total == 13


def test_a_count_past_the_ceiling_is_the_ceiling_and_says_so(db_session: Session) -> None:
    """The saturated case, on one row more than the ceiling planted for it: the server stops
    at the ceiling and the result is declared a minimum, so a client reads "at least 200"
    and not a number nobody counted."""
    db_session.execute(
        insert(Organization),
        [{"legal_name": f"Cliente {n} Srl"} for n in range(COUNT_CEILING + 1)],
    )
    db_session.flush()
    repo = SearchRepository(db_session)

    group, counts = _rows_returned(
        db_session, lambda: repo.organizations(_TERM_PAST_THE_CEILING, PER_CLASS_LIMIT)
    )

    assert group.total == COUNT_CEILING, group
    assert group.total_is_minimum is True, group
    assert len(group.hits) == PER_CLASS_LIMIT
    assert max(counts) <= PER_CLASS_LIMIT, counts
