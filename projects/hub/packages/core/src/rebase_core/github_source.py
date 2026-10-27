"""The GitHub source of the card: a person's public profile and the repositories they own.

REB-837 (design 2026-10-27, § Sources). Read through GitHub's REST API, public data only,
with the hub's read-only token when one is configured (`REBASE_GITHUB_TOKEN`: it lifts
the rate limit, it gives access to nothing). Three calls to learn who someone is
(`/users/{login}`, `/users/{login}/repos`) and one per top repository for its languages.

What the snapshot says and does not say:

* The repositories are listed **with their names**: the names are what the anonymity guard
  compares the card against (`cards._identifying`), and the model is told never to write
  them. A fork and an archived repository are marked and sorted last: they are somebody
  else's work or a finished one.
* **No account age.** `created_at` is never read into the text: a ten-year-old account is
  not ten years of professional experience, and `anni` comes only from dated roles
  (design, «Calls made after the builder check»).
* `hash_material()` is the same facts **without anything that moves on its own**: stars,
  last push, language byte counts, the activity bucket, the public-repository count. A card
  is stale when a source's hash differs, and a star must not make it so. The material is
  ordered by name, not by weight, for the same reason: stars decide the weight.

A missing user, a refusal or a network error is a snapshot with a `status`, not an
exception: the weekly refetch must not stop at one person.

**The rate limit is the pass's, not the person's** (REB-844). Without a token GitHub allows
60 requests an hour, and a person costs up to seven. Every answer's `x-ratelimit-remaining`
and `x-ratelimit-reset` are read (and `retry-after` on a 403 or 429): when the budget
reaches zero, or GitHub refuses for it, the source remembers until when and answers
`limited` to every `fetch` before that moment **without calling GitHub**, so the pass
spends nothing more and `SourceService` writes nothing for the people it did not reach
(no `error` row, no backoff): the next pass picks them up.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote, urlsplit

from rebase_core.audit import utcnow
from rebase_core.http import MAX_BODY_BYTES, NETWORK_ERROR_STATUS, HttpHeadersCall

API = "https://api.github.com"
# Repositories whose languages are asked for, one call each: the heaviest few say what a
# person writes; the long tail would cost a call apiece for nothing.
LANGUAGE_REPOS = 5
# Repositories named in the text. The list call returns up to 100.
MAX_REPOS_LISTED = 30
MAX_DESCRIPTION_CHARS = 200
MAX_BIO_CHARS = 400
# How long the source stays silent when GitHub refuses for the rate limit and says nothing
# about when it lifts, and the least it stays silent when what it says is already past
# (a clock that differs from GitHub's): never zero, or every person of the pass would be
# one more refused call.
UNANNOUNCED_LIMIT = timedelta(hours=1)
LEAST_SILENCE = timedelta(minutes=1)

# First path segments on github.com that are pages of the site, not accounts.
_RESERVED = frozenset(
    {
        "about", "apps", "blog", "codespaces", "collections", "contact", "customer-stories",
        "enterprise", "events", "explore", "features", "gist", "issues", "join", "login",
        "logout", "marketplace", "new", "notifications", "orgs", "organizations", "pricing",
        "pulls", "readme", "resources", "search", "security", "sessions", "settings",
        "site", "solutions", "sponsors", "team", "topics", "trending", "users",
    }
)  # fmt: skip
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_HOSTS = frozenset({"github.com", "www.github.com"})

# `limited`: GitHub's budget is spent, nothing was learnt about this person and nothing is
# to be written about them. Never stored: `freelancer_sources.status` has no such value.
GithubStatus = Literal["ok", "unreadable", "error", "limited"]


class _Limited(Exception):
    """The rate limit is spent: raised inside `GithubSource` only, caught by `fetch`."""


def github_login_from_links(links: list[str]) -> str | None:
    """The first account a list of links names: `github.com/<login>` or one of its
    repositories (`github.com/<login>/<repo>`, whose owner is the account). Site pages
    such as `/orgs/...` and `/sponsors/...` name no one. A `<login>.github.io` site is a
    page, read as any other link, not an account."""
    for link in links:
        parts = urlsplit(link.strip())
        if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in _HOSTS:
            continue
        segments = [segment for segment in parts.path.split("/") if segment]
        if not segments:
            continue
        login = segments[0]
        if login.lower() in _RESERVED or not _LOGIN.match(login):
            continue
        return login
    return None


@dataclass(frozen=True)
class Repo:
    name: str
    description: str
    language: str | None
    fork: bool
    archived: bool
    stars: int
    pushed_at: str | None


@dataclass(frozen=True)
class GithubSnapshot:
    login: str
    bio: str = ""
    repos: list[Repo] = field(default_factory=list)
    language_totals: dict[str, int] = field(default_factory=dict)
    contribution_recency: str = ""
    volatile: dict[str, int] = field(default_factory=dict)
    status: GithubStatus = "ok"

    def by_weight(self) -> list[Repo]:
        """Own work first, then forks and archived; stars and recency within each."""
        return sorted(
            self.repos,
            key=lambda r: (
                r.fork or r.archived,
                -r.stars,
                _recent_first(r.pushed_at),
                r.name.lower(),
            ),
        )

    def normalised_text(self) -> str:
        lines = ["FONTE: GitHub", f"Utente: {self.login}"]
        if self.bio:
            lines.append(f"Bio: {self.bio}")
        if self.contribution_recency:
            lines.append(f"Attività: {self.contribution_recency}")
        if self.language_totals:
            total = sum(self.language_totals.values()) or 1
            ranked = sorted(self.language_totals.items(), key=lambda item: (-item[1], item[0]))
            shares = ", ".join(f"{name} {round(100 * size / total)}%" for name, size in ranked[:6])
            lines.append(f"Linguaggi (per quantità di codice): {shares}")
        count = self.volatile.get("public_repos")
        header = "Repository" if count is None else f"Repository pubblici: {count}. Elenco"
        listed = self.by_weight()[:MAX_REPOS_LISTED]
        if listed:
            lines.append(f"{header}:")
        for repo in listed:
            marks = [m for m, on in (("fork", repo.fork), ("archiviato", repo.archived)) if on]
            detail = [repo.language] if repo.language else []
            detail.append(f"{repo.stars} stelle")
            tail = f" [{', '.join(marks)}]" if marks else ""
            description = f": {repo.description}" if repo.description else ""
            lines.append(f"- {repo.name}{tail}{description} ({'; '.join(detail)})")
        return "\n".join(lines)

    def hash_material(self) -> str:
        """The facts that are the person's, in an order that does not depend on how
        popular they are: the bio, and each repository's name, description, main language
        and whether it is a fork or archived."""
        lines = ["FONTE: GitHub", f"Utente: {self.login}", f"Bio: {self.bio}"]
        for repo in sorted(self.repos, key=lambda r: r.name.lower()):
            flags = f"fork={int(repo.fork)} archiviato={int(repo.archived)}"
            lines.append(f"- {repo.name} | {repo.description} | {repo.language or ''} | {flags}")
        return "\n".join(lines)


_LISTED_REPO = re.compile(r"^- (?P<name>[A-Za-z0-9._-]+)(?: \[(?P<marks>[^\]]*)\])?", re.MULTILINE)


def owned_repo_names(text: str) -> list[str]:
    """The names of the repositories a stored GitHub source (`normalised_text`) lists as
    the person's own: every one but the forks, which are somebody else's project and
    carry its name (`django`), not the person's. What the anonymity guard compares a card
    against, read back from the text rather than stored twice."""
    return [
        hit["name"]
        for hit in _LISTED_REPO.finditer(text)
        if "fork" not in (hit["marks"] or "").split(", ")
    ]


def _recent_first(timestamp: str | None) -> float:
    """A sort key that puts the most recently pushed first."""
    parsed = _parse_time(timestamp)
    return -parsed.timestamp() if parsed else 0.0


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _recency(pushed: list[str | None], now: datetime) -> str:
    """How recently the person's own code moved, in buckets: a date or a count would make
    every refetch a different text."""
    times = [t for t in (_parse_time(p) for p in pushed) if t is not None]
    if not times:
        return ""
    age = now - max(times)
    if age <= timedelta(days=90):
        return "codice pubblicato negli ultimi 3 mesi"
    if age <= timedelta(days=365):
        return "codice pubblicato nell'ultimo anno"
    return "nessun codice pubblicato da oltre un anno"


def _clean(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


class GithubSource:
    def __init__(
        self, http: HttpHeadersCall, token: str = "", now: Callable[[], datetime] = utcnow
    ) -> None:
        self._http = http
        self._token = token
        self._now = now
        self._silent_until: datetime | None = None

    def blocked(self) -> bool:
        """Whether the rate limit is spent right now: `fetch` would answer `limited`
        without calling GitHub."""
        return self._silent_until is not None and self._now() < self._silent_until

    def _get(self, path: str) -> tuple[int, object]:
        if self.blocked():
            raise _Limited
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            status, answered, body = self._http("GET", f"{API}{path}", headers, b"")
        except Exception:
            # `urllib_call` only unwraps an HTTP error: a timeout, a reset or a refused
            # connection raises. It travels as the network's status, never as an exception,
            # so one flaky night does not stop the pass or a card's write.
            return NETWORK_ERROR_STATUS, None
        if self._spent(status, answered) and status in (403, 429):
            raise _Limited
        # A 200 that took the last of the budget is answered; `blocked()` stops the next call.
        if status != 200 or len(body) > MAX_BODY_BYTES:
            return status, None
        try:
            return status, json.loads(body)
        except ValueError:
            return status, None

    def _spent(self, status: int, answered: dict[str, str]) -> bool:
        """Reads the rate limit off an answer and, when it is spent, remembers until when.
        Spent means: nothing left (`x-ratelimit-remaining: 0`), a 429, or a 403 that names
        a `retry-after` (GitHub's secondary limit). A bare 403 is a refusal, not a limit."""
        remaining = _whole(answered.get("x-ratelimit-remaining"))
        retry_after = _whole(answered.get("retry-after"))
        reset = _whole(answered.get("x-ratelimit-reset"))
        if not (remaining == 0 or status == 429 or (status == 403 and retry_after is not None)):
            return False
        now = self._now()
        if retry_after is not None:
            until = now + timedelta(seconds=retry_after)
        elif reset is not None:
            until = datetime.fromtimestamp(reset, UTC)
        else:
            until = now + UNANNOUNCED_LIMIT
        self._silent_until = max(until, now + LEAST_SILENCE)
        return True

    def fetch(self, login: str) -> GithubSnapshot:
        """The public profile and repositories of `login`. Never raises for GitHub's
        answer: a missing user, an organisation (a person's account is a `User`) and a page
        GitHub says is gone are `unreadable`; a refusal, a network or server failure are
        `error`, which keeps the text read before; a spent rate limit, before the call or
        in the middle of it, is `limited` and keeps everything as it was."""
        if not _LOGIN.match(login):
            return GithubSnapshot(login, status="unreadable")
        try:
            return self._read(login)
        except _Limited:
            return GithubSnapshot(login, status="limited")

    def _read(self, login: str) -> GithubSnapshot:
        who = quote(login, safe="")
        status, profile = self._get(f"/users/{who}")
        if not isinstance(profile, dict):
            return GithubSnapshot(login, status=_failure(status))
        if profile.get("type") != "User":
            return GithubSnapshot(login, status="unreadable")
        status, listing = self._get(f"/users/{who}/repos?type=owner&per_page=100&sort=pushed")
        if not isinstance(listing, list):
            return GithubSnapshot(login, status=_failure(status))
        repos = [repo for item in listing if (repo := _repo(item, login)) is not None]
        snapshot = GithubSnapshot(
            login=login,
            bio=_clean(profile.get("bio"), MAX_BIO_CHARS),
            repos=repos,
            contribution_recency=_recency(
                [r.pushed_at for r in repos if not r.fork and not r.archived], self._now()
            ),
            volatile={"public_repos": len(repos)},
        )
        totals: dict[str, int] = {}
        own = [repo for repo in snapshot.by_weight() if not repo.fork and not repo.archived]
        for repo in own[:LANGUAGE_REPOS]:
            _, languages = self._get(f"/repos/{who}/{quote(repo.name, safe='')}/languages")
            if isinstance(languages, dict):
                for name, size in languages.items():
                    if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                        totals[str(name)] = totals.get(str(name), 0) + size
        return replace(snapshot, language_totals=totals)


def _whole(value: str | None) -> int | None:
    """A header that is a whole number of seconds, or `None`."""
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _failure(status: int) -> GithubStatus:
    """What GitHub's answer says about the account. A missing user, a removed one and one
    withheld for legal reasons (404, 410, 451) will not come back by asking again:
    `unreadable`. Any other refusal (a bare 403 among them: the rate limit is `limited`,
    raised before this is asked), a dead network and GitHub's own 5xx are the day's, not
    the account's: `error`, so the text read before stays and the next pass asks again.
    (A 200 whose body is not an object is not the account's either way: `unreadable`.)"""
    if status in (200, 404, 410, 451):
        return "unreadable"
    return "error"


def _repo(item: object, login: str) -> Repo | None:
    """One repository the person owns and shows publicly, or `None`."""
    if not isinstance(item, dict):
        return None
    owner = item.get("owner")
    owner_login = owner.get("login") if isinstance(owner, dict) else None
    name = item.get("name")
    if not isinstance(name, str) or not isinstance(owner_login, str):
        return None
    if owner_login.lower() != login.lower() or item.get("private") or item.get("disabled"):
        return None
    language = item.get("language")
    stars = item.get("stargazers_count")
    return Repo(
        name=name,
        description=_clean(item.get("description"), MAX_DESCRIPTION_CHARS),
        language=language if isinstance(language, str) else None,
        fork=bool(item.get("fork")),
        archived=bool(item.get("archived")),
        stars=stars if isinstance(stars, int) and not isinstance(stars, bool) else 0,
        pushed_at=item["pushed_at"] if isinstance(item.get("pushed_at"), str) else None,
    )
