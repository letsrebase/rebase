"""What the global search accepts and returns.

Spec §8.1 fixes the searched fields, §8.5 the ordering and the counting, §8.6 the three
interface states. The shape here is what makes those three states expressible without the
client inferring anything: `totale` is the real count and `totale_e_un_minimo` says
whether it was truncated, so "5 of 500" and "5 of 5" are different responses rather than
the same list of five.
"""

import re
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.validation import SafeStr

# Spec §8.3: a trigram index cannot serve a pattern from which no trigram can be
# extracted, so below three characters the search would be a sequential scan; and a
# two-character term on 50 000 customers returns thousands of rows, which is not an answer
# either. The palette says "continua a scrivere" and issues no request; this bound is the
# server-side half of the same rule.
MIN_TERM_LENGTH = 3
# A term longer than this is not a search, and the column being searched is at most 320
# characters anyway (`customers.email`).
MAX_TERM_LENGTH = 100
# Spec §8.5: the palette does not paginate. Five per class plus the real count.
PER_CLASS_LIMIT = 5
# Exact up to here, then declared as a minimum. Implemented as `count(*)` over a subquery
# with `LIMIT 201`: exact when exactness matters, cheap when it does not, never a lie.
COUNT_CEILING = 200

SearchEntity = Literal["customer", "person", "deal", "document", "invoice"]


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    termine: SafeStr = Field(min_length=MIN_TERM_LENGTH, max_length=MAX_TERM_LENGTH)
    limite: int = Field(default=PER_CLASS_LIMIT, ge=1, le=20)
    # One azienda's records (REB-623): customers, deals, documents and invoices by their
    # own column, people through their customer. `None` is every azienda.
    azienda_id: UUID | None = None


class SearchHit(BaseModel):
    entity: SearchEntity
    id: UUID
    # What the palette renders on the row. Built by the repository from the entity's own
    # identifying columns, never by the frontend concatenating fields -- that would be
    # business logic in the browser.
    etichetta: str
    sottotitolo: str | None
    # `Decimal`, never float: a float score renders as 0.6000000238418579 and would break
    # criterion 4's byte-identical requirement. The bounds match the `Numeric(6, 4)` the
    # SQL expression produces, so a value the expression could not have produced is
    # refused here rather than rounded silently.
    punteggio: Decimal = Field(max_digits=6, decimal_places=4)
    # Which field produced the score. Shown as a hint ("P.IVA", "email") so a match on a
    # column the row does not display is not a mystery.
    campo: str


class SearchGroup(BaseModel):
    entity: SearchEntity
    hits: list[SearchHit]
    # The real count of matching rows, exact up to COUNT_CEILING.
    totale: int
    # True when `totale` is COUNT_CEILING and the real count may be higher. The palette
    # renders "oltre 200" for this case; it never renders "200".
    totale_e_un_minimo: bool


class SearchResults(BaseModel):
    termine: str
    gruppi: list[SearchGroup]


# The three shapes people actually write a fiscal number in, and the order they are tried
# in is load-bearing: `2026/7` matches `_YEAR_FIRST` before `_NUMBER_FIRST` could read the
# `2026` as a number, and `7/2026` fails `_YEAR_FIRST` (whose first group needs four
# digits) and then matches `_NUMBER_FIRST`. Swapping the two would read `2026/7` as
# "invoice 2026 of year 7" and reject it on the year bound.
#
# `re.fullmatch` throughout, never `re.match` with `$`: `$` matches before a trailing
# newline, so `"7\n"` would parse as invoice 7 through a `$` anchor while carrying a
# character the user never typed. The term arrives from a query string, so that is not
# hypothetical.
_YEAR_FIRST = re.compile(r"(\d{4})[/\-](\d{1,6})")
_NUMBER_FIRST = re.compile(r"(\d{1,6})[/\-](\d{4})")
_BARE_NUMBER = re.compile(r"(\d{1,6})")

# An invoice number is at least 1 (slice 3's counter starts there) and no installation
# issues a million invoices in a year. The bound exists so that a long digit string -- a
# VAT number, a fiscal code fragment, an IBAN tail -- is treated as free text rather than
# as a number nobody has: without it, `01234567890` would be read as an invoice number and
# the branch would answer "nothing" instead of trigramming the causale.
_MIN_INVOICE_NUMBER = 1
_MAX_INVOICE_NUMBER = 999_999
# Wide enough to cover any register this product will meet and narrow enough that a
# four-digit fragment of ordinary text -- a price, a postcode -- is not mistaken for a year.
_MIN_YEAR = 2000
_MAX_YEAR = 2999


def _bounded(anno: int, numero: int) -> tuple[int, int] | None:
    if _MIN_YEAR <= anno <= _MAX_YEAR and _MIN_INVOICE_NUMBER <= numero <= _MAX_INVOICE_NUMBER:
        return anno, numero
    return None


def parse_fiscal_number(term: str) -> tuple[int | None, int] | None:
    """`(anno, numero)` when the term has the shape of a fiscal number, else `None`.

    `2026/7`, `7/2026` and `2026-7` are all how the same number gets written. A bare `007`
    returns `(None, 7)` and matches invoice 7 in **every** year: guessing the current year
    would hide last year's invoice 7 with nothing on screen to say so, which is a partial
    result presented as a complete one.

    When this returns a value the invoice branch matches by equality on `(anno, numero)` and
    does **not** also trigram the `causale`. The two paths are exclusive because `123`
    searched as a trigram means "every description containing 123", which is not an answer
    to "show me invoice 123". The cost of that exclusivity, stated rather than hidden: a
    causale containing a bare four-digit number is not reachable by typing that number
    alone. It is reachable by typing any of the words around it, and the alternative --
    running both paths -- makes every numeric search return the rows that merely mention the
    number beside the one that *is* it.

    A shape that looks like a number but falls outside the bounds is `None` and not an empty
    result: `01234567890` is a VAT number, `1999/7` is more likely a date range than an
    invoice, and both should reach the trigram path rather than answer "no such invoice".
    """
    candidate = term.strip()

    match = _YEAR_FIRST.fullmatch(candidate)
    if match:
        return _bounded(int(match.group(1)), int(match.group(2)))

    match = _NUMBER_FIRST.fullmatch(candidate)
    if match:
        return _bounded(int(match.group(2)), int(match.group(1)))

    match = _BARE_NUMBER.fullmatch(candidate)
    if match:
        numero = int(match.group(1))
        if _MIN_INVOICE_NUMBER <= numero <= _MAX_INVOICE_NUMBER:
            return None, numero
    return None
