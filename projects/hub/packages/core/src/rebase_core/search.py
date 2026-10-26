"""Trigram search: an escaped `ILIKE` decides which rows match, `pg_trgm` similarity
decides the order among them (REB-285).

Reimplements natively, and far more narrowly, what PigroCRM's own search scores with
per-field weights and a similarity floor
(`projects/pigrocrm/packages/core/src/pigrocrm/core/search/scoring.py`) -- this package
may import neither adapter, and PigroCRM is neither (`projects/hub/AGENTS.md`, "the one
rule"). REB-285's own card asks for less than the CRM built: "no full-text ranking;
trigram similarity ordered by creation date is enough for a list an admin scans."

Matching and ordering are deliberately two different things. `matches_any` is an
`ILIKE '%term%'` across every searchable column -- accelerated by a `gin_trgm_ops`
index per column (migration 0013) -- and it is what decides whether a row is in the
result at all: a search for `"AI"` must still find "Miriai", and `similarity()` alone
would not, because a two-character term shares too few trigrams with a long field to
clear any threshold worth setting. `similarity_score` never filters; it only orders the
rows `matches_any` already chose, highest first, so a name that starts with the term
outranks one that merely contains it somewhere in the middle.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, case, func, or_, select

# Bounded for the same reason every free-text field in this package is: an unbounded
# term reaching `ILIKE` and `similarity()` on every row is a denial of service with
# extra steps. Twice `NAME_MAX_LENGTH` (120) is room for a full "nome cognome" typed by
# hand and nothing beyond it.
SEARCH_MAX_LENGTH = 200


def escape_like(term: str) -> str:
    r"""Escapes SQL LIKE/ILIKE metacharacters in a user-supplied search term.

    Unescaped, "%" means "any run of characters" and "_" means "any one character" to
    LIKE/ILIKE, so a user searching for a literal "_" or "%" would match unrelated rows
    that merely have *some* character in that position -- a correctness bug, not a SQL
    injection risk, since callers always bind the escaped term as a parameter and never
    interpolate it into SQL text.

    The backslash is escaped first, before "%" and "_": escaping either of those first
    would double-escape the backslashes that step introduces. Pass the result to
    `.ilike(pattern, escape="\\")` -- a single backslash -- so the database applies the
    same escape convention this function assumes."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def matches_any(columns: Sequence[Any], term: str) -> ColumnElement[bool]:
    """Whether any of `columns` contains `term`, case-insensitively. A column that is
    NULL for this row (a lead's `posizione`, a card's `origine`) simply never matches --
    `ILIKE` against NULL is NULL, which `or_` treats as "no", not as an error."""
    pattern = f"%{escape_like(term)}%"
    return or_(*(column.ilike(pattern, escape="\\") for column in columns))


def similarity_score(columns: Sequence[Any], term: str) -> ColumnElement[float]:
    """The best `pg_trgm` similarity `term` has with any of `columns`, 0 for a NULL
    one -- `similarity(NULL, term)` is NULL, and `greatest` treats a NULL argument as
    smaller than every real one only once every argument is `coalesce`d, so that is
    done per column before `greatest` sees them."""
    return func.greatest(*(func.coalesce(func.similarity(column, term), 0.0) for column in columns))


def any_element_matches(array: Any, term: str) -> ColumnElement[bool]:
    """Whether any text element of the JSONB `array` contains `term`, case-insensitively,
    `%` and `_` taken for themselves. Only an array is expanded:
    `jsonb_array_elements_text` raises on a string or an object, which a card written by
    hand may hold, so the guard is inside the call, not beside it in an `AND`, since SQL
    does not promise the order it runs in. A NULL `array` has no element, so no match."""
    only_an_array = case((func.jsonb_typeof(array) == "array", array))
    elements = func.jsonb_array_elements_text(only_an_array).table_valued("value")
    pattern = f"%{escape_like(term)}%"
    return select(elements.c.value).where(elements.c.value.ilike(pattern, escape="\\")).exists()
