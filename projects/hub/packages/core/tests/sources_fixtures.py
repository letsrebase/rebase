"""What the sources' tests share (HTEST-10): the fake page fetcher and GitHub client, a person
with links, the service over them and the clock. `test_sources.py`, `test_sources_pass.py` and
the card tests that read sources import from here.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fakes_cards import text_pdf
from freelancer_fixtures import enable_sources
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.github_source import GithubSnapshot, GithubSource, Repo
from rebase_core.models import FreelancerSource
from rebase_core.sources import (
    SourceService,
)
from rebase_core.web_fetch import FetchResult, SafeFetcher

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


PAGE = "Ada\n# Backend engineer\nBuilds payment systems."


class FakeFetcher(SafeFetcher):
    def __init__(self, pages: dict[str, FetchResult] | None = None) -> None:
        super().__init__()
        self.pages = dict(pages or {})
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return self.pages.get(url, FetchResult("ok", PAGE, url))


class FakeGithub(GithubSource):
    def __init__(self, snapshot: GithubSnapshot | None = None) -> None:
        super().__init__(lambda *args: (500, {}, b""))
        self.snapshot = snapshot
        self.calls: list[str] = []

    def fetch(self, login: str) -> GithubSnapshot:
        self.calls.append(login)
        return self.snapshot or snapshot_of(login)


def snapshot_of(
    login: str = "ada", description: str = "a ledger", stars: int = 3, **changes: object
) -> GithubSnapshot:
    repos = [Repo("ledger-core", description, "Rust", False, False, stars, "2026-10-01T00:00:00Z")]
    return GithubSnapshot(
        login=login,
        bio="I build things.",
        repos=repos,
        language_totals={"Rust": 100 * stars},
        contribution_recency="codice pubblicato negli ultimi 3 mesi",
        volatile={"public_repos": 1},
        **changes,  # type: ignore[arg-type]
    )


def _person(
    session: Session, links: list[str], email: str = "ada@studio.it", *, cv: bool = True
) -> UUID:
    data = FreelancerCreate(
        nome="Ada",
        last_name="Lovelace",
        email=email,
        daily_rate=Decimal("450.00"),
        posizione="Backend developer",
        remote="remote",
        links=links,
    )
    service = FreelancerService(session)
    read, _ = (
        service.apply(data, text_pdf("Ada, backend developer."), "cv.pdf", "")
        if cv
        else service.apply(data)
    )
    return read.id


def _service(
    session: Session,
    fetcher: FakeFetcher | None = None,
    github: FakeGithub | None = None,
    *,
    on: tuple[str, ...] = ("github", "links"),
    now: datetime = NOW,
) -> SourceService:
    enable_sources(session, *on)
    return SourceService(session, fetcher or FakeFetcher(), github or FakeGithub(), lambda: now)


def _rows(session: Session, freelancer_id: UUID) -> list[FreelancerSource]:
    return list(
        session.scalars(
            select(FreelancerSource)
            .where(FreelancerSource.freelancer_id == freelancer_id)
            .order_by(FreelancerSource.kind, FreelancerSource.position)
            .execution_options(populate_existing=True)
        )
    )


LINKS = ["https://github.com/ada", "https://ada.example/", "https://www.linkedin.com/in/ada"]


# ---- the weekly pass, review of #671 -------------------------------------------------------


class _Flaky(FakeGithub):
    """GitHub that dies for one account, the way `urllib_call` does: with an exception."""

    def fetch(self, login: str) -> GithubSnapshot:
        self.calls.append(login)
        if login == "boom":
            raise TimeoutError("timed out")
        return snapshot_of(login)


class GithubApi:
    """GitHub's API behind the `HttpHeadersCall` seam with the unauthenticated hourly
    budget. Every call spends one; the answer that takes the last says `remaining: 0`;
    any call over the budget is a 403 naming the reset. `hour_turns()` is the window
    resetting. Every account has no repository, so a person costs two calls, and the
    logins in `broken` answer 502."""

    def __init__(self, budget: int, clock: "Clock") -> None:
        self.budget = budget
        self.clock = clock
        self.remaining = budget
        self.reset = clock.now + timedelta(minutes=40)
        self.calls: list[str] = []
        self.broken: set[str] = set()

    def hour_turns(self) -> None:
        self.remaining = self.budget
        self.reset = self.clock.now + timedelta(hours=1)

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls.append(url)
        window = {"x-ratelimit-reset": str(int(self.reset.timestamp()))}
        if self.remaining == 0:
            return 403, {**window, "x-ratelimit-remaining": "0"}, b'{"message": "rate limit"}'
        self.remaining -= 1
        answered = {**window, "x-ratelimit-remaining": str(self.remaining)}
        login = url.removeprefix("https://api.github.com/users/").split("/")[0]
        if login in self.broken:
            return 502, answered, b"{}"
        if url.endswith("/repos?type=owner&per_page=100&sort=pushed"):
            return 200, answered, b"[]"
        return 200, answered, json.dumps({"login": login, "type": "User", "bio": login}).encode()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _pass(
    session: Session, api: GithubApi, fetcher: FakeFetcher | None = None
) -> tuple[int, SourceService]:
    """One hourly pass: a new process, so a new `GithubSource` with no memory of the last."""
    service = SourceService(
        session,
        fetcher or FakeFetcher(),
        GithubSource(api, "", now=api.clock),
        api.clock,
    )
    return service.refetch_due(limit=50), service


def _github_rows(session: Session, people: list[UUID]) -> list[str]:
    """Per person, in order: the status of their GitHub row, or `-` for none."""
    result = []
    for person in people:
        rows = [r for r in _rows(session, person) if r.kind == "github"]
        result.append(rows[0].status if rows else "-")
    return result
