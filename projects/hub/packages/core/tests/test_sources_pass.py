"""The refetch pass over many people (REB-839): one failure does not stop it, a rate-limited or
failing account backs off, a spent budget stops it and the next pass resumes, and what is
read while a person or a switch changes.
"""

import logging
from datetime import timedelta

import pytest
from sources_fixtures import (
    LINKS,
    NOW,
    Clock,
    FakeFetcher,
    FakeGithub,
    GithubApi,
    _Flaky,
    _github_rows,
    _pass,
    _person,
    _rows,
    _service,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.github_source import GithubSnapshot
from rebase_core.models import Freelancer, FreelancerSource
from rebase_core.sources import (
    retry_delay,
)
from rebase_core.web_fetch import FetchResult


def test_one_persons_failure_does_not_stop_the_pass(clean_hub: Session) -> None:
    bad = _person(clean_hub, ["https://github.com/boom"], email="bad@studio.it")
    good = _person(clean_hub, ["https://github.com/ada"], email="good@studio.it")
    service = _service(clean_hub, FakeFetcher(), _Flaky(), on=("github",))

    assert service.refetch_due(limit=10) == 2

    assert _rows(clean_hub, bad) == [] and [r.ref for r in _rows(clean_hub, good)] == ["ada"]


def test_a_rate_limited_account_keeps_the_text_it_had(clean_hub: Session) -> None:
    """GitHub's 403 is `error` (`GithubSource`), and an `error` keeps what was read: the
    text and the hash stay, so the card does not turn stale for a limit."""
    person = _person(clean_hub, ["https://github.com/ada"])
    _service(clean_hub, on=("github",)).refetch(person)
    before = [(r.text, r.content_sha256) for r in _rows(clean_hub, person)]
    limited = FakeGithub(GithubSnapshot("ada", status="error"))

    _service(
        clean_hub, FakeFetcher(), limited, on=("github",), now=NOW + timedelta(days=8)
    ).refetch(person)

    assert [(r.text, r.content_sha256) for r in _rows(clean_hub, person)] == before
    assert {r.status for r in _rows(clean_hub, person)} == {"ok"}
    assert {r.attempts for r in _rows(clean_hub, person)} == {1}  # and it is tried again soon


def test_the_pass_reads_only_people_a_card_is_written_for(clean_hub: Session) -> None:
    kept = _person(clean_hub, LINKS, email="a@studio.it")
    turned_down = _person(clean_hub, LINKS, email="b@studio.it")
    no_cv = _person(clean_hub, LINKS, email="c@studio.it", cv=False)
    service = _service(clean_hub, now=NOW - timedelta(days=10))
    for person in (kept, turned_down):
        service.refetch(person)
    clean_hub.execute(
        text("UPDATE freelancers SET stato = 'scartato' WHERE id = :i"), {"i": turned_down}
    )
    clean_hub.commit()

    assert _service(clean_hub, now=NOW).refetch_due(limit=10) == 1

    assert {r.fetched_at for r in _rows(clean_hub, kept)} == {NOW}
    # What a person turned down had stored goes, and nobody without a CV is ever read.
    assert _rows(clean_hub, turned_down) == [] and _rows(clean_hub, no_cv) == []


def test_removing_the_last_link_deletes_its_text_at_the_next_pass(clean_hub: Session) -> None:
    person = _person(clean_hub, ["https://ada.example/"])
    service = _service(clean_hub, on=("links",))
    service.refetch(person)
    row = clean_hub.get(Freelancer, person)
    assert row is not None
    row.links = []
    clean_hub.commit()

    assert service.refetch_due(limit=5) == 1

    assert _rows(clean_hub, person) == []


def test_a_person_deleted_while_the_pages_were_read_gets_nothing_stored(clean_hub: Session) -> None:
    person = _person(clean_hub, LINKS)

    class Deleting(FakeFetcher):
        def fetch(self, url: str) -> FetchResult:
            clean_hub.execute(
                text("UPDATE freelancers SET deleted_at = now() WHERE id = :i"), {"i": person}
            )
            clean_hub.commit()
            return super().fetch(url)

    service = _service(clean_hub, Deleting())
    service.session.expire_all()

    assert service.refetch(person).items == []
    assert _rows(clean_hub, person) == []


def test_a_switch_turned_off_while_the_pages_were_read_stores_nothing(clean_hub: Session) -> None:
    person = _person(clean_hub, LINKS)

    class Switching(FakeFetcher):
        def fetch(self, url: str) -> FetchResult:
            clean_hub.execute(text("UPDATE ai_sources SET enabled = false WHERE kind = 'links'"))
            clean_hub.commit()
            return super().fetch(url)

    service = _service(clean_hub, Switching(), on=("links",))

    service.refetch(person)

    assert _rows(clean_hub, person) == []


# ---- a failure is retried on a backoff; GitHub's limit is the pass's (REB-844) ---------------


@pytest.mark.parametrize(
    ("attempts", "hours"),
    [(0, 1), (1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 24), (7, 24), (10_000, 24)],
)
def test_the_backoff_doubles_from_an_hour_up_to_a_day(attempts: int, hours: int) -> None:
    assert retry_delay(attempts) == timedelta(hours=hours)


def test_an_error_row_is_retried_on_a_growing_backoff_and_forgotten_on_success(
    clean_hub: Session,
) -> None:
    person = _person(clean_hub, ["https://github.com/ada"])
    github = FakeGithub(GithubSnapshot("ada", status="error"))
    moment = NOW

    for attempts, hours in enumerate([1, 2, 4, 8, 16, 24, 24], start=1):
        service = _service(clean_hub, FakeFetcher(), github, on=("github",), now=moment)
        assert service.refetch_due(limit=10) == 1
        (row,) = _rows(clean_hub, person)
        assert (row.status, row.attempts, row.fetched_at) == ("error", attempts, moment)

        wait = timedelta(hours=hours)
        early = _service(clean_hub, FakeFetcher(), github, on=("github",), now=moment + wait * 0.99)
        assert early.refetch_due(limit=10) == 0, f"after {attempts} failures, before {hours}h"
        moment += wait  # the pass of exactly that hour, as the hourly loop reaches it

    github.snapshot = None  # GitHub answers again
    service = _service(clean_hub, FakeFetcher(), github, on=("github",), now=moment)
    assert service.refetch_due(limit=10) == 1
    (row,) = _rows(clean_hub, person)
    assert (row.status, row.attempts, row.fetched_at) == ("ok", 0, moment)
    # Back on the weekly clock: nothing for six days, then the normal refresh.
    later = _service(
        clean_hub, FakeFetcher(), github, on=("github",), now=moment + timedelta(days=6)
    )
    assert later.refetch_due(limit=10) == 0
    week = _service(
        clean_hub, FakeFetcher(), github, on=("github",), now=moment + timedelta(days=7, seconds=1)
    )
    assert week.refetch_due(limit=10) == 1


def test_a_source_that_was_read_and_then_fails_is_retried_within_the_hour_too(
    clean_hub: Session,
) -> None:
    person = _person(clean_hub, ["https://github.com/ada"])
    _service(clean_hub, on=("github",)).refetch(person)
    (before,) = _rows(clean_hub, person)
    failing = FakeGithub(GithubSnapshot("ada", status="error"))
    fails_at = NOW + timedelta(days=8)

    _service(clean_hub, FakeFetcher(), failing, on=("github",), now=fails_at).refetch(person)

    (row,) = _rows(clean_hub, person)
    assert (row.text, row.status, row.attempts, row.fetched_at) == (
        before.text,
        "ok",
        1,
        fails_at,
    )
    half = _service(
        clean_hub, FakeFetcher(), failing, on=("github",), now=fails_at + timedelta(minutes=30)
    )
    assert half.refetch_due(limit=10) == 0
    retry = _service(
        clean_hub, FakeFetcher(), FakeGithub(), on=("github",), now=fails_at + timedelta(hours=1)
    )
    assert retry.refetch_due(limit=10) == 1
    assert {r.attempts for r in _rows(clean_hub, person)} == {0}


def test_a_limited_read_writes_nothing_not_even_the_count(clean_hub: Session) -> None:
    person = _person(clean_hub, ["https://github.com/ada"])
    failing = FakeGithub(GithubSnapshot("ada", status="error"))
    _service(clean_hub, FakeFetcher(), failing, on=("github",), now=NOW).refetch(person)
    _service(
        clean_hub, FakeFetcher(), failing, on=("github",), now=NOW + timedelta(hours=1)
    ).refetch(person)
    (before,) = _rows(clean_hub, person)
    assert before.attempts == 2

    limited = FakeGithub(GithubSnapshot("ada", status="limited"))
    _service(
        clean_hub, FakeFetcher(), limited, on=("github",), now=NOW + timedelta(hours=5)
    ).refetch(person)

    (row,) = _rows(clean_hub, person)
    assert (row.status, row.attempts, row.fetched_at) == ("error", 2, before.fetched_at)


def test_a_spent_budget_stops_the_pass_and_leaves_the_rest_untouched(
    clean_hub: Session, caplog: pytest.LogCaptureFixture
) -> None:
    people = [
        _person(clean_hub, [f"https://github.com/dev{n}"], email=f"p{n}@studio.it")
        for n in range(5)
    ]
    clean_hub.execute(text("UPDATE ai_sources SET enabled = true"))
    clean_hub.commit()
    clock = Clock(NOW)
    api = GithubApi(budget=4, clock=clock)  # two people's worth

    with caplog.at_level(logging.WARNING, logger="rebase_core.sources"):
        read, _ = _pass(clean_hub, api)

    assert read == 2
    assert _github_rows(clean_hub, people) == ["ok", "ok", "-", "-", "-"]
    assert len(api.calls) == 4  # no call over the budget: the third person was never asked
    assert "3 people wait for the next pass" in caplog.text
    assert (
        clean_hub.scalar(select(FreelancerSource).where(FreelancerSource.status == "error")) is None
    )


def test_the_person_the_limit_cut_off_still_gets_their_links_read(clean_hub: Session) -> None:
    links = _person(
        clean_hub, ["https://github.com/dev0", "https://ada.example/"], email="a@studio.it"
    )
    github_only = _person(clean_hub, ["https://github.com/dev1"], email="b@studio.it")
    clean_hub.execute(text("UPDATE ai_sources SET enabled = true"))
    clean_hub.commit()
    clock = Clock(NOW)
    api = GithubApi(budget=0, clock=clock)
    fetcher = FakeFetcher()

    read, _ = _pass(clean_hub, api, fetcher)

    assert read == 0  # the first person's account was left for the next pass
    assert [(r.kind, r.status) for r in _rows(clean_hub, links)] == [("link", "ok")]
    assert _rows(clean_hub, github_only) == []
    assert len(api.calls) == 1 and fetcher.calls == ["https://ada.example/"]

    clock.now += timedelta(hours=1)
    api.budget = 60
    api.hour_turns()
    read, _ = _pass(clean_hub, api, fetcher)
    assert read == 2
    assert _github_rows(clean_hub, [links, github_only]) == ["ok", "ok"]


def test_a_later_pass_resumes_where_the_limit_stopped_the_last(clean_hub: Session) -> None:
    people = [
        _person(clean_hub, [f"https://github.com/dev{n}"], email=f"p{n}@studio.it")
        for n in range(5)
    ]
    clean_hub.execute(text("UPDATE ai_sources SET enabled = true"))
    clean_hub.commit()
    clock = Clock(NOW)
    api = GithubApi(budget=4, clock=clock)
    seen = []

    for _hour in range(3):
        _pass(clean_hub, api)
        seen.append(_github_rows(clean_hub, people))
        clock.now += timedelta(hours=1)
        api.hour_turns()

    assert seen == [
        ["ok", "ok", "-", "-", "-"],
        ["ok", "ok", "ok", "ok", "-"],
        ["ok", "ok", "ok", "ok", "ok"],
    ]
    assert {r.attempts for p in people for r in _rows(clean_hub, p)} == {0}
    read, _ = _pass(clean_hub, api)
    assert read == 0  # everyone is read: nothing is due for a week


def test_one_broken_account_is_backed_off_while_the_rest_are_read(clean_hub: Session) -> None:
    people = [
        _person(clean_hub, [f"https://github.com/dev{n}"], email=f"p{n}@studio.it")
        for n in range(3)
    ]
    clean_hub.execute(text("UPDATE ai_sources SET enabled = true"))
    clean_hub.commit()
    clock = Clock(NOW)
    api = GithubApi(budget=60, clock=clock)
    api.broken = {"dev1"}

    _pass(clean_hub, api)
    assert _github_rows(clean_hub, people) == ["ok", "error", "ok"]
    calls = len(api.calls)

    clock.now += timedelta(minutes=30)
    _pass(clean_hub, api)
    assert len(api.calls) == calls  # inside its hour: nothing asked

    clock.now += timedelta(minutes=30)
    api.broken = set()
    _pass(clean_hub, api)
    assert _github_rows(clean_hub, people) == ["ok", "ok", "ok"]
    assert len(api.calls) == calls + 2  # one person, two calls
