"""Keyset pagination: an opaque cursor over `(sort value, id)`, newest-or-best-match
first or oldest first, never offset (REB-285, REB-313).

Modeled on PigroCRM's `pigrocrm.core.db.sort` (residuo R9) -- same opaque, one-way
encoding, same row-value keyset predicate -- reimplemented natively here because
`packages/core` may import neither adapter, and PigroCRM is neither
(`projects/hub/AGENTS.md`, "the one rule"). Narrower than the CRM's module on purpose:
every list this package paginates sorts by exactly one of two things, `created_at` (a
`datetime`, never null once a row exists) or a trigram similarity score (a `float`,
never null either -- `search.similarity_score` `coalesce`s it). A search term is
always best-match first; REB-285's Talents and Companies have no bare listing order
beside that, so both ever needed was descending. REB-313's admin list keeps reading
oldest first with no term (ORB-123, a history), so `keyset_predicate` takes a
`descending` flag beside the one sort key a request carries -- `SortSpec` itself still
carries only the `kind` a value needs to survive JSON, since the direction is a
property of the scan, not of the value.

**Offset pagination re-reads and skips rows under concurrent insertion**, which is why
this module exists at all -- an admin's list grows while they page through it.

**The cursor is opaque.** The pair `(sort value, id)` is JSON, base64url, unpadded, and
the client echoes it back without interpreting it -- an empty string in a query
parameter would otherwise be indistinguishable from other edge cases the decoder must
still refuse cleanly. The encoding also carries the sort key, so replaying a cursor
minted while searching against a plain `created_at` listing (or the reverse) is refused
rather than silently comparing a timestamp to a similarity score.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import ColumnElement, tuple_

from rebase_core.errors import ValidationFailed

SortKind = Literal["datetime", "float"]

# The longest value this module ever encodes is an ISO datetime (about 32 characters
# with a UTC offset) or a similarity score, plus a UUID and the JSON envelope around
# both -- comfortably under 200 bytes before base64. 512 leaves room for a longer sort
# key without ever approaching the size where a decoder becomes a place to spend CPU on
# an unbounded input.
CURSOR_MAX_LENGTH = 512

_ENTITY = "cursor"
_INVALID = "cursore non valido"


@dataclass(frozen=True)
class SortSpec:
    """The one sort key a listing is paginating by right now: `"created_at"` or
    `"score"`, and what a decoded value must be turned back into."""

    key: str
    kind: SortKind


def _encode_value(spec: SortSpec, value: object) -> str | float:
    if spec.kind == "datetime":
        if not isinstance(value, datetime):
            raise ValidationFailed(_ENTITY, "cursor", "valore di ordinamento non è una data")
        return value.isoformat()
    if not isinstance(value, int | float):
        raise ValidationFailed(_ENTITY, "cursor", "valore di ordinamento non è un numero")
    return float(value)


def _decode_value(spec: SortSpec, raw: object) -> datetime | float:
    if spec.kind == "datetime":
        if not isinstance(raw, str):
            raise ValidationFailed(_ENTITY, "cursor", f"{_INVALID}: attesa una data ISO 8601")
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationFailed(
                _ENTITY, "cursor", f"{_INVALID}: attesa una data ISO 8601"
            ) from exc
    if not isinstance(raw, int | float):
        raise ValidationFailed(_ENTITY, "cursor", f"{_INVALID}: atteso un numero")
    return float(raw)


def encode_cursor(spec: SortSpec, value: object, row_id: UUID) -> str:
    payload = {"k": spec.key, "v": _encode_value(spec, value), "i": str(row_id)}
    # `separators` without spaces, `sort_keys=True`: the encoding is a pure function of
    # its inputs, so two requests that land on the same last row produce byte-identical
    # cursors.
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def decode_cursor(spec: SortSpec, raw: str) -> tuple[datetime | float, UUID]:
    if not raw or len(raw) > CURSOR_MAX_LENGTH:
        raise ValidationFailed(
            _ENTITY,
            "cursor",
            f"{_INVALID}: attesa una stringa opaca di al massimo {CURSOR_MAX_LENGTH} caratteri",
        )
    padded = raw + "=" * (-len(raw) % 4)
    try:
        body = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(body)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailed(_ENTITY, "cursor", _INVALID) from exc
    if not isinstance(payload, dict) or set(payload) != {"k", "v", "i"}:
        raise ValidationFailed(_ENTITY, "cursor", _INVALID)
    if payload["k"] != spec.key:
        raise ValidationFailed(
            _ENTITY,
            "cursor",
            f"il cursore appartiene a un altro ordinamento, atteso sort={spec.key}",
        )
    identifier = payload["i"]
    # `isinstance` before `UUID(...)`: `UUID(None)` raises `TypeError`, and catching
    # that alongside `ValueError` would also swallow a genuine bug in this function.
    if not isinstance(identifier, str):
        raise ValidationFailed(_ENTITY, "cursor", f"{_INVALID}: atteso un UUID")
    try:
        row_id = UUID(identifier)
    except ValueError as exc:
        raise ValidationFailed(_ENTITY, "cursor", f"{_INVALID}: atteso un UUID") from exc
    return _decode_value(spec, payload["v"]), row_id


def keyset_predicate(
    sort_column: Any,
    id_column: Any,
    value: object,
    row_id: UUID,
    *,
    descending: bool = True,
) -> ColumnElement[bool]:
    """Everything strictly after `(value, row_id)` in a `(sort, id)` scan -- descending
    by default, ascending when `descending=False` (REB-313's admins list, read oldest
    first).

    `(col, id) < (v, rid)` (or `>` ascending) is the row-value form of
    `col < v OR (col = v AND id < rid)` -- the same set of rows, but a single indexable
    condition rather than a disjunction. Neither sort key is ever null once a row is in
    the result at all (an unmatched search row never reaches this point; a listed row
    always has its sort column), so there is no null tail to reopen with an
    `OR column.is_(None)` arm the way PigroCRM's general `keyset_predicate` needs for a
    nullable sort column -- see its `db/sort.py` for that case."""
    left = tuple_(sort_column, id_column)
    right = (value, row_id)
    return left < right if descending else left > right
