"""What a hostile or unlucky upload must not do to the intake (the branch's security review):
spend CPU, return a 500, or use one token twice; and the parity details of the groundedness port
that a crafted value could use to disagree with the CLI."""

import json
import random
import threading
import time
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from work_profile_helpers import Clock, person, sample, settings, sign_in

from rebase_core.db import session_factory
from rebase_core.errors import WorkProfileRefused
from rebase_core.models import CliAuthorization, WorkProfile
from rebase_core.work_profiles.groundedness import (
    TooManyAnchors,
    _Matcher,
    _number,
    assess_groundedness,
    extract_anchors,
    split_tech,
)
from rebase_core.work_profiles.intake import MAX_ANCHORS, WorkProfileService
from rebase_core.work_profiles.structure import MAX_COUNT


def _submit(session: Session, clock: Clock, token: str, raw: bytes | dict[str, Any]) -> Any:
    body = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
    return WorkProfileService(session, settings(), clock).submit(token, body)


# ---- one token, one profile, under concurrency ---------------------------------------------


def _race(engine: Engine, calls: list[Any]) -> list[BaseException | None]:
    barrier = threading.Barrier(len(calls))
    outcomes: list[BaseException | None] = [None] * len(calls)

    def run(index: int) -> None:
        session = session_factory(engine)()
        try:
            barrier.wait()
            calls[index](session)
        except BaseException as error:  # noqa: BLE001  (the test reads what each thread met)
            outcomes[index] = error
        finally:
            session.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return outcomes


def test_one_token_sent_twice_at_once_stores_one_profile_and_answers_one_401(
    hub_session: Session, hub_engine: Engine
) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    token = sign_in(hub_session, clock, user_id)
    send = lambda session: _submit(session, clock, token, sample())  # noqa: E731

    outcomes = _race(hub_engine, [send, send])

    assert sorted(type(o).__name__ for o in outcomes) == ["CliTokenInvalid", "NoneType"]
    hub_session.expire_all()
    assert hub_session.scalar(select(func.count()).select_from(WorkProfile)) == 1


def test_two_tokens_of_one_freelancer_sent_at_once_replace_each_other_without_an_error(
    hub_session: Session, hub_engine: Engine
) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    first, second = sign_in(hub_session, clock, user_id), sign_in(hub_session, clock, user_id)

    outcomes = _race(
        hub_engine,
        [
            lambda session: _submit(session, clock, first, sample()),
            lambda session: _submit(session, clock, second, sample()),
        ],
    )

    assert outcomes == [None, None]
    hub_session.expire_all()
    assert hub_session.scalar(select(func.count()).select_from(WorkProfile)) == 1


# ---- cost ----------------------------------------------------------------------------------


def test_the_matcher_agrees_with_the_scan_it_replaces_and_is_fast() -> None:
    rng = random.Random(11)
    pool = {float(rng.randint(-50, 5000)) for _ in range(300)} | {0.0, 0.5, 1.0, 87.0}

    def naive(n: float) -> bool:
        return n in pool or any(v != 0 and n != 0 and abs((n - v) / v) <= 0.05 for v in pool)

    matcher = _Matcher(pool)
    values = [float(rng.randint(-60, 5500)) for _ in range(3000)] + [0.0, 0.4, 0.95, 1.05]
    assert [matcher.matches(v) for v in values] == [naive(v) for v in values]

    big = _Matcher({float(i * 3) for i in range(60_000)})
    started = time.monotonic()
    for i in range(20_000):
        big.matches(float(i * 3 + 1))
    assert time.monotonic() - started < 2


def test_a_profile_made_of_anchors_is_refused_quickly_and_costs_the_sign_in(
    hub_session: Session,
) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    token = sign_in(hub_session, clock, user_id)
    profile = sample()
    profile["summary"] = " ".join(f"{n % 9000 + 2}" for n in range(60_000))  # ~ 300 KB of numbers
    profile["domains"] = [{"label": f"d{i}", "products": 0, "sessions": 0} for i in range(2000)]

    started = time.monotonic()
    with pytest.raises(WorkProfileRefused, match="troppe cifre"):
        _submit(hub_session, clock, token, profile)

    assert time.monotonic() - started < 5
    assert hub_session.scalars(select(CliAuthorization)).one().state == "spent"
    with pytest.raises(TooManyAnchors):
        assess_groundedness(profile, max_anchors=MAX_ANCHORS)


# ---- values that must be a 422, never a 500 ------------------------------------------------


def _deep(depth: int) -> bytes:
    return b'{"a":' * depth + b"1" + b"}" * depth


def _with(change: Any) -> dict[str, Any]:
    profile = sample()
    change(profile)
    return profile


HOSTILE: list[tuple[str, Any, str]] = [
    ("deep json", _deep(50_000), "JSON valido"),
    ("deep but parseable", _deep(200), "annidato"),
    ("NaN", b'{"schema": "playnew-profile/v1", "x": NaN}', "JSON valido"),
    ("Infinity", b'{"schema": "playnew-profile/v1", "x": -Infinity}', "JSON valido"),
    ("1e999", b'{"schema": "playnew-profile/v1", "x": 1e999}', "JSON valido"),
    ("huge int", b'{"schema": "playnew-profile/v1", "x": ' + b"9" * 5000 + b"}", "JSON valido"),
    ("NUL in a text", _with(lambda p: p.update(summary="a\u0000b")), "nullo"),
    ("NUL in a key", _with(lambda p: p.update({"k\u0000": 1})), "nullo"),
    ("a count of 10^40", _with(lambda p: p["volume"].update(sessions=10**40)), "volume.sessions"),
    (
        "a count past a billion",
        _with(lambda p: p["projects"][0].update(sessions=MAX_COUNT + 1)),
        "sessions",
    ),
]


@pytest.mark.parametrize(("name", "raw", "sentence"), HOSTILE, ids=[h[0] for h in HOSTILE])
def test_a_hostile_value_is_a_422_sentence(
    hub_session: Session, name: str, raw: Any, sentence: str
) -> None:
    clock = Clock()
    user_id, _ = person(hub_session)
    token = sign_in(hub_session, clock, user_id)

    with pytest.raises(WorkProfileRefused) as refused:
        _submit(hub_session, clock, token, raw)

    assert sentence in refused.value.message


def test_huge_numbers_in_fields_the_pool_reads_do_not_overflow() -> None:
    profile = sample()
    profile["intensity"]["observedDays"] = 10**400
    profile["intensity"]["activeDaysRatio"] = 1e308
    profile["distribution"] = {"top3Share": 1e308, "multiMonthShare": 10**400}
    profile["domains"] = [{"label": "x", "products": 1e308, "sessions": 1e308}] * 3
    profile["summary"] = "Over 5 products in 2026-05."

    result = assess_groundedness(profile)

    assert result.score is not None or result.total < 4
    assert any("products across domains" in a["anchor"] for a in result.anomalies)


# ---- parity with JavaScript ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12", 12.0),
        (" 12\u00a0", 12.0),
        ("1_000", None),
        ("0x10", 16.0),
        ("0b11", 3.0),
        ("0o17", 15.0),
        ("-0x10", None),
        ("", 0.0),
        ("1e3", 1000.0),
        (".5", 0.5),
        ("5.", 5.0),
        ("inf", None),
        ("Infinity", None),
        ("nan", None),
        ("١٢", None),
        ([5], 5.0),
        ([], 0.0),
        ([1, 2], None),
        ([[7]], 7.0),
        ({}, None),
        (True, 1.0),
        (None, None),
        (10**400, None),
    ],
)
def test_number_reads_a_value_as_javascripts_number_does(
    value: Any, expected: float | None
) -> None:
    assert _number(value) == expected


def test_a_percent_with_a_javascript_space_before_the_sign_is_an_anchor() -> None:
    for space in ("\u00a0", "\u202f", "\u2003", "\ufeff", " ", "\t"):
        kinds = [a.kind for a in extract_anchors(f"87{space}% of days")]
        assert "percent" in kinds, repr(space)
    assert "percent" not in [
        a.kind for a in extract_anchors("87\u200b%")
    ]  # a zero-width space is not `\s`


def test_tech_labels_split_on_javascript_spaces() -> None:
    assert split_tech("Supabase\u00a0Postgres") == ["supabase", "postgres"]
