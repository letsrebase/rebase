"""The dedup key of a movement (spec 2026-11-09, due items, § 2).

With the bank's reference it is the reference alone: the same movement in two statements is
kept once. Without one it is the date, the amount, a hash of the description and the
occurrence index within the file, so two genuine identical same-day payments (indices 0 and
1) are both kept, while the same movement in two overlapping files (same index) is kept once.
The key is unique per account, never across accounts, so it carries no account id.
"""

import datetime as dt
import hashlib
from collections.abc import Iterable, Iterator
from decimal import Decimal

_SEP = "\x1f"


def _digest(*parts: str) -> str:
    return hashlib.sha256(_SEP.join(parts).encode()).hexdigest()


def normalise_description(value: str | None) -> str:
    """Whitespace collapsed and case folded, so a re-export that re-wraps a description is
    still the same movement."""
    return " ".join((value or "").split()).casefold()


def reference_key(reference: str) -> str:
    return _digest("ref", reference.strip())


def fallback_key(
    booking_date: dt.date, amount: Decimal, description: str | None, occurrence: int
) -> str:
    description_hash = _digest(normalise_description(description))
    return _digest(
        "fallback", booking_date.isoformat(), f"{amount:.2f}", description_hash, str(occurrence)
    )


def keys_for_file(
    rows: Iterable[tuple[dt.date, Decimal, str | None, str | None]],
) -> Iterator[str]:
    """The key of each row of one file, in order: `(booking_date, amount, description,
    bank_reference)`. The occurrence index counts the earlier rows of the same file with the
    same date, amount and description, and only rows without a reference count."""
    seen: dict[tuple[dt.date, str, str], int] = {}
    for booking_date, amount, description, reference in rows:
        if reference is not None and reference.strip():
            yield reference_key(reference)
            continue
        identity = (booking_date, f"{amount:.2f}", normalise_description(description))
        occurrence = seen.get(identity, 0)
        seen[identity] = occurrence + 1
        yield fallback_key(booking_date, amount, description, occurrence)
