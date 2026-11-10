"""The one door to PigroCRM's API (REB-892, HCORE-08): what a status means, that a `PUT`
never raises, the size check, the outcome written on a row, and the sweep round. No
database: the session is a stand-in that counts its commits and rollbacks."""

import json
import logging
import urllib.error
from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from rebase_core.http import MAX_BODY_BYTES, HttpCall
from rebase_core.match_words import ERROR, HTTPS_ONLY, LINKED, REJECTED
from rebase_core.pigro_door import (
    ANSWERED_STATUS,
    CAUSE_NOT_THE_SHAPE,
    CAUSE_REFUSED,
    CAUSE_TIMEOUT,
    CAUSE_TOO_LONG,
    CAUSE_UNREACHABLE,
    NOT_ANSWERING,
    NOT_THE_SHAPE,
    TOO_LONG,
    Gone,
    Outcome,
    PigroDoor,
    PigroUnavailable,
    SweepCount,
    committed,
    speaks_https,
    sweep_round,
    write,
)

URL = "https://crm.test"
TOKEN = "secret-token"


class Answer(BaseModel):
    slug: str


def _door(status: int = 200, body: bytes = b"", *, url: str = URL) -> tuple[PigroDoor, list[tuple]]:
    seen: list[tuple] = []

    def http(
        method: str, target: str, headers: dict[str, str], payload: bytes
    ) -> tuple[int, bytes]:
        seen.append((method, target, headers, payload))
        return status, body

    return PigroDoor(url, TOKEN, http), seen


def _raising(error: BaseException) -> PigroDoor:
    def http(
        method: str, target: str, headers: dict[str, str], payload: bytes
    ) -> tuple[int, bytes]:
        raise error

    http_call: HttpCall = http
    return PigroDoor(URL, TOKEN, http_call)


def _put(
    door: PigroDoor, *, refused: tuple[int, ...] = (422,), gone: tuple[int, ...] = ()
) -> Outcome[Answer]:
    return door.put("/x/1", {"a": 1}, Answer, subject="the test", refused=refused, gone=gone)


def test_a_put_sends_the_bearer_and_the_json_body_to_the_door() -> None:
    door, seen = _door(201, b'{"slug": "ada"}')
    outcome = _put(door)
    assert outcome == Outcome(LINKED, answer=Answer(slug="ada"))
    (method, target, headers, payload) = seen[0]
    assert (method, target, json.loads(payload)) == ("PUT", f"{URL}/x/1", {"a": 1})
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Content-Type"] == "application/json"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (422, b'{"detail": "ruolo: troppo lungo"}', Outcome(REJECTED, "ruolo: troppo lungo")),
        (422, b"", Outcome(REJECTED, "HTTP 422")),
        (
            409,
            json.dumps({"code": "conflict", "reason": "Deal eliminato."}).encode(),
            Outcome(REJECTED, "Deal eliminato.", gone=True),
        ),
        (503, b'{"detail": "Riprova tra poco."}', Outcome(ERROR, "Riprova tra poco.")),
        (500, b'{"detail": "boom"}', Outcome(ERROR, "HTTP 500")),
        (404, b"", Outcome(ERROR, "HTTP 404")),
    ],
)
def test_the_status_says_which_state_the_row_is_left_in(
    status: int, body: bytes, expected: Outcome[Answer]
) -> None:
    door, _ = _door(status, body)
    assert _put(door, refused=(409, 422), gone=(409,)) == expected


def test_a_status_the_caller_did_not_name_as_refused_is_an_error_to_retry() -> None:
    door, _ = _door(409, b'{"detail": "x"}')
    assert _put(door) == Outcome(ERROR, "HTTP 409")


def test_an_answer_past_the_cap_or_not_the_shape_is_an_error_not_a_link() -> None:
    long_body = b'{"slug": "' + b"a" * MAX_BODY_BYTES + b'"}'
    assert _put(_door(200, long_body)[0]) == Outcome(ERROR, CAUSE_TOO_LONG)
    assert _put(_door(201, b'{"nope": 1}')[0]) == Outcome(ERROR, CAUSE_NOT_THE_SHAPE)
    assert _put(_door(200, b"<html>")[0]) == Outcome(ERROR, CAUSE_NOT_THE_SHAPE)


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (TimeoutError("slow"), CAUSE_TIMEOUT),
        (urllib.error.URLError(TimeoutError("slow")), CAUSE_TIMEOUT),
        (urllib.error.URLError(ConnectionRefusedError()), CAUSE_REFUSED),
        (ConnectionResetError("reset"), CAUSE_UNREACHABLE),
        (RuntimeError("anything at all"), CAUSE_UNREACHABLE),
    ],
)
def test_a_put_never_raises_and_names_the_cause(
    error: BaseException, cause: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="rebase_core.pigro_door"):
        outcome = _put(_raising(error))
    assert outcome == Outcome(ERROR, cause)
    assert TOKEN not in caplog.text


def test_the_bearer_never_travels_over_plain_http_to_another_host() -> None:
    door, seen = _door(201, b'{"slug": "ada"}', url="http://crm.test")
    assert _put(door) == Outcome(ERROR, HTTPS_ONLY)
    assert seen == []
    assert speaks_https("http://localhost:8000") and speaks_https("https://crm.test")
    assert not speaks_https("http://crm.test") and not speaks_https("https://")


def test_a_get_returns_what_parse_reads() -> None:
    door, seen = _door(200, b'{"slug": "ada"}')
    assert door.get("/x?from=1", Answer.model_validate_json) == Answer(slug="ada")
    assert seen[0][:2] == ("GET", f"{URL}/x?from=1")


def test_a_get_that_is_not_answered_as_asked_says_why_in_the_admins_words() -> None:
    with pytest.raises(PigroUnavailable) as no_answer:
        _raising(OSError("down")).get("/x", Answer.model_validate_json)
    assert str(no_answer.value) == NOT_ANSWERING
    with pytest.raises(PigroUnavailable) as bad_status:
        _door(502)[0].get("/x", Answer.model_validate_json)
    assert str(bad_status.value) == ANSWERED_STATUS.format(status=502)
    with pytest.raises(PigroUnavailable) as too_long:
        _door(200, b"x" * (MAX_BODY_BYTES + 1))[0].get("/x", Answer.model_validate_json)
    assert str(too_long.value) == TOO_LONG
    with pytest.raises(PigroUnavailable) as shape:
        _door(200, b'{"nope": 1}')[0].get("/x", Answer.model_validate_json)
    assert str(shape.value) == NOT_THE_SHAPE


def test_a_get_status_the_caller_names_is_gone_with_the_crms_sentence() -> None:
    door, _ = _door(409, json.dumps({"code": "conflict", "reason": "Deal eliminato."}).encode())
    with pytest.raises(Gone) as gone:
        door.get("/x", Answer.model_validate_json, gone=(409,))
    assert gone.value.cause == "Deal eliminato."


def test_write_sets_the_named_columns_and_copies_a_link_over_a_cleared_error() -> None:
    row = SimpleNamespace(state=None, error="old", slug=None)

    def copy(target: SimpleNamespace, answer: Answer) -> None:
        target.slug = answer.slug

    write(row, Outcome(ERROR, "HTTP 500"), state="state", error="error", copy=copy)
    assert (row.state, row.error, row.slug) == (ERROR, "HTTP 500", None)
    write(
        row,
        Outcome(LINKED, answer=Answer(slug="ada")),
        state="state",
        error="error",
        copy=copy,
    )
    assert (row.state, row.error, row.slug) == (LINKED, None, "ada")
    write(row, Outcome(ERROR, "HTTP 503"), state="state", error="error", copy=copy)
    assert (row.state, row.error, row.slug) == (ERROR, "HTTP 503", "ada")


def test_committed_commits_a_clean_block_and_rolls_back_a_failed_one() -> None:
    session = create_autospec(Session, instance=True)
    with committed(session):
        pass
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    with pytest.raises(ValueError, match="no"), committed(session):
        raise ValueError("no")
    session.rollback.assert_called_once()
    assert session.commit.call_count == 1


def test_a_sweep_round_lets_go_first_counts_each_row_and_survives_a_failure() -> None:
    session = create_autospec(Session, instance=True)
    ids: list[UUID] = [uuid4() for _ in range(6)]
    seen: list[UUID] = []

    def link(row_id: UUID) -> bool | None:
        # Nothing may be held while the CRM answers: the transaction that read the ids
        # was let go before the first call.
        assert session.rollback.call_count >= 1
        seen.append(row_id)
        position = ids.index(row_id)
        if position == 3:
            raise RuntimeError("one row's failure")
        return [True, False, None, None, True, False][position]

    count = sweep_round(session, ids, link, what="match")
    assert count == SweepCount(linked=2, failed=3)
    assert seen == ids
    assert sweep_round(session, [], link, what="match") == SweepCount(0, 0)
