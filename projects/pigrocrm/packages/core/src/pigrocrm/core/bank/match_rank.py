"""What makes a movement look like the payment of a due item, as pure functions (spec 2026-11-09,
§ 3; REB-1042): the reasons it is ranked by and the arithmetic of a split. Nothing here reads the
database or settles anything; `matching.py` feeds it rows and decides what to do with the answer.

Four pieces of evidence, each a reason code on a candidate:

* `exact_amount`: the item's remaining amount is the movement's, to the cent, in the same
  currency and direction (a single item) or the items' remaining amounts add up to it
  (`items_total`, for a split);
* `document_number`: the number on the item's document (an invoice's `2026/35`, a supplier's own
  number) is in the bank's text: the description, the bank reference or the `EndToEndId`;
* `known_iban`: the movement's counterparty IBAN is one a person accepted for the item's
  organisation. It **ranks** and never decides: a known IBAN alone settles nothing (§ 3);
* `counterparty_name`: the movement's counterparty (or its description) names the organisation.

The bank's text is free text written by others: it is only ever compared here, never obeyed.
"""

import datetime as dt
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from uuid import UUID

EXACT_AMOUNT = "exact_amount"
ITEMS_TOTAL = "items_total"
DOCUMENT_NUMBER = "document_number"
KNOWN_IBAN = "known_iban"
COUNTERPARTY_NAME = "counterparty_name"
ALREADY_MARKED_PAID = "already_marked_paid"

# What a reason weighs when candidates are ranked: a document number in the bank's text is the
# strongest sign, then the amount, then who paid.
WEIGHTS: dict[str, int] = {
    DOCUMENT_NUMBER: 5,
    EXACT_AMOUNT: 4,
    ITEMS_TOTAL: 4,
    KNOWN_IBAN: 3,
    ALREADY_MARKED_PAID: 3,
    COUNTERPARTY_NAME: 2,
}
# The reasons that say who paid or what for, as opposed to how much: a proposal the importer
# writes on its own needs one of them besides the amount.
EVIDENCE: frozenset[str] = frozenset({DOCUMENT_NUMBER, KNOWN_IBAN, COUNTERPARTY_NAME})

# The most items a split is searched over, and the most it combines: a payment that covers
# more is not proposed, a person picks it.
SPLIT_POOL_MAX = 12
SPLIT_SIZE_MAX = 4

# The words of a company's legal form say nothing about who it is.
_LEGAL_FORMS = frozenset(
    {
        "srl",
        "srls",
        "spa",
        "snc",
        "sas",
        "sapa",
        "scarl",
        "ss",
        "soc",
        "societa",
        "ltd",
        "limited",
        "gmbh",
        "inc",
        "llc",
        "plc",
        "sa",
        "bv",
        "ag",
        "di",
        "e",
        "the",
    }
)
_WORD = re.compile(r"[a-z0-9]+")
_MIN_NAME_LETTERS = 4
_MIN_NUMBER_LENGTH = 3


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def name_tokens(text: str | None) -> frozenset[str]:
    """The words that identify a name: lowercase, accents and punctuation gone, the legal form
    (`s.r.l.`, `spa`) dropped. «Alfa Servizi S.r.l.» and «ALFA SERVIZI» read the same."""
    if not text:
        return frozenset()
    words = _WORD.findall(_ascii(text).replace(".", ""))
    return frozenset(word for word in words if word not in _LEGAL_FORMS and len(word) > 1)


def names_match(bank_text: str | None, organisation_name: str | None) -> bool:
    """Whether the bank's text names the organisation: every identifying word of the shorter
    name is in the longer one, and together they are not a stray syllable. Used on the
    movement's counterparty and on its description, so «Bonifico a favore di Alfa Servizi»
    matches «Alfa Servizi S.r.l.»."""
    bank, name = name_tokens(bank_text), name_tokens(organisation_name)
    if not bank or not name:
        return False
    smaller, larger = (bank, name) if len(bank) <= len(name) else (name, bank)
    return smaller <= larger and sum(len(word) for word in smaller) >= _MIN_NAME_LETTERS


def name_in_text(bank_text: str | None, organisation_name: str | None) -> bool:
    """Whether every identifying word of the organisation's name is in the bank's text. Unlike
    `names_match` it is one way: a description that is only «alfa» does not name «Alfa
    Servizi»."""
    text, name = name_tokens(bank_text), name_tokens(organisation_name)
    return bool(name) and name <= text and sum(len(word) for word in name) >= _MIN_NAME_LETTERS


def invoice_number(year: int, number: int) -> str:
    """How an invoice's number is read on a screen and written on a transfer: `2026/35`."""
    return f"{year}/{number}"


def invoice_reference(year: int, number: int) -> re.Pattern[str]:
    """The ways a payer writes invoice 2026/35 in a description: `2026/35`, `35/2026`, with a
    slash, a dash or a dot and leading zeros, and never inside a longer number."""
    separator = r"\s*[/\-.]\s*"
    return re.compile(
        rf"(?<![0-9]){year}{separator}0*{number}(?![0-9])"
        rf"|(?<![0-9])0*{number}{separator}{year}(?![0-9])"
    )


def text_mentions(bank_text: str, document_number: str | None) -> bool:
    """Whether a document's own number (a supplier's `FT-1042/A`) is in the bank's text. The
    comparison ignores case and punctuation, and a number shorter than three characters is not
    evidence: «1» is in every description."""
    if not document_number:
        return False
    needle = "".join(_WORD.findall(_ascii(document_number)))
    if len(needle) < _MIN_NUMBER_LENGTH:
        return False
    haystack = "".join(_WORD.findall(_ascii(bank_text)))
    return needle in haystack


@dataclass(frozen=True)
class Fit:
    """One item as a movement sees it, ready to be ranked or allocated. `remaining` is a
    magnitude, what is still owed (zero for an item settled in full); `adoptable` says it holds a
    payment marked by hand for the movement's amount, which the movement may adopt."""

    item_id: UUID
    remaining: Decimal
    due_date: dt.date | None
    organization_id: UUID | None
    has_number: bool
    has_iban: bool
    has_name: bool
    adoptable: bool = False


def item_reasons(fit: Fit) -> set[str]:
    reasons: set[str] = set()
    if fit.has_number:
        reasons.add(DOCUMENT_NUMBER)
    if fit.has_iban:
        reasons.add(KNOWN_IBAN)
    if fit.has_name:
        reasons.add(COUNTERPARTY_NAME)
    return reasons


def selection_reasons(fits: Sequence[Fit], magnitude: Decimal, allocated: Decimal) -> list[str]:
    """The reasons a selection holds as a whole: the amount (`exact_amount` for one item paid
    in full, `items_total` for several that add up to the movement), and each piece of evidence
    only when it holds for every item. Ordered by weight."""
    if not fits:
        return []
    reasons: set[str] = set()
    if allocated == magnitude:
        if len(fits) == 1 and not fits[0].adoptable and fits[0].remaining == magnitude:
            reasons.add(EXACT_AMOUNT)
        elif len(fits) > 1 and all(0 < fit.remaining <= magnitude for fit in fits):
            reasons.add(ITEMS_TOTAL)
    if all(fit.adoptable for fit in fits):
        reasons.add(ALREADY_MARKED_PAID)
    shared = set.intersection(*(item_reasons(fit) for fit in fits))
    reasons |= shared
    return sorted(reasons, key=lambda reason: (-WEIGHTS[reason], reason))


def score(reasons: Iterable[str]) -> int:
    return sum(WEIGHTS[reason] for reason in reasons)


def split_combinations(fits: Sequence[Fit], magnitude: Decimal) -> list[tuple[Fit, ...]]:
    """The sets of two to four items whose remaining amounts add up to the movement, to the
    cent, searched over at most `SPLIT_POOL_MAX` items, the earliest due first. The caller
    passes the items of one counterparty: a payment that settles two invoices of the same
    customer is the case §3 names, and the sum of strangers' invoices is a coincidence."""
    pool = sorted(
        (fit for fit in fits if 0 < fit.remaining < magnitude),
        key=lambda fit: (fit.due_date is None, fit.due_date or dt.date.max, str(fit.item_id)),
    )[:SPLIT_POOL_MAX]
    found: list[tuple[Fit, ...]] = []
    for size in range(2, SPLIT_SIZE_MAX + 1):
        for chosen in combinations(pool, size):
            if sum((fit.remaining for fit in chosen), Decimal("0.00")) == magnitude:
                found.append(chosen)
    return found
