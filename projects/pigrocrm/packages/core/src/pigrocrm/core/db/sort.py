"""Ordering and keyset pagination for the four entities residuo R9 covers.

R9: slice 1 §7 promised "paginazione cursor-based, ordinamento e filtri" and every
shipped `list()` orders by `id` alone -- which with UUIDv7 means creation order. This
module is the missing half.

Three decisions, and each one is load-bearing.

**Keyset, not offset.** Offset pagination re-reads and skips rows under concurrent
insertion. That is why slice 1 chose keyset, and it does not stop being true because the
sort column changed.

**The cursor is opaque.** A keyset over a non-unique column needs the pair
`(sort value, id)`, and the sort value can be null (`people.cognome`). An empty string in
a query parameter is indistinguishable from a null, and rows are lost on exactly that
distinction -- so the pair is JSON, base64url, unpadded, and the client echoes it back
without interpreting it. The encoding also carries the sort key, so replaying a cursor
against a different `sort` is refused rather than silently comparing a surname to a
timestamp.

**`NULLS LAST` only where a null is possible, tie-break in the direction of travel.**
`ORDER BY col <dir>, id <dir>`, with `NULLS LAST` spelled out on the one nullable column
and on no other. The tie-break following the direction is what lets one ascending
`(col, id)` index serve `desc` as a backward scan -- but only if nothing else in the
ordering contradicts the index, and `NULLS LAST` on a `NOT NULL` column does exactly that.

Postgres matches ordering pathkeys *including* nulls placement, and it does not consult
`NOT NULL` to reconcile two spellings that can only describe the same rows. A backward scan
of an ascending index yields `DESC NULLS FIRST`; asking for `DESC NULLS LAST` therefore
matches no index at all, and the planner sequentially scans the table and sorts it --
measured on `postgres:17-alpine` at 50 000 rows, with `ORDER BY ragione_sociale DESC, id
DESC` over the same rows taking `Index Scan Backward using ix_customers_ragione_sociale_id`.
On a column that cannot be null the two orderings are identical by construction, because
there are no nulls to place; so `SortSpec.nullable` decides whether `nulls_last()` is
emitted, and that is the only thing it decides.

`people.cognome` is the one column where the null tail is real, so it keeps
`DESC NULLS LAST` and pays for it with a second index `(cognome DESC NULLS LAST, id DESC)`.
This is why the whitelist is short: every admitted column costs an index, and a nullable one
costs two.

`test_sort_plan.py` asserted all of this on the executed plan over a twenty-thousand-row
corpus until REB-580 removed it: a space's tables hold hundreds of rows, where the planner
sorts in memory whatever the index says, so the assertion measured nothing a user meets.
`test_migrations.py` asserts the indexes are *created* and `test_sort_cursor.py` asserts the
rows come back in the declared *order*; the rule above is what keeps the indexes usable for
a tenant that outgrows that.

The whitelist is an exact string comparison and deliberately not a regular expression.
`re.fullmatch` would be correct where `re.match(r"^...$")` is not -- Python's `$` matches
before a trailing newline -- but the safest form of that rule is not to reach for a
pattern at all when the admissible set is four literals long. A sort key ends up inside
`ORDER BY`; there is nothing to be gained by admitting anything the declaration did not
name.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import ColumnElement, UnaryExpression, and_, or_, tuple_
from sqlalchemy.orm.attributes import InstrumentedAttribute

from pigrocrm.core.errors import ValidationFailed

SortDirection = Literal["asc", "desc"]
# `date` joined the two originals for `attivita.scadenza` (slice 10). It is a kind of
# its own and not `text`, because the cursor's value becomes a bound parameter compared
# against the column: a `Date` column against a text parameter is `date > text`, an
# operator Postgres does not have, so the keyset seek would fail at the database rather
# than at the decoder. The `datetime` branch cannot serve it either --
# `datetime.fromisoformat("2026-09-09")` answers midnight, and a `Date` column compared
# against a timestamp is a comparison across two types that only accidentally agrees.
SortKind = Literal["text", "date", "datetime"]

# Bounded for the same reason `limit` is (Global Constraints): an unbounded parameter
# reaching a decoder is a denial of service with extra steps. The value is derived, not
# guessed -- the brief's 512 was a guess and it was wrong by a factor of four, which the
# test named after this constant caught.
#
# The longest whitelisted sort column is `customers.ragione_sociale`, String(255). The
# worst case per character is a control character, which JSON must escape to `\uXXXX`
# (6 bytes); 255 * 6 = 1530, plus roughly 80 bytes of envelope (`{"i":"<uuid>","k":
# "<key>","v":""}`) is about 1610 bytes, which base64 expands to about 2148 characters.
# 4096 clears that with room for a longer sort key, and is still small enough that a
# megabyte of query string is refused before anything decodes it.
#
# A bound below the longest cursor the encoder can *produce* would be worse than no bound
# at all: it would make the last page of an ordered scan unreachable, and only for
# customers with long accented names.
CURSOR_MAX_LENGTH = 4096

_ENTITY = "cursor"


@dataclass(frozen=True)
class SortSpec:
    """One admissible sort column.

    `kind` exists because the cursor is JSON and JSON has no datetime: the decoder needs
    to be told how to read the value back. Inferring it from the column type is possible
    and rejected -- it would put an `isinstance` ladder over SQLAlchemy type objects in
    the hot path of every list request, to answer a question the declaration already
    knows.
    """

    key: str
    column: InstrumentedAttribute[Any]
    kind: SortKind
    nullable: bool


@dataclass(frozen=True)
class SortWhitelist:
    specs: tuple[SortSpec, ...]
    default_key: str

    def keys(self) -> tuple[str, ...]:
        return tuple(spec.key for spec in self.specs)

    def resolve(self, key: str | None) -> SortSpec:
        wanted = key if key is not None else self.default_key
        for spec in self.specs:
            if spec.key == wanted:
                return spec
        raise ValidationFailed(
            "list_query",
            "sort",
            "ordinamento non ammesso",
            expected=", ".join(self.keys()),
        )


def _identity(spec: SortSpec) -> InstrumentedAttribute[UUID]:
    """The mapped `id` of whatever entity `spec.column` belongs to.

    Every model in this schema carries `PrimaryKeyMixin`, so the attribute always exists;
    the `cast` is what tells mypy that, since `Mapper.class_` is `type[Any]`.
    """
    return cast(InstrumentedAttribute[UUID], spec.column.parent.class_.id)


def _encode_value(spec: SortSpec, value: object) -> object:
    if value is None:
        return None
    if spec.kind == "datetime":
        if not isinstance(value, datetime):
            raise ValidationFailed(
                _ENTITY,
                "cursor",
                "valore di ordinamento non è una data",
                expected="datetime",
            )
        return value.isoformat()
    if spec.kind == "date":
        # `datetime` first, and this order matters: `datetime` is a subclass of `date`,
        # so an `isinstance(value, date)` check here would accept a timestamp and encode
        # it with `date.isoformat()`, silently dropping the time and producing a cursor
        # that seeks to midnight.
        if isinstance(value, datetime) or not isinstance(value, date):
            raise ValidationFailed(
                _ENTITY,
                "cursor",
                "valore di ordinamento non è una data",
                expected="date",
            )
        return value.isoformat()
    return str(value)


def _decode_value(spec: SortSpec, raw: object) -> object | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValidationFailed(_ENTITY, "cursor", "cursore non valido", expected="stringa")
    if spec.kind == "datetime":
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationFailed(
                _ENTITY, "cursor", "cursore non valido", expected="data ISO 8601"
            ) from exc
    if spec.kind == "date":
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationFailed(
                _ENTITY, "cursor", "cursore non valido", expected="data AAAA-MM-GG"
            ) from exc
    return raw


def encode_cursor(spec: SortSpec, value: object, row_id: UUID) -> str:
    payload = {"k": spec.key, "v": _encode_value(spec, value), "i": str(row_id)}
    # `separators` without spaces, `sort_keys=True`: the encoding must be a pure function
    # of its inputs, because criterion 4 asserts byte-identical responses across runs.
    #
    # `ensure_ascii=False` because the whole thing is base64-encoded immediately after,
    # so there is nothing to protect from a non-ASCII byte -- and the default would spend
    # six characters on every accented letter of an Italian company name, twelve on
    # anything outside the BMP. That is a three-fold difference in the length of every
    # cursor over a `ragione_sociale`, paid on every page of every ordered scan.
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def decode_cursor(spec: SortSpec, raw: str) -> tuple[object | None, UUID]:
    if not raw or len(raw) > CURSOR_MAX_LENGTH:
        raise ValidationFailed(
            _ENTITY,
            "cursor",
            "cursore non valido",
            expected=f"stringa opaca di al massimo {CURSOR_MAX_LENGTH} caratteri",
        )
    padded = raw + "=" * (-len(raw) % 4)
    try:
        body = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(body)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailed(
            _ENTITY, "cursor", "cursore non valido", expected="cursore restituito dall'API"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"k", "v", "i"}:
        raise ValidationFailed(
            _ENTITY, "cursor", "cursore non valido", expected="cursore restituito dall'API"
        )
    if payload["k"] != spec.key:
        raise ValidationFailed(
            _ENTITY,
            "cursor",
            "il cursore appartiene a un altro ordinamento",
            expected=f"un cursore prodotto con sort={spec.key}",
        )
    # `isinstance` before `UUID(...)`: `UUID(None)` raises `TypeError` and `UUID(17)`
    # raises `AttributeError`, and catching those alongside `ValueError` would also
    # swallow a genuine bug in this function.
    identifier = payload["i"]
    if not isinstance(identifier, str):
        raise ValidationFailed(_ENTITY, "cursor", "cursore non valido", expected="UUID")
    try:
        row_id = UUID(identifier)
    except ValueError as exc:
        raise ValidationFailed(_ENTITY, "cursor", "cursore non valido", expected="UUID") from exc
    return _decode_value(spec, payload["v"]), row_id


def order_by(spec: SortSpec, direction: SortDirection) -> tuple[UnaryExpression[Any], ...]:
    """`col <dir>, id <dir>`, with `NULLS LAST` only when the column admits a null.

    Both clauses carry the same direction, which is what makes one ascending `(col, id)`
    index enough to serve both directions of a non-nullable column. `nulls_last()` on such a
    column would undo that: it is semantically a no-op -- there are no nulls to place -- but
    the planner matches nulls placement as part of the ordering pathkey and will not use
    `NOT NULL` to reconcile the two, so the whole ordering silently stops matching the index.
    See the module docstring for the measurement.

    A nullable column is the opposite case: there `NULLS LAST` is a real requirement in both
    directions, and it is why `people.cognome` carries a descending index of its own.
    """
    column, identity = spec.column, _identity(spec)
    ordered = column.asc() if direction == "asc" else column.desc()
    if spec.nullable:
        ordered = ordered.nulls_last()
    return (ordered, identity.asc() if direction == "asc" else identity.desc())


def keyset_predicate(
    spec: SortSpec,
    direction: SortDirection,
    value: object | None,
    row_id: UUID,
) -> ColumnElement[bool]:
    """Everything strictly after `(value, row_id)` in `order_by(spec, direction)`.

    **A row-value comparison where the column cannot be null.** `(col, id) > (v, rid)` is
    the same set of rows as `col > v OR (col = v AND id > rid)`, and the two are not the
    same query: the disjunction is not an indexable condition, so Postgres applies it as a
    `Filter` over a scan that starts at the beginning of the index, and page *N* reads and
    discards the `(N-1) x limit` entries of the pages before it -- the asymptotic cost of
    the `OFFSET` pagination this module exists to avoid. The row-value form becomes a single
    `Index Cond` and the scan starts where the previous page stopped.

    **The nullable column keeps the disjunction, and must.** A row comparison with a NULL
    member evaluates to NULL, so `(cognome, id) > (v, rid)` is never true for a person
    without a surname and the entire null tail vanishes from a scan that ordered it last.
    The `column.is_(None)` arm is the one that gets forgotten: without it, paging from a
    non-null value stops at the last non-null row and the null tail is never returned at
    all -- rows silently missing from a complete scan, which is precisely the failure
    keyset pagination was chosen to avoid. It is correct in *both* directions because
    `order_by` puts nulls last in both for a nullable column: the null tail always comes
    after every value.
    """
    column, identity = spec.column, _identity(spec)
    if value is None:
        # Already inside the null tail, which is ordered by `id` alone. Reachable on a
        # non-nullable column only through a forged cursor, where it correctly matches
        # nothing rather than raising.
        after_id = identity > row_id if direction == "asc" else identity < row_id
        return and_(column.is_(None), after_id)

    if not spec.nullable:
        # The right-hand side is a plain Python tuple, not a second `tuple_()`: SQLAlchemy's
        # `Tuple` binds it through the types of the columns on the left, so the datetime keys
        # arrive as `timestamptz` and `id` as `uuid` with no cast written here.
        pair = tuple_(column, identity)
        return pair > (value, row_id) if direction == "asc" else pair < (value, row_id)

    strictly_after = column > value if direction == "asc" else column < value
    same_value_after_id = and_(
        column == value,
        identity > row_id if direction == "asc" else identity < row_id,
    )
    return or_(strictly_after, same_value_after_id, column.is_(None))
