"""A freelancer's verified GitHub account, and what a work profile is checked against
(REB-1166 to REB-1169; design "Cross-check work profiles against a verified GitHub account",
2026-11-24).

A work profile is built on the person's machine, so every check of the intake tests the document
against itself. This module brings in a second witness: the GitHub account the person proved is
theirs by signing in to it from the member area. Three reads, all of counts and labels:

- **The link.** The OAuth round trip (`oauth.py`, purpose `link`) hands over a token for one
  moment. GitHub's numeric id and login are read with it; the token is then dropped, never stored.
- **The snapshot** (`GithubReader.snapshot`). Per calendar month of the 24 before the link:
  commits, pull requests opened, reviews and issues, as GitHub's contribution data counts them,
  and the technology labels of the person's own public repositories. Commits carry the date their
  author wrote, which can be anything (`git commit --date`); the other three are dated by GitHub
  when they happen, so they cannot be backdated. The person chose the scope: `public` is read
  with the hub's own token, as anybody could read it (GitHub's union of scopes means a token of
  the person's may carry `read:user` from an earlier link, so their token cannot promise public
  only); `private` with the person's token and its `read:user`, which adds private contributions
  to the monthly totals and never names a private repository.
- **The artifacts** (`GithubReader.check_artifact`). Each project's URL artifact that is a GitHub
  pull request or commit is read with the hub's token: who wrote it, whether it merged, and who
  merged it in whose repository.

`cross_check` puts the three beside the profile for the admin's review panel: counts and
matches, no score, read by no decision (decision row of 2026-11-24 in `DECISIONS.md`). No match
does not mean false: work on another forge, on a client's GitHub under the client's address, or
in private repositories the person did not share, is invisible here.
"""

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.db import SessionOpener
from rebase_core.errors import DomainError
from rebase_core.http import HttpCall
from rebase_core.models import ARTIFACT_OUTCOMES, FreelancerSource, GithubAccount, WorkProfile
from rebase_core.work_profiles.schemas import (
    WorkProfileCrossCheck,
    WorkProfileCrossCheckArtifact,
    WorkProfileCrossCheckMonth,
    WorkProfileCrossCheckTechnology,
    WorkProfileGithub,
)

logger = logging.getLogger(__name__)

API = "https://api.github.com"
GRAPHQL = f"{API}/graphql"
# Calendar months read at the link, the current one included.
SNAPSHOT_MONTHS = 24
# Artifacts checked per profile: a profile has a handful of projects; more is not a person's.
MAX_ARTIFACTS = 10
# A span in a profile longer than this is not read month by month (a hostile one could be
# ten thousand years).
MAX_SPAN_MONTHS = 240

# GitHub's language names (linguist) that are technology labels of the fork's stack detection
# (`work_profiles/tech-labels.json`). A label outside the values is a framework or a service
# GitHub cannot see from a repository's languages.
LANGUAGE_LABELS: dict[str, str] = {
    "Go": "Go",
    "HCL": "Terraform",
    "Java": "Java",
    "Kotlin": "Kotlin",
    "MDX": "MDX",
    "PHP": "PHP",
    "PLpgSQL": "SQL",
    "Python": "Python",
    "Ruby": "Ruby",
    "Rust": "Rust",
    "Shell": "Shell",
    "Svelte": "Svelte",
    "Swift": "Swift",
    "TSQL": "SQL",
    "TypeScript": "TypeScript",
    "Vue": "Vue",
}
VISIBLE_LABELS = frozenset(LANGUAGE_LABELS.values())

_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_NUMBER = re.compile(r"^[1-9][0-9]{0,9}$")
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_MONTH = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
# GitHub's `DateTime`, in UTC.
_STAMP = "%Y-%m-%dT%H:%M:%SZ"

_COUNTS = (
    "totalCommitContributions totalPullRequestContributions "
    "totalPullRequestReviewContributions totalIssueContributions"
)


class GithubUnavailable(Exception):
    """GitHub did not give an answer this flow can use. The message names the step."""


class GithubAccountTaken(DomainError):
    """The account is already linked to another freelancer's card."""

    code = "github_account_taken"


@dataclass(frozen=True)
class Identity:
    github_id: int
    login: str


@dataclass(frozen=True)
class Snapshot:
    activity: list[dict[str, Any]]
    languages: list[str]


def month_windows(now: datetime, months: int = SNAPSHOT_MONTHS) -> list[tuple[str, str, str]]:
    """The last `months` calendar months, oldest first, the current one included: the label
    `YYYY-MM` and the first and last instant of the month in UTC, as GitHub's `DateTime`."""
    windows: list[tuple[str, str, str]] = []
    year, month = now.year, now.month
    for _ in range(months):
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        start = datetime(year, month, 1, tzinfo=UTC)
        end = datetime(next_year, next_month, 1, tzinfo=UTC) - timedelta(seconds=1)
        windows.append((f"{year:04d}-{month:02d}", start.strftime(_STAMP), end.strftime(_STAMP)))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return list(reversed(windows))


def _snapshot_query(owner: str, windows: list[tuple[str, str, str]]) -> str:
    """One GraphQL query: the identity, one aliased contribution count per month, and the
    languages of the owner's public repositories that are not forks."""
    months = " ".join(
        f'm{index}: contributionsCollection(from: "{start}", to: "{end}") {{ {_COUNTS} }}'
        for index, (_label, start, end) in enumerate(windows)
    )
    repos = (
        "repositories(first: 100, ownerAffiliations: [OWNER], privacy: PUBLIC, isFork: false) "
        "{ nodes { languages(first: 10) { nodes { name } } } }"
    )
    return f"query($login: String!) {{ {owner} {{ databaseId login {months} {repos} }} }}"


def parse_artifact(url: str) -> tuple[str, str, str, str] | None:
    """`(owner, repo, kind, ref)` for a GitHub pull request (`kind` `pull`, `ref` its number) or
    commit (`commit`, the sha) URL, else `None`. A commit inside a pull request's page is the
    pull request."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in _GITHUB_HOSTS:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 4 or not _SEGMENT.match(segments[0]) or not _SEGMENT.match(segments[1]):
        return None
    owner, repo, kind, ref = segments[0], segments[1], segments[2], segments[3]
    if kind == "pull" and _NUMBER.match(ref):
        return owner, repo, "pull", ref
    if kind == "commit" and _SHA.match(ref):
        return owner, repo, "commit", ref.lower()
    return None


class GithubReader:
    """GitHub over the hub's HTTP seam. `hub_token` is the hub's own read-only token
    (`REBASE_GITHUB_TOKEN`): public data only, and the rate limit of an authenticated caller."""

    def __init__(self, http: HttpCall, hub_token: str, now: Callable[[], datetime] = utcnow):
        self.http = http
        self.hub_token = hub_token
        self._now = now

    def _call(self, step: str, method: str, url: str, token: str, body: bytes = b"") -> Any:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if body:
            headers["Content-Type"] = "application/json"
        try:
            status, raw = self.http(method, url, headers, body)
        except Exception as exc:  # noqa: BLE001 -- urllib raises URLError, timeouts and resets
            logger.warning("github %s did not answer: %s", step, type(exc).__name__)
            raise GithubUnavailable(step) from exc
        if status != 200:
            logger.warning("github %s answered %s", step, status)
            raise GithubUnavailable(step)
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise GithubUnavailable(step) from exc

    def _graphql(self, step: str, token: str, query: str, variables: dict[str, Any]) -> Any:
        body = json.dumps({"query": query, "variables": variables}).encode()
        answer = self._call(step, "POST", GRAPHQL, token, body)
        if not isinstance(answer, dict) or answer.get("errors") or not answer.get("data"):
            logger.warning("github %s answered errors", step)
            raise GithubUnavailable(step)
        return answer["data"]

    def identity(self, token: str) -> Identity:
        """Who the token belongs to: GitHub's numeric id and the login."""
        data = self._graphql("identity", token, "query { viewer { databaseId login } }", {})
        viewer = data.get("viewer") if isinstance(data, dict) else None
        github_id = viewer.get("databaseId") if isinstance(viewer, dict) else None
        login = viewer.get("login") if isinstance(viewer, dict) else None
        if not isinstance(github_id, int) or not isinstance(login, str) or not login:
            raise GithubUnavailable("identity")
        return Identity(github_id, login)

    def snapshot(self, identity: Identity, scope: str, person_token: str) -> Snapshot:
        """The 24 months and the languages. `private` reads as the person (`viewer`, their token
        with `read:user`); `public` reads the account as anybody would, with the hub's token."""
        windows = month_windows(self._now())
        if scope == "private":
            query = _snapshot_query("viewer", windows).replace("query($login: String!)", "query")
            data = self._graphql("snapshot", person_token, query, {})
            node = data.get("viewer")
        else:
            if not self.hub_token:
                logger.warning("github snapshot: no hub token to read public activity with")
                raise GithubUnavailable("snapshot")
            query = _snapshot_query("user(login: $login)", windows)
            data = self._graphql("snapshot", self.hub_token, query, {"login": identity.login})
            node = data.get("user")
        if not isinstance(node, dict) or node.get("databaseId") != identity.github_id:
            raise GithubUnavailable("snapshot")
        activity = []
        for index, (label, _start, _end) in enumerate(windows):
            counts = node.get(f"m{index}")
            if not isinstance(counts, dict):
                raise GithubUnavailable("snapshot")
            activity.append(
                {
                    "month": label,
                    "commits": _count(counts.get("totalCommitContributions")),
                    "pull_requests": _count(counts.get("totalPullRequestContributions")),
                    "reviews": _count(counts.get("totalPullRequestReviewContributions")),
                    "issues": _count(counts.get("totalIssueContributions")),
                }
            )
        return Snapshot(activity, _languages(node.get("repositories")))

    def check_artifact(self, url: str, github_id: int) -> str:
        """One of `ARTIFACT_OUTCOMES` for a project's URL artifact against the account."""
        parsed = parse_artifact(url)
        if parsed is None:
            return "not_checkable"
        if not self.hub_token:
            return "unavailable"
        owner, repo, kind, ref = parsed
        path = f"{API}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        path += f"/pulls/{ref}" if kind == "pull" else f"/commits/{ref}"
        headers = {
            "Authorization": f"Bearer {self.hub_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            status, raw = self.http("GET", path, headers, b"")
        except Exception as exc:  # noqa: BLE001 -- as `_call`
            logger.warning("github artifact did not answer: %s", type(exc).__name__)
            return "unavailable"
        # 301: the repository was renamed or moved, and the seam follows no redirect.
        if status in (301, 404, 410, 422):
            return "not_found"
        if status != 200:
            return "unavailable"
        try:
            answer = json.loads(raw)
        except ValueError:
            return "unavailable"
        if not isinstance(answer, dict):
            return "unavailable"
        if kind == "commit":
            return "commit" if _id_of(answer.get("author")) == github_id else "other_author"
        if _id_of(answer.get("user")) != github_id:
            return "other_author"
        if not answer.get("merged_at"):
            return "unmerged"
        merger = _id_of(answer.get("merged_by"))
        base = answer.get("base")
        repo_node = base.get("repo") if isinstance(base, dict) else None
        owner_id = _id_of(repo_node.get("owner")) if isinstance(repo_node, dict) else None
        if merger is not None and merger != github_id and owner_id != github_id:
            return "merged_by_other"
        return "merged"


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _id_of(node: object) -> int | None:
    value = node.get("id") if isinstance(node, dict) else None
    return value if isinstance(value, int) else None


def _languages(repositories: object) -> list[str]:
    nodes = repositories.get("nodes") if isinstance(repositories, dict) else None
    labels: set[str] = set()
    for repo in nodes if isinstance(nodes, list) else []:
        languages = repo.get("languages") if isinstance(repo, dict) else None
        names = languages.get("nodes") if isinstance(languages, dict) else None
        for language in names if isinstance(names, list) else []:
            name = language.get("name") if isinstance(language, dict) else None
            if isinstance(name, str) and name in LANGUAGE_LABELS:
                labels.add(LANGUAGE_LABELS[name])
    return sorted(labels)


def _artifact_urls(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """`(project id, url)` of every project's URL artifact, in order, at most `MAX_ARTIFACTS`."""
    found: list[tuple[str, str]] = []
    projects = payload.get("projects")
    for project in projects if isinstance(projects, list) else []:
        if not isinstance(project, dict):
            continue
        artifact = project.get("artifact")
        url = artifact.get("url") if isinstance(artifact, dict) else None
        project_id = project.get("id")
        if isinstance(url, str) and url.strip() and isinstance(project_id, str):
            found.append((project_id, url))
    return found[:MAX_ARTIFACTS]


def _months_between(first: str, last: str) -> Iterable[str]:
    year, month = int(first[:4]), int(first[5:])
    end = (int(last[:4]), int(last[5:]))
    for _ in range(MAX_SPAN_MONTHS):
        if (year, month) > end:
            return
        yield f"{year:04d}-{month:02d}"
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def claimed_months(payload: dict[str, Any]) -> list[str]:
    """The months the profile says the person worked in: every month between the first and the
    last of each project's span, the representative ones and the others. A span says when a
    project ran, not that every month in it had a session, so this is the most a profile
    claims, not the least."""
    months: set[str] = set()
    for key in ("projects", "otherProjects"):
        rows = payload.get(key)
        for row in rows if isinstance(rows, list) else []:
            span = row.get("span") if isinstance(row, dict) else None
            if not isinstance(span, dict):
                continue
            first, last = span.get("from"), span.get("to")
            if not (isinstance(first, str) and _MONTH.match(first)):
                continue
            last = last if isinstance(last, str) and _MONTH.match(last) else first
            months.update(_months_between(first, last))
    return sorted(months)


def cross_check(account: GithubAccount | None, profile: WorkProfile) -> WorkProfileCrossCheck:
    """What the admin's panel reads beside the profile. Empty lists without an account."""
    if account is None:
        return WorkProfileCrossCheck(
            github=None,
            months=[],
            months_outside=0,
            with_server_activity=0,
            with_commits_only=0,
            without_activity=0,
            technologies=[],
            artifacts=[],
            artifacts_checked_at=None,
        )
    by_month = {str(row.get("month")): row for row in account.activity if isinstance(row, dict)}
    months: list[WorkProfileCrossCheckMonth] = []
    outside = 0
    for month in claimed_months(profile.payload):
        row = by_month.get(month)
        if row is None:
            outside += 1
            continue
        server = _count(row.get("pull_requests")) + _count(row.get("reviews"))
        server += _count(row.get("issues"))
        months.append(
            WorkProfileCrossCheckMonth(
                month=month, server_dated=server, commits=_count(row.get("commits"))
            )
        )
    languages = set(account.languages)
    technologies = []
    usage = profile.payload.get("techUsage")
    for row in usage if isinstance(usage, list) else []:
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(name, str):
            continue
        if name not in VISIBLE_LABELS:
            status = "not_visible"
        else:
            status = "seen" if name in languages else "not_seen"
        technologies.append(WorkProfileCrossCheckTechnology(name=name, status=status))
    artifacts = [
        WorkProfileCrossCheckArtifact(project=str(c.get("project", "")), outcome=c["outcome"])
        for c in profile.artifact_checks
        if isinstance(c, dict) and c.get("outcome") in ARTIFACT_OUTCOMES
    ]
    return WorkProfileCrossCheck(
        github=WorkProfileGithub(
            login=account.login, scope=account.scope, linked_at=account.linked_at
        ),
        months=months,
        months_outside=outside,
        with_server_activity=sum(1 for m in months if m.server_dated > 0),
        with_commits_only=sum(1 for m in months if m.server_dated == 0 and m.commits > 0),
        without_activity=sum(1 for m in months if m.server_dated == 0 and m.commits == 0),
        technologies=technologies,
        artifacts=artifacts,
        artifacts_checked_at=profile.artifacts_checked_at,
    )


class GithubLinkService:
    """The link, the unlink and the artifact checks, on a `Session`."""

    def __init__(
        self, session: Session, reader: GithubReader, now: Callable[[], datetime] = utcnow
    ) -> None:
        self.session = session
        self.reader = reader
        self._now = now

    def account(self, freelancer_id: UUID) -> GithubAccount | None:
        return self.session.scalar(
            select(GithubAccount).where(GithubAccount.freelancer_id == freelancer_id)
        )

    def link(self, freelancer_id: UUID, token: str, scope: str) -> GithubAccount:
        """Reads who the token belongs to and the snapshot, then stores both on the card,
        replacing an earlier link. `GithubAccountTaken` when another card holds the account,
        `GithubUnavailable` when GitHub did not answer. The token is not kept."""
        identity = self.reader.identity(token)
        holder = self.session.scalar(
            select(GithubAccount.freelancer_id).where(GithubAccount.github_id == identity.github_id)
        )
        if holder is not None and holder != freelancer_id:
            raise GithubAccountTaken("Questo account GitHub è già collegato a un'altra scheda.")
        snapshot = self.reader.snapshot(identity, scope, token)
        row = self.account(freelancer_id)
        if row is None:
            row = GithubAccount(freelancer_id=freelancer_id)
            self.session.add(row)
        elif row.github_id != identity.github_id:
            self._clear_checks(freelancer_id)  # made against the account being replaced
        row.github_id = identity.github_id
        row.login = identity.login
        row.scope = scope
        row.linked_at = self._now()
        row.activity = snapshot.activity
        row.languages = snapshot.languages
        try:
            self.session.commit()
        except IntegrityError as error:  # a second card linked the same account meanwhile
            self.session.rollback()
            raise GithubAccountTaken(
                "Questo account GitHub è già collegato a un'altra scheda."
            ) from error
        return row

    def unlink(self, freelancer_id: UUID) -> None:
        """The account and what was read of it go at once: the snapshot, the artifact checks
        made against it and the card's stored GitHub source. Asking twice is not an error."""
        row = self.account(freelancer_id)
        if row is not None:
            self.session.delete(row)
        self.session.execute(
            delete(FreelancerSource).where(
                FreelancerSource.freelancer_id == freelancer_id, FreelancerSource.kind == "github"
            )
        )
        self._clear_checks(freelancer_id)
        self.session.commit()

    def _clear_checks(self, freelancer_id: UUID) -> None:
        profile = self._profile(freelancer_id)
        if profile is not None:
            profile.artifact_checks = []
            profile.artifacts_checked_at = None

    def _profile(self, freelancer_id: UUID) -> WorkProfile | None:
        return self.session.scalar(
            select(WorkProfile).where(WorkProfile.freelancer_id == freelancer_id)
        )

    def refresh_artifacts(self, freelancer_id: UUID) -> None:
        """Checks the profile's artifacts against the linked account and stores the outcomes;
        clears them when either is missing. GitHub is read before any row is locked."""
        profile = self._profile(freelancer_id)
        if profile is None:
            return
        account = self.account(freelancer_id)
        submitted_at = profile.submitted_at
        checked_for = account.github_id if account is not None else None
        if checked_for is None:
            checks: list[dict[str, str]] = []
            checked_at = None
        else:
            urls = _artifact_urls(profile.payload)
            self.session.rollback()  # no transaction held open across the network
            checks = [
                {"project": project, "outcome": self.reader.check_artifact(url, checked_for)}
                for project, url in urls
            ]
            checked_at = self._now()
        current = self._profile(freelancer_id)
        if current is None or current.submitted_at != submitted_at:
            return  # replaced or deleted meanwhile: its own check runs after its intake
        now_linked = self.account(freelancer_id)
        if (now_linked.github_id if now_linked is not None else None) != checked_for:
            return  # unlinked or relinked meanwhile: the outcomes are about another account
        current.artifact_checks = checks
        current.artifacts_checked_at = checked_at
        self.session.commit()


def refresh_artifacts_after(
    opener: SessionOpener, reader: GithubReader, freelancer_id: UUID
) -> None:
    """`refresh_artifacts` after a response, on a session of its own: a failure is logged and
    the admin's «Ricontrolla» runs it again."""
    try:
        with opener() as session:
            GithubLinkService(session, reader).refresh_artifacts(freelancer_id)
    except Exception:
        logger.exception("work profile artifacts of %s not checked", freelancer_id)


def drop_absorbed_account(session: Session, survivor_card: UUID, absorbed_card: UUID) -> None:
    """A merge of two cards (REB-800): the absorbed card's GitHub account is deleted, like its
    sources, and the survivor keeps its own; the person links again if the survivor had none.
    The survivor's work profile may have come from the absorbed card with checks made against
    the deleted account, so they are cleared and an admin checks again. Not committed here."""
    absorbed = session.scalar(
        select(GithubAccount).where(GithubAccount.freelancer_id == absorbed_card)
    )
    if absorbed is None:
        return
    session.delete(absorbed)
    profile = session.scalar(select(WorkProfile).where(WorkProfile.freelancer_id == survivor_card))
    if profile is not None:
        profile.artifact_checks = []
        profile.artifacts_checked_at = None
