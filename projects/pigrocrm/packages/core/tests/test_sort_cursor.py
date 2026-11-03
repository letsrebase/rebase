"""Residuo R9's machinery, tested before anything uses it.

R9 measures that no `list()` in either adapter orders by anything but `id`, while slice 1
§7 promised ordering. The fix keeps keyset pagination -- offset pagination re-reads and
skips rows under concurrent insertion, which is why slice 1 chose keyset -- so ordering by
a non-unique column needs a *composite* cursor. That makes the cursor opaque, and opacity
is also what lets it represent a null: an empty string in a query parameter is
indistinguishable from a null, and rows are lost on exactly that distinction.
"""

import base64
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import (
    CURSOR_MAX_LENGTH,
    SortSpec,
    SortWhitelist,
    decode_cursor,
    encode_cursor,
    keyset_predicate,
    order_by,
)
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.people.models import Person

_TEXT = SortSpec(key="legal_name", column=Customer.legal_name, kind="text", nullable=False)
_STAMP = SortSpec(key="created_at", column=Customer.created_at, kind="datetime", nullable=False)
_NULLABLE = SortSpec(key="last_name", column=Person.last_name, kind="text", nullable=True)


def _forge(payload: object) -> str:
    """A cursor built by hand, the way a hostile or stale client would send one."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


def test_a_text_cursor_round_trips() -> None:
    row_id = uuid7()
    raw = encode_cursor(_TEXT, "Rossi Ingegneria Srl", row_id)
    assert decode_cursor(_TEXT, raw) == ("Rossi Ingegneria Srl", row_id)


def test_a_datetime_cursor_round_trips_with_its_timezone() -> None:
    row_id = uuid7()
    moment = datetime(2026, 8, 21, 14, 30, 5, 123456, tzinfo=UTC)
    raw = encode_cursor(_STAMP, moment, row_id)
    assert decode_cursor(_STAMP, raw) == (moment, row_id)


def test_a_null_cursor_round_trips_and_is_not_an_empty_string() -> None:
    """The distinction the opacity exists for."""
    row_id = uuid7()
    raw_null = encode_cursor(_NULLABLE, None, row_id)
    raw_empty = encode_cursor(_NULLABLE, "", row_id)
    assert raw_null != raw_empty
    assert decode_cursor(_NULLABLE, raw_null) == (None, row_id)
    assert decode_cursor(_NULLABLE, raw_empty) == ("", row_id)


def test_a_cursor_is_url_safe_and_unpadded() -> None:
    raw = encode_cursor(_TEXT, 'a/b+c=d "e" &f?', uuid7())
    assert "=" not in raw
    assert "/" not in raw
    assert "+" not in raw


@pytest.mark.parametrize("character", ["à", "\U0001f600", "", '"', "\\", "x"])
def test_the_longest_value_the_column_admits_still_fits_the_cursor_bound(
    character: str,
) -> None:
    """`CURSOR_MAX_LENGTH` is a rejection threshold as well as a documented size, so a
    legitimate cursor must not be able to exceed it. `customers.legal_name` is
    String(255) and is the longest whitelisted sort column in the slice; a bound that
    refused its own output would make the last page of an ordered scan unreachable, and
    only for customers whose names contain the expensive characters.

    This is what caught the brief's `CURSOR_MAX_LENGTH = 512`: 255 accented characters
    encode to a 2138-character cursor under `json.dumps`'s default `ensure_ascii=True`,
    four times the stated bound. Every character class that costs more than one byte is
    parametrised here -- accented (2 bytes of UTF-8), astral (4), a control character
    (escaped to `\\uXXXX`, 6) and the two JSON must escape (`"` and `\\`, 2 each).
    """
    longest = character * 255
    raw = encode_cursor(_TEXT, longest, uuid7())
    assert len(raw) <= CURSOR_MAX_LENGTH, len(raw)
    assert decode_cursor(_TEXT, raw)[0] == longest


def test_encoding_is_a_pure_function_of_its_inputs() -> None:
    """Criterion 4 asserts byte-identical responses across runs, and `next_cursor` is in
    the response. A dict whose key order followed insertion would break that silently."""
    row_id = uuid7()
    assert encode_cursor(_TEXT, "Rossi", row_id) == encode_cursor(_TEXT, "Rossi", row_id)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not-base64!!",
        "eyJ2IjogMX0",  # {"v": 1} -- no id and no sort key at all
        "x" * (CURSOR_MAX_LENGTH + 1),
    ],
)
def test_a_malformed_cursor_is_a_domain_error_not_a_crash(raw: str) -> None:
    """A cursor arrives from a query string, so a hostile or stale one is ordinary input.
    It must produce a 422 with a named field, never a `binascii.Error` or a `KeyError`
    escaping as a 500."""
    with pytest.raises(ValidationFailed) as caught:
        decode_cursor(_TEXT, raw)
    assert caught.value.details["field"] == "cursor"


@pytest.mark.parametrize(
    "payload",
    [
        {"k": "legal_name", "v": "Rossi", "i": "not-a-uuid"},
        {"k": "legal_name", "v": "Rossi", "i": None},
        {"k": "legal_name", "v": "Rossi"},
        {"k": "legal_name", "v": "Rossi", "i": str(uuid7()), "extra": 1},
        {"k": "legal_name", "v": 17, "i": str(uuid7())},
        ["legal_name", "Rossi"],
        "just a string",
    ],
)
def test_a_well_formed_base64_carrying_the_wrong_shape_is_also_refused(payload: object) -> None:
    """The brief's own malformed cases all died at the base64 or the key-set check, so
    the UUID branch and the value-type branch had no exerciser at all. These reach them:
    every payload here is valid base64 of valid JSON.
    """
    with pytest.raises(ValidationFailed) as caught:
        decode_cursor(_TEXT, _forge(payload))
    assert caught.value.details["field"] == "cursor"


def test_a_datetime_cursor_carrying_something_that_is_not_a_date_is_refused() -> None:
    with pytest.raises(ValidationFailed) as caught:
        decode_cursor(_STAMP, _forge({"k": "created_at", "v": "ieri", "i": str(uuid7())}))
    assert caught.value.details["field"] == "cursor"


def test_a_datetime_cursor_without_an_offset_is_refused() -> None:
    """The encoder always writes one: a naive value is forged, and against a `timestamptz`
    column it would be read in the session's zone."""
    with pytest.raises(ValidationFailed) as caught:
        naive = {"k": "created_at", "v": "2026-08-21T14:30:05", "i": str(uuid7())}
        decode_cursor(_STAMP, _forge(naive))
    assert caught.value.details["field"] == "cursor"


def test_a_cursor_encoded_for_one_sort_key_is_refused_by_another() -> None:
    """Changing `sort` mid-scan while replaying `next_cursor` would otherwise compare a
    surname against a timestamp and return an arbitrary page."""
    raw = encode_cursor(_TEXT, "Rossi", uuid7())
    with pytest.raises(ValidationFailed) as caught:
        decode_cursor(_STAMP, raw)
    assert caught.value.details["field"] == "cursor"


def test_the_whitelist_refuses_a_key_it_does_not_contain() -> None:
    whitelist = SortWhitelist(specs=(_TEXT, _STAMP), default_key="created_at")
    assert whitelist.keys() == ("legal_name", "created_at")
    assert whitelist.resolve(None).key == "created_at"
    assert whitelist.resolve("legal_name").key == "legal_name"
    with pytest.raises(ValidationFailed) as caught:
        whitelist.resolve("note; DROP TABLE customers")
    assert caught.value.details["field"] == "sort"


@pytest.mark.parametrize(
    "key",
    ["ragione_sociale\n", "\nragione_sociale", " ragione_sociale", "RAGIONE_SOCIALE", ""],
)
def test_the_whitelist_admits_the_key_and_nothing_around_it(key: str) -> None:
    """The house rule about `re.fullmatch` over `re.match` with `$`, in the place it
    would bite hardest. This whitelist is an exact string comparison rather than a
    pattern, which is stronger still -- and this test is what keeps it that way, because
    the natural "improvement" here is a regex, and `^legal_name$` accepts
    "ragione_sociale\\n". A key that reaches `ORDER BY` with a newline welded to it is a
    whitelist that is not one.
    """
    whitelist = SortWhitelist(specs=(_TEXT, _STAMP), default_key="created_at")
    with pytest.raises(ValidationFailed) as caught:
        whitelist.resolve(key)
    assert caught.value.details["field"] == "sort"


def test_order_by_puts_nulls_last_in_both_directions(db_session: Session) -> None:
    """`people.last_name` is nullable. Nulls last ascending is Postgres's default; nulls
    last *descending* is not, and getting it by accident is how the null tail ends up at
    the top of page one with no cursor value to resume from."""
    for last_name in ("Bianchi", None, "Rossi"):
        db_session.add(Person(nome="Marco", last_name=last_name, custom_fields={}))
    db_session.flush()

    ascending = db_session.scalars(
        select(Person.last_name).order_by(*order_by(_NULLABLE, "asc"))
    ).all()
    descending = db_session.scalars(
        select(Person.last_name).order_by(*order_by(_NULLABLE, "desc"))
    ).all()

    assert list(ascending) == ["Bianchi", "Rossi", None]
    assert list(descending) == ["Rossi", "Bianchi", None]


def test_order_by_breaks_ties_in_the_direction_of_travel(db_session: Session) -> None:
    """`ORDER BY col <dir>, id <dir>` and not `id ASC` in both -- the tie-break following
    the direction is exactly what lets a single ascending `(col, id)` index serve `desc`
    as a backward scan, and it is the reason the whitelist can be as short as it is.
    """
    same_surname = [Person(nome="A", last_name="Rossi", custom_fields={}) for _ in range(3)]
    for person in same_surname:
        db_session.add(person)
    db_session.flush()
    by_creation = sorted(person.id for person in same_surname)

    ascending = db_session.scalars(select(Person.id).order_by(*order_by(_NULLABLE, "asc"))).all()
    descending = db_session.scalars(select(Person.id).order_by(*order_by(_NULLABLE, "desc"))).all()

    assert list(ascending) == by_creation
    assert list(descending) == list(reversed(by_creation))


def test_the_keyset_predicate_resumes_exactly_after_the_cursor_row(db_session: Session) -> None:
    rows = [Person(nome="A", last_name=c, custom_fields={}) for c in ("B", "C", None, None)]
    for row in rows:
        db_session.add(row)
    db_session.flush()

    ordered = db_session.scalars(select(Person).order_by(*order_by(_NULLABLE, "asc"))).all()
    third = ordered[2]  # the first of the two nulls

    after = db_session.scalars(
        select(Person.id)
        .where(keyset_predicate(_NULLABLE, "asc", third.last_name, third.id))
        .order_by(*order_by(_NULLABLE, "asc"))
    ).all()

    assert list(after) == [ordered[3].id]


def test_the_keyset_predicate_from_a_non_null_value_still_reaches_the_null_tail(
    db_session: Session,
) -> None:
    """The clause people forget. Without `OR col IS NULL`, paging ascending stops at the
    last non-null row and the null tail is never returned at all."""
    for last_name in ("B", None):
        db_session.add(Person(nome="A", last_name=last_name, custom_fields={}))
    db_session.flush()

    first = db_session.scalars(select(Person).order_by(*order_by(_NULLABLE, "asc"))).all()[0]

    after = db_session.scalars(
        select(Person.last_name)
        .where(keyset_predicate(_NULLABLE, "asc", first.last_name, first.id))
        .order_by(*order_by(_NULLABLE, "asc"))
    ).all()

    assert list(after) == [None]


def test_the_keyset_predicate_never_repeats_and_never_skips_a_row(db_session: Session) -> None:
    """The property the whole module exists for, walked end to end in both directions.

    Page size two, over a set with duplicate surnames and a null tail, until exhaustion.
    Every row must appear exactly once and in the declared order -- the two failures a
    keyset predicate produces are a repeated boundary row (`>=` where `>` was meant) and
    a skipped one (a missing `id` tie-break), and neither shows up in a single-page test.
    """
    surnames = ("Rossi", "Bianchi", "Rossi", None, "Verdi", None, "Bianchi")
    for last_name in surnames:
        db_session.add(Person(nome="A", last_name=last_name, custom_fields={}))
    db_session.flush()

    for direction in ("asc", "desc"):
        expected = list(
            db_session.scalars(select(Person.id).order_by(*order_by(_NULLABLE, direction))).all()
        )

        walked: list[object] = []
        cursor: tuple[object, object] | None = None
        while True:
            stmt = select(Person).order_by(*order_by(_NULLABLE, direction)).limit(2)
            if cursor is not None:
                stmt = stmt.where(keyset_predicate(_NULLABLE, direction, cursor[0], cursor[1]))
            page = list(db_session.scalars(stmt).all())
            if not page:
                break
            walked.extend(person.id for person in page)
            cursor = (page[-1].last_name, page[-1].id)

        assert walked == expected, direction
