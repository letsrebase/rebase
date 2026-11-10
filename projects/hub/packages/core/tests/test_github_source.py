"""The GitHub source (REB-837): which account a list of links names, and what the public
API says about it, as text and as a hash that ignores what moves on its own. A fake
`HttpCall` answers from canned JSON: no test here touches the network."""

import json
from datetime import UTC, datetime, timedelta
from http.client import HTTPException
from typing import Any
from urllib.error import URLError

import pytest

from rebase_core.github_source import (
    LANGUAGE_REPOS,
    GithubSnapshot,
    GithubSource,
    Repo,
    github_login_from_links,
)
from rebase_core.http import NETWORK_ERROR_STATUS

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def repo(name: str, **kwargs: Any) -> dict[str, Any]:
    return {
        "name": name,
        "owner": {"login": kwargs.pop("owner", "ada")},
        "description": kwargs.pop("description", f"does {name}"),
        "language": kwargs.pop("language", "Python"),
        "fork": kwargs.pop("fork", False),
        "archived": kwargs.pop("archived", False),
        "private": kwargs.pop("private", False),
        "stargazers_count": kwargs.pop("stars", 0),
        "pushed_at": kwargs.pop("pushed_at", "2026-10-01T10:00:00Z"),
        **kwargs,
    }


class FakeGithub:
    """An `HttpHeadersCall` over the three endpoints the source uses; `calls` keeps what
    left, `answered` is the header set every answer carries."""

    def __init__(
        self,
        profile: dict[str, Any] | None = None,
        repos: list[dict[str, Any]] | None = None,
        languages: dict[str, dict[str, int]] | None = None,
        status: int = 200,
        answered: dict[str, str] | None = None,
    ) -> None:
        self.profile = {"login": "ada", "type": "User", "bio": "I build things.", **(profile or {})}
        self.repos = repos if repos is not None else []
        self.languages = languages or {}
        self.status = status
        self.answered = answered or {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls.append((method, url, headers))
        if self.status != 200:
            return self.status, self.answered, b'{"message": "no"}'
        path = url.removeprefix("https://api.github.com")
        if path.startswith("/users/ada/repos"):
            return 200, self.answered, json.dumps(self.repos).encode()
        if path == "/users/ada":
            return 200, self.answered, json.dumps(self.profile).encode()
        if path.startswith("/repos/ada/") and path.endswith("/languages"):
            name = path.split("/")[3]
            return 200, self.answered, json.dumps(self.languages.get(name, {})).encode()
        return 404, self.answered, b"{}"


def source(fake: FakeGithub, token: str = "") -> GithubSource:
    return GithubSource(fake, token, now=lambda: NOW)


@pytest.mark.parametrize(
    ("links", "login"),
    [
        (["https://github.com/ada"], "ada"),
        (["https://www.github.com/ada/"], "ada"),
        (["http://github.com/ada?tab=repositories"], "ada"),
        (["https://github.com/ada/analytical-engine"], "ada"),
        (["https://github.com/ada/analytical-engine/issues/4"], "ada"),
        (["https://example.com", "https://github.com/ada"], "ada"),
        (["https://github.com/orgs/acme/people", "https://github.com/ada"], "ada"),
        (["https://github.com/sponsors/ada"], None),
        (["https://github.com/orgs/acme"], None),
        (["https://github.com/"], None),
        (["https://github.com/-bad-"], None),
    ],
)
def test_login_from_profile_and_repo_urls(links: list[str], login: str | None) -> None:
    assert github_login_from_links(links) == login


def test_login_none_when_no_github_link() -> None:
    links = ["https://ada.example", "https://ada.github.io", "https://gitlab.com/ada", ""]
    assert github_login_from_links(links) is None
    assert github_login_from_links([]) is None


def test_login_ignores_lookalike_hosts() -> None:
    assert github_login_from_links(["https://github.com.evil.example/ada"]) is None
    assert github_login_from_links(["https://notgithub.com/ada"]) is None


def test_only_owned_public_repos_listed() -> None:
    fake = FakeGithub(
        repos=[
            repo("mine"),
            repo("someone-elses", owner="grace"),
            repo("secret", private=True),
            repo("broken", disabled=True),
        ]
    )
    snapshot = source(fake).fetch("ada")
    assert [r.name for r in snapshot.repos] == ["mine"]
    assert "someone-elses" not in snapshot.normalised_text()
    assert "secret" not in snapshot.normalised_text()
    listing = next(url for _, url, _ in fake.calls if "/repos?" in url)
    assert "type=owner" in listing and "per_page=100" in listing


def test_forks_and_archived_sorted_last_and_marked() -> None:
    fake = FakeGithub(
        repos=[
            repo("forked", fork=True, stars=900),
            repo("old", archived=True, stars=500),
            repo("small", stars=1),
            repo("big", stars=50),
        ]
    )
    text = source(fake).fetch("ada").normalised_text()
    order = [text.index(f"- {name}") for name in ("big", "small", "forked", "old")]
    assert order == sorted(order)
    assert "- forked [fork]" in text
    assert "- old [archiviato]" in text
    assert "- big:" in text and "[" not in text.split("- big")[1].split("\n")[0]


def test_text_names_the_repositories_the_login_and_the_source() -> None:
    fake = FakeGithub(repos=[repo("ledger-core", description="A ledger library", language="Rust")])
    text = source(fake).fetch("ada").normalised_text()
    assert text.splitlines()[0] == "FONTE: GitHub"
    assert "Utente: ada" in text
    assert "Bio: I build things." in text
    assert "ledger-core: A ledger library (Rust; 0 stelle)" in text


def test_language_totals_come_from_the_top_own_repos_only() -> None:
    names = [f"r{n}" for n in range(LANGUAGE_REPOS + 3)]
    fake = FakeGithub(
        repos=[repo(name, stars=100 - i) for i, name in enumerate(names)]
        + [repo("forked", fork=True, stars=5000)],
        languages={name: {"Python": 600, "Go": 400} for name in names + ["forked"]},
    )
    snapshot = source(fake).fetch("ada")
    asked = [url for _, url, _ in fake.calls if url.endswith("/languages")]
    assert len(asked) == LANGUAGE_REPOS
    assert not any("forked" in url for url in asked)
    assert snapshot.language_totals == {"Python": 600 * LANGUAGE_REPOS, "Go": 400 * LANGUAGE_REPOS}
    assert "Linguaggi (per quantità di codice): Python 60%, Go 40%" in snapshot.normalised_text()


def _snapshot(**changes: Any) -> GithubSnapshot:
    base = {
        "login": "ada",
        "bio": "I build things.",
        "repos": [
            Repo("alpha", "first", "Python", False, False, 10, "2026-10-01T10:00:00Z"),
            Repo("beta", "second", "Go", False, False, 3, "2026-09-01T10:00:00Z"),
        ],
        "language_totals": {"Python": 10},
        "contribution_recency": "codice pubblicato negli ultimi 3 mesi",
        "volatile": {"public_repos": 2},
    }
    return GithubSnapshot(**{**base, **changes})


def test_hash_material_ignores_stars_and_pushed_at() -> None:
    moved = _snapshot(
        repos=[
            Repo("alpha", "first", "Python", False, False, 999, "2026-10-20T00:00:00Z"),
            Repo("beta", "second", "Go", False, False, 4000, "2026-10-26T00:00:00Z"),
        ],
        language_totals={"Python": 99999, "Rust": 1},
        contribution_recency="nessun codice pubblicato da oltre un anno",
        volatile={"public_repos": 7},
    )
    assert moved.hash_material() == _snapshot().hash_material()
    assert moved.normalised_text() != _snapshot().normalised_text()


def test_hash_material_does_not_depend_on_the_weight_order() -> None:
    """Stars decide the order in the text; if they decided it in the hash too, a star
    could reorder the material and make the card stale."""
    a = _snapshot().repos
    flipped = [
        Repo(a[0].name, a[0].description, a[0].language, False, False, 1, a[0].pushed_at),
        Repo(a[1].name, a[1].description, a[1].language, False, False, 500, a[1].pushed_at),
    ]
    assert _snapshot(repos=flipped).hash_material() == _snapshot().hash_material()


@pytest.mark.parametrize(
    "changed",
    [
        {"bio": "I now lead a team."},
        {"repos": [Repo("alpha", "first, rewritten", "Python", False, False, 10, None)]},
        {"repos": [Repo("alpha", "first", "Python", True, False, 10, None)]},
        {"repos": [Repo("alpha", "first", "Python", False, True, 10, None)]},
        {"repos": [Repo("alpha", "first", "Rust", False, False, 10, None)]},
        {"repos": [Repo("gamma", "first", "Python", False, False, 10, None)]},
    ],
)
def test_hash_changes_when_description_changes(changed: dict[str, Any]) -> None:
    assert _snapshot(**changed).hash_material() != _snapshot().hash_material()


def test_token_sent_only_when_configured() -> None:
    anonymous = FakeGithub(repos=[repo("a")])
    source(anonymous).fetch("ada")
    assert anonymous.calls and all("Authorization" not in h for _, _, h in anonymous.calls)

    authed = FakeGithub(repos=[repo("a")])
    source(authed, token="tok-123").fetch("ada")
    assert all(h["Authorization"] == "Bearer tok-123" for _, _, h in authed.calls)
    assert all(h["Accept"] == "application/vnd.github+json" for _, _, h in authed.calls)
    assert all(method == "GET" for method, _, _ in authed.calls)


def test_a_bare_403_is_a_refusal_not_the_rate_limit() -> None:
    """A 403 that names no limit (no `retry-after`, budget left) is GitHub refusing this
    account: `error` keeps what was read (`SourceService.refetch`), where `unreadable`
    would wipe it and wait a week, and the source goes on asking for the next person."""
    github = source(FakeGithub(status=403, answered={"x-ratelimit-remaining": "41"}))
    assert github.fetch("ada").status == "error"
    assert not github.blocked()


@pytest.mark.parametrize("status", [404, 410, 451])
def test_a_missing_or_removed_user_gives_unreadable(status: int) -> None:
    snapshot = source(FakeGithub(status=status)).fetch("ada")
    assert snapshot.status == "unreadable"


@pytest.mark.parametrize(
    "failure",
    [URLError("refused"), TimeoutError("timed out"), ConnectionResetError(), HTTPException()],
    ids=["urlerror", "timeout", "reset", "http"],
)
def test_a_network_exception_gives_error_not_a_raise(failure: Exception) -> None:
    """`urllib_call` unwraps only an HTTP error: everything else it raises. The source turns
    it into the network's status, so one flaky night stops nothing."""

    def raising(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        raise failure

    assert GithubSource(raising, "").fetch("ada").status == "error"


def test_an_organisation_is_not_a_persons_account() -> None:
    fake = FakeGithub(profile={"type": "Organization"}, repos=[repo("a")])
    assert source(fake).fetch("ada").status == "unreadable"


def test_network_failure_and_server_error_give_error() -> None:
    assert source(FakeGithub(status=NETWORK_ERROR_STATUS)).fetch("ada").status == "error"
    assert source(FakeGithub(status=502)).fetch("ada").status == "error"


def test_malformed_answer_is_unreadable_not_a_crash() -> None:
    def garbled(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        return 200, {}, b"<html>not json</html>"

    assert GithubSource(garbled, "").fetch("ada").status == "unreadable"


def test_a_login_that_is_not_one_is_never_sent() -> None:
    fake = FakeGithub()
    assert source(fake).fetch("../../orgs/acme").status == "unreadable"
    assert fake.calls == []


def test_account_age_never_in_text() -> None:
    fake = FakeGithub(
        profile={"created_at": "2013-02-03T04:05:06Z", "updated_at": "2026-10-20T00:00:00Z"},
        repos=[repo("a", created_at="2014-05-06T07:08:09Z")],
    )
    snapshot = source(fake).fetch("ada")
    for material in (snapshot.normalised_text(), snapshot.hash_material()):
        assert "2013" not in material and "2014" not in material
        assert "created" not in material.lower()


def test_activity_is_a_bucket_not_a_date() -> None:
    recent = source(FakeGithub(repos=[repo("a", pushed_at="2026-09-20T00:00:00Z")])).fetch("ada")
    stale = source(FakeGithub(repos=[repo("a", pushed_at="2023-01-01T00:00:00Z")])).fetch("ada")
    forks_only = source(
        FakeGithub(repos=[repo("a", fork=True, pushed_at="2026-10-20T00:00:00Z")])
    ).fetch("ada")
    assert "negli ultimi 3 mesi" in recent.normalised_text()
    assert "da oltre un anno" in stale.normalised_text()
    assert forks_only.contribution_recency == ""
    assert "2026-09" not in recent.normalised_text()


# ---- the rate limit (REB-844) -------------------------------------------------------------

RESET = NOW + timedelta(minutes=25)
SPENT = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(RESET.timestamp()))}


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.mark.parametrize(
    ("status", "answered"),
    [
        (403, SPENT),
        (429, SPENT),
        (429, {}),
        (403, {"retry-after": "30"}),
        (429, {"retry-after": "30"}),
    ],
    ids=["403-spent", "429-spent", "429-bare", "403-retry-after", "429-retry-after"],
)
def test_a_refusal_for_the_limit_is_limited_and_stops_every_later_call(
    status: int, answered: dict[str, str]
) -> None:
    fake = FakeGithub(status=status, answered=answered)
    github = source(fake)

    assert github.fetch("ada").status == "limited"
    assert len(fake.calls) == 1 and github.blocked()
    assert github.fetch("grace").status == "limited"
    assert len(fake.calls) == 1  # nothing left the process after the first refusal


def test_the_source_stays_silent_until_the_reset_the_answer_names() -> None:
    clock = Clock()
    fake = FakeGithub(status=403, answered=SPENT)
    github = GithubSource(fake, "", now=clock)
    assert github.fetch("ada").status == "limited"

    clock.now = RESET - timedelta(seconds=1)
    assert github.blocked() and github.fetch("ada").status == "limited"
    assert len(fake.calls) == 1

    clock.now = RESET
    fake.status = 200
    fake.answered = {}
    assert not github.blocked()
    assert github.fetch("ada").status == "ok"
    assert len(fake.calls) > 1


def test_retry_after_wins_over_the_reset() -> None:
    clock = Clock()
    github = GithubSource(
        FakeGithub(status=403, answered={**SPENT, "retry-after": "90"}), "", now=clock
    )
    github.fetch("ada")
    clock.now = NOW + timedelta(seconds=89)
    assert github.blocked()
    clock.now = NOW + timedelta(seconds=90)
    assert not github.blocked()


def test_a_limit_with_no_date_keeps_the_source_silent_for_an_hour() -> None:
    clock = Clock()
    github = GithubSource(FakeGithub(status=429), "", now=clock)
    github.fetch("ada")
    clock.now = NOW + timedelta(minutes=59)
    assert github.blocked()
    clock.now = NOW + timedelta(hours=1)
    assert not github.blocked()


def test_a_reset_already_past_still_silences_the_source_for_a_minute() -> None:
    """A clock that is ahead of GitHub's would turn the limit into one more refused call
    per person of the pass."""
    clock = Clock()
    stale = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW.timestamp()) - 600)}
    github = GithubSource(FakeGithub(status=403, answered=stale), "", now=clock)
    github.fetch("ada")
    clock.now = NOW + timedelta(seconds=59)
    assert github.blocked()
    clock.now = NOW + timedelta(seconds=60)
    assert not github.blocked()


def test_the_last_request_of_the_budget_is_answered_and_the_next_is_not() -> None:
    """Without a token the answer to the sixtieth request says `remaining: 0`: it is a
    good answer and is used, and the person after it costs GitHub nothing."""
    answered = iter([{"x-ratelimit-remaining": "1"}, SPENT])
    fake = FakeGithub(repos=[])  # no repositories: two calls a person
    plain = fake.__call__

    def counting(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        status, _, body_ = plain(method, url, headers, body)
        return status, next(answered), body_

    github = source(counting)  # type: ignore[arg-type]
    assert github.fetch("ada").status == "ok"
    assert github.blocked()
    assert github.fetch("ada").status == "limited"
    assert len(fake.calls) == 2


def test_a_budget_that_ends_after_the_profile_leaves_the_listing_unasked() -> None:
    fake = FakeGithub(repos=[repo("a")])
    plain = fake.__call__

    def spent_at_once(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        status, _, body_ = plain(method, url, headers, body)
        return status, SPENT, body_

    github = source(spent_at_once)  # type: ignore[arg-type]
    assert github.fetch("ada").status == "limited"
    assert len(fake.calls) == 1


def test_the_limit_spent_in_the_middle_of_a_person_leaves_no_partial_text() -> None:
    """The languages are part of the text: a person whose language calls ran into the
    limit is `limited` as a whole, never stored without them."""
    fake = FakeGithub(repos=[repo("a", stars=3)], languages={"a": {"Rust": 10}})
    original = fake.__call__

    def spent_on_languages(
        method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        if url.endswith("/languages"):
            fake.calls.append((method, url, headers))
            return 403, SPENT, b"{}"
        return original(method, url, headers, body)

    github = source(spent_on_languages)  # type: ignore[arg-type]
    snapshot = github.fetch("ada")
    assert snapshot.status == "limited" and snapshot.language_totals == {}
