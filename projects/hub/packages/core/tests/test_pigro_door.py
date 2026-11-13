"""The one door to PigroCRM's API (REB-892, HCORE-08): what a status means, that a `PUT`
never raises, the size check, the outcome written on a row, and the sweep round. No
database: the session is a stand-in that counts its commits and rollbacks."""

import json
import logging
import urllib.error
from datetime import date
from types import SimpleNamespace
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.crm_words import ERROR, HTTPS_ONLY, LINKED, REJECTED
from rebase_core.http import (
    MAX_BODY_BYTES,
    MAX_DOWNLOAD_BYTES,
    HttpCall,
    urllib_call,
    urllib_download_call,
)
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
    FreelancerDoor,
    Gone,
    Outcome,
    PigroDoor,
    PigroMisconfigured,
    PigroUnavailable,
    SweepCount,
    committed,
    freelancer_door,
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


# ---- the reads of a freelancer (REB-1107) -------------------------------------------------


class _Script:
    """A seam that answers from a list, one answer per call, and records the calls."""

    def __init__(self, *answers: tuple[int, bytes] | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(
        self, method: str, target: str, headers: dict[str, str], payload: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, target, headers))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_get_is_asked_again_on_a_failed_connection_and_on_a_not_ready_crm_only() -> None:
    def parse(raw: bytes) -> str:
        return raw.decode()

    for failed in (ConnectionRefusedError(), (502, b""), (503, b""), (504, b"")):
        seam = _Script(failed, (200, b"ok"))  # type: ignore[arg-type]
        assert PigroDoor(URL, TOKEN, seam).get("/x", parse, attempts=2) == "ok"
        assert len(seam.calls) == 2
    # Asked again once, not for ever: the second failure is the answer.
    seam = _Script(ConnectionRefusedError(), ConnectionRefusedError())
    with pytest.raises(PigroUnavailable, match="non risponde"):
        PigroDoor(URL, TOKEN, seam).get("/x", parse, attempts=2)
    assert len(seam.calls) == 2
    # What is not a CRM that is not ready is not asked again: a refusal, a missing page, a
    # server error, and the default is one call.
    for status in (400, 404, 500):
        seam = _Script((status, b""), (200, b"ok"))
        with pytest.raises(PigroUnavailable):
            PigroDoor(URL, TOKEN, seam).get("/x", parse, attempts=2)
        assert len(seam.calls) == 1
    seam = _Script((503, b""), (200, b"ok"))
    with pytest.raises(PigroUnavailable):
        PigroDoor(URL, TOKEN, seam).get("/x", parse)
    assert len(seam.calls) == 1


def test_a_get_reads_a_file_up_to_its_own_limit_not_the_json_seams() -> None:
    big = b"x" * (MAX_BODY_BYTES + 1)
    with pytest.raises(PigroUnavailable, match="troppo lungo"):
        PigroDoor(URL, TOKEN, _Script((200, big))).get("/x", lambda raw: raw)
    assert (
        PigroDoor(URL, TOKEN, _Script((200, big))).get(
            "/x", lambda raw: raw, limit=MAX_DOWNLOAD_BYTES
        )
        == big
    )
    with pytest.raises(PigroUnavailable, match="troppo lungo"):
        PigroDoor(URL, TOKEN, _Script((200, big + b"x" * MAX_DOWNLOAD_BYTES))).get(
            "/x", lambda raw: raw, limit=MAX_DOWNLOAD_BYTES
        )


def _reads(*answers: tuple[int, bytes] | Exception) -> tuple[FreelancerDoor, _Script, _Script]:
    json_seam, download_seam = _Script(*answers), _Script(*answers)
    return FreelancerDoor(URL, TOKEN, json_seam, download_seam), json_seam, download_seam


def test_each_read_is_a_get_on_the_freelancers_own_path_with_the_bearer() -> None:
    freelancer, other = uuid4(), uuid4()
    week = {
        "freelancer_id": None,
        "hub_freelancer_id": str(freelancer),
        "date_from": "2026-11-02",
        "date_to": "2026-11-08",
        "weeks": [],
    }
    door, seam, _ = _reads(
        (200, json.dumps(week).encode()),
        (200, b'{"documents": []}'),
        (200, b'{"tax_code": "X", "vat_number": null, "certified_email": null}'),
        (
            200,
            b'{"framework_regimes": ["ordinary"], "shared_clients": 1, '
            b'"tax_code_conflict": false, "organization": true, "other_organization": false}',
        ),
    )

    availability = door.availability(freelancer, date(2026, 11, 2), date(2026, 11, 8))
    contracts = door.contracts(freelancer)
    fiscal = door.fiscal_identifiers(freelancer)
    merge = door.merge_check(freelancer, other)

    assert availability.freelancer_id is None and availability.weeks == []
    assert contracts.documents == []
    assert fiscal.tax_code == "X"
    assert (merge.framework_regimes, merge.shared_clients) == (["ordinary"], 1)
    assert [(method, target) for method, target, _ in seam.calls] == [
        (
            "GET",
            f"{URL}/api/rebase/freelancers/{freelancer}/availability?from=2026-11-02&to=2026-11-08",
        ),
        ("GET", f"{URL}/api/rebase/freelancers/{freelancer}/contracts"),
        ("GET", f"{URL}/api/rebase/freelancers/{freelancer}/fiscal-identifiers"),
        ("GET", f"{URL}/api/rebase/freelancers/{freelancer}/merge-check?with={other}"),
    ]
    assert {headers["Authorization"] for _, _, headers in seam.calls} == {f"Bearer {TOKEN}"}
    # With no dates the window is the CRM's default: nothing is sent.
    door_open, seam_open, _ = _reads((200, json.dumps(week).encode()))
    door_open.availability(freelancer)
    assert seam_open.calls[0][1].endswith("/availability")


def test_a_signed_pdf_goes_through_the_download_seam_and_a_404_is_gone() -> None:
    freelancer, document = uuid4(), uuid4()
    big_pdf = b"%PDF" + b"0" * (MAX_BODY_BYTES * 2)
    door, json_seam, download_seam = _reads((200, big_pdf))

    assert door.signed_pdf(freelancer, document) == big_pdf

    assert json_seam.calls == []
    assert download_seam.calls[0][1] == (
        f"{URL}/api/rebase/freelancers/{freelancer}/contracts/{document}/pdf"
    )
    refused, _, _ = _reads((404, b'{"detail": "Not Found"}'))
    with pytest.raises(Gone):
        refused.signed_pdf(freelancer, document)


def test_the_reads_refuse_to_leave_without_a_token_or_over_plain_http() -> None:
    seam = _Script((200, b"{}"))
    for door in (
        FreelancerDoor(URL, "", seam, seam),
        FreelancerDoor("http://crm.test", TOKEN, seam, seam),
    ):
        with pytest.raises(PigroMisconfigured):
            door.contracts(uuid4())
        with pytest.raises(PigroMisconfigured):
            door.signed_pdf(uuid4(), uuid4())
    # This machine is the one plain HTTP the bearer may travel to.
    assert seam.calls == []
    local = FreelancerDoor(
        "http://127.0.0.1:8000", TOKEN, _Script((200, b'{"documents": []}')), seam
    )
    assert local.contracts(uuid4()).documents == []


def test_the_builder_pairs_production_s_json_seam_with_the_download_one() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        pigro_api_url=URL,
        pigro_engagements_token=TOKEN,
    )

    production = freelancer_door(settings)
    assert (production.api_url, production.token) == (URL, TOKEN)
    assert production.http is urllib_call and production.download is urllib_download_call
    seam = _Script()
    assert freelancer_door(settings, seam).download is seam
