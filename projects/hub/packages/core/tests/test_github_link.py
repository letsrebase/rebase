"""The verified GitHub account and the work profile's cross-check (REB-1166 to REB-1169): the
snapshot read with the right token for the scope the person chose, the artifacts' outcomes, the
link's rules and what the admin's panel is computed from."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from github_fakes import (
    GITHUB_ID,
    HUB_TOKEN,
    LOGIN,
    PERSON_TOKEN,
    FakeGithub,
    pull,
    pull_answer,
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from work_profile_helpers import person, sample

from rebase_core.github_link import (
    GithubAccountTaken,
    GithubLinkService,
    GithubReader,
    GithubUnavailable,
    Identity,
    claimed_months,
    cross_check,
    drop_absorbed_account,
    month_windows,
    parse_artifact,
)
from rebase_core.models import GithubAccount, WorkProfile

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
IDENTITY = Identity(GITHUB_ID, LOGIN)


def _reader(fake: FakeGithub, hub_token: str = HUB_TOKEN) -> GithubReader:
    return GithubReader(fake, hub_token, lambda: NOW)


def _profile(session: Session, freelancer_id: UUID, payload: dict[str, Any]) -> WorkProfile:
    row = WorkProfile(
        freelancer_id=freelancer_id,
        payload=payload,
        groundedness=90,
        unsupported_anchors=[],
        expires_at=NOW + timedelta(days=365),
    )
    session.add(row)
    session.commit()
    return row


def _with_artifacts(*urls: str) -> dict[str, Any]:
    payload = sample()
    for project, url in zip(payload["projects"], urls, strict=False):
        project["artifact"] = {"type": "url", "url": url}
    return payload


# ---- pure ----------------------------------------------------------------------------------


def test_the_snapshot_reads_the_24_calendar_months_up_to_the_current_one() -> None:
    windows = month_windows(NOW)

    assert len(windows) == 24
    assert windows[0] == ("2024-12", "2024-12-01T00:00:00Z", "2024-12-31T23:59:59Z")
    assert windows[-1] == ("2026-11", "2026-11-01T00:00:00Z", "2026-11-30T23:59:59Z")
    assert ("2025-02", "2025-02-01T00:00:00Z", "2025-02-28T23:59:59Z") in windows


@pytest.mark.parametrize(
    ("url", "parsed"),
    [
        ("https://github.com/acme/api/pull/12", ("acme", "api", "pull", "12")),
        ("https://github.com/acme/api/pull/12/files", ("acme", "api", "pull", "12")),
        ("https://www.github.com/acme/api/commit/ABCDEF1", ("acme", "api", "commit", "abcdef1")),
        ("https://github.com/acme/api/pull/0", None),
        ("https://github.com/acme/api/issues/3", None),
        ("https://gitlab.com/acme/api/-/merge_requests/1", None),
        ("https://github.com/acme", None),
        ("not a url", None),
    ],
)
def test_only_a_github_pull_request_or_commit_is_an_artifact_to_check(
    url: str, parsed: tuple[str, str, str, str] | None
) -> None:
    assert parse_artifact(url) == parsed


def test_the_claimed_months_are_every_month_of_every_project_span() -> None:
    payload = {
        "projects": [{"span": {"from": "2026-03", "to": "2026-05"}}, {"span": {"from": "2026-05"}}],
        "otherProjects": [{"span": {"from": "2025-12", "to": "2026-01"}}, {"span": "junk"}],
    }

    assert claimed_months(payload) == ["2025-12", "2026-01", "2026-03", "2026-04", "2026-05"]


def test_a_hostile_span_is_read_for_twenty_years_at_most() -> None:
    payload = {"projects": [{"span": {"from": "0001-01", "to": "9999-12"}}]}

    assert len(claimed_months(payload)) == 240


# ---- the snapshot ------------------------------------------------------------------------


def test_a_public_snapshot_is_read_with_the_hubs_token_never_the_persons() -> None:
    fake = FakeGithub()
    fake.public = {"2026-05": {"pull_requests": 2, "commits": 9}}
    fake.private = {"2026-05": {"pull_requests": 40}}

    snapshot = _reader(fake).snapshot(IDENTITY, "public", PERSON_TOKEN)

    assert fake.snapshot_tokens() == [HUB_TOKEN]
    may = next(row for row in snapshot.activity if row["month"] == "2026-05")
    assert may == {"month": "2026-05", "commits": 9, "pull_requests": 2, "reviews": 0, "issues": 0}
    assert snapshot.languages == ["Terraform", "TypeScript"]


def test_a_private_snapshot_reads_as_the_person_and_adds_their_private_counts() -> None:
    fake = FakeGithub()
    fake.public = {"2026-05": {"pull_requests": 2}}
    fake.private = {"2026-05": {"pull_requests": 40}}

    snapshot = _reader(fake).snapshot(IDENTITY, "private", PERSON_TOKEN)

    assert fake.snapshot_tokens() == [PERSON_TOKEN]
    may = next(row for row in snapshot.activity if row["month"] == "2026-05")
    assert may["pull_requests"] == 42
    assert [row["month"] for row in snapshot.activity] == [w[0] for w in month_windows(NOW)]


def test_no_snapshot_without_the_hubs_token_errors_or_another_account() -> None:
    with pytest.raises(GithubUnavailable):
        _reader(FakeGithub(), hub_token="").snapshot(IDENTITY, "public", PERSON_TOKEN)
    broken = FakeGithub()
    broken.graphql_errors = True
    with pytest.raises(GithubUnavailable):
        _reader(broken).snapshot(IDENTITY, "private", PERSON_TOKEN)
    renamed = FakeGithub()
    renamed.identity = {"databaseId": 1, "login": LOGIN}
    with pytest.raises(GithubUnavailable):
        _reader(renamed).snapshot(IDENTITY, "public", PERSON_TOKEN)


# ---- the artifacts -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        ((200, pull_answer(GITHUB_ID, merged_by=7, repo_owner=7)), "merged_by_other"),
        ((200, pull_answer(GITHUB_ID, merged_by=GITHUB_ID, repo_owner=7)), "merged"),
        ((200, pull_answer(GITHUB_ID, merged_by=7, repo_owner=GITHUB_ID)), "merged"),
        ((200, pull_answer(GITHUB_ID)), "unmerged"),
        ((200, pull_answer(7, merged_by=8)), "other_author"),
        ((404, {}), "not_found"),
        ((502, {}), "unavailable"),
        ((403, {}), "unavailable"),
    ],
)
def test_a_pull_request_is_checked_for_its_author_and_who_merged_it(
    answer: tuple[int, Any], outcome: str
) -> None:
    fake = FakeGithub()
    fake.rest[pull("acme", "api", 12)] = answer

    assert _reader(fake).check_artifact("https://github.com/acme/api/pull/12", GITHUB_ID) == outcome
    assert fake.token_of(fake.calls[-1][2]) == HUB_TOKEN


def test_a_commit_is_the_accounts_or_somebody_elses() -> None:
    fake = FakeGithub()
    url = "https://api.github.com/repos/acme/api/commits/abcdef1"
    fake.rest[url] = (200, {"author": {"id": GITHUB_ID}})
    reader = _reader(fake)

    assert (
        reader.check_artifact("https://github.com/acme/api/commit/abcdef1", GITHUB_ID) == "commit"
    )
    fake.rest[url] = (200, {"author": None})
    assert reader.check_artifact("https://github.com/acme/api/commit/abcdef1", GITHUB_ID) == (
        "other_author"
    )
    assert reader.check_artifact("https://ada.example/case", GITHUB_ID) == "not_checkable"


# ---- the link ----------------------------------------------------------------------------


def test_a_link_stores_the_account_and_its_snapshot_and_never_the_token(
    clean_hub: Session,
) -> None:
    _, card = person(clean_hub)
    assert card is not None
    fake = FakeGithub()
    fake.public = {"2026-04": {"reviews": 3}}

    GithubLinkService(clean_hub, _reader(fake), lambda: NOW).link(card, PERSON_TOKEN, "public")

    row = clean_hub.scalars(select(GithubAccount)).one()
    assert (row.github_id, row.login, row.scope, row.linked_at) == (GITHUB_ID, LOGIN, "public", NOW)
    assert len(row.activity) == 24 and row.languages == ["Terraform", "TypeScript"]
    stored = str((row.activity, row.languages, row.login))
    assert PERSON_TOKEN not in stored and HUB_TOKEN not in stored


def test_an_account_linked_to_one_card_cannot_be_linked_to_another(clean_hub: Session) -> None:
    _, first = person(clean_hub)
    _, second = person(clean_hub, "grace@studio.it")
    assert first is not None and second is not None
    service = GithubLinkService(clean_hub, _reader(FakeGithub()), lambda: NOW)
    service.link(first, PERSON_TOKEN, "public")

    with pytest.raises(GithubAccountTaken):
        service.link(second, PERSON_TOKEN, "public")
    service.link(first, PERSON_TOKEN, "private")  # the same card linking again replaces

    rows = clean_hub.scalars(select(GithubAccount)).all()
    assert [(r.freelancer_id, r.scope) for r in rows] == [(first, "private")]


def test_artifacts_are_checked_against_the_account_and_cleared_by_the_unlink(
    clean_hub: Session,
) -> None:
    _, card = person(clean_hub)
    assert card is not None
    fake = FakeGithub()
    fake.rest[pull("acme", "api", 12)] = (200, pull_answer(GITHUB_ID, merged_by=7, repo_owner=7))
    profile = _profile(
        clean_hub,
        card,
        _with_artifacts("https://github.com/acme/api/pull/12", "https://ada.example/case"),
    )
    service = GithubLinkService(clean_hub, _reader(fake), lambda: NOW)

    service.refresh_artifacts(card)  # no account: nothing to check against
    clean_hub.refresh(profile)
    assert profile.artifact_checks == [] and profile.artifacts_checked_at is None

    service.link(card, PERSON_TOKEN, "public")
    service.refresh_artifacts(card)
    clean_hub.refresh(profile)
    assert profile.artifact_checks == [
        {"project": "p1", "outcome": "merged_by_other"},
        {"project": "p2", "outcome": "not_checkable"},
    ]
    assert profile.artifacts_checked_at == NOW

    service.unlink(card)
    clean_hub.refresh(profile)
    assert clean_hub.scalars(select(GithubAccount)).all() == []
    assert profile.artifact_checks == [] and profile.artifacts_checked_at is None
    service.unlink(card)  # asking twice is not an error


# ---- what the admin's panel reads --------------------------------------------------------


def test_the_cross_check_sets_claimed_months_languages_and_artifacts_beside_the_account() -> None:
    account = GithubAccount(
        login=LOGIN,
        scope="private",
        linked_at=NOW,
        activity=[
            {"month": "2026-03", "commits": 0, "pull_requests": 2, "reviews": 1, "issues": 0},
            {"month": "2026-04", "commits": 5, "pull_requests": 0, "reviews": 0, "issues": 0},
            {"month": "2026-05", "commits": 0, "pull_requests": 0, "reviews": 0, "issues": 0},
        ],
        languages=["TypeScript"],
    )
    profile = WorkProfile(
        payload={
            "projects": [{"id": "p1", "span": {"from": "2026-02", "to": "2026-05"}}],
            "techUsage": [{"name": "TypeScript"}, {"name": "Python"}, {"name": "Zod"}],
        },
        artifact_checks=[{"project": "p1", "outcome": "merged"}, {"project": "p2", "outcome": "x"}],
        artifacts_checked_at=NOW,
    )

    check = cross_check(account, profile)

    assert check.github is not None and check.github.login == LOGIN
    assert [(m.month, m.server_dated, m.commits) for m in check.months] == [
        ("2026-03", 3, 0),
        ("2026-04", 0, 5),
        ("2026-05", 0, 0),
    ]
    assert (check.months_outside, check.with_server_activity) == (1, 1)
    assert (check.with_commits_only, check.without_activity) == (1, 1)
    assert [(t.name, t.status) for t in check.technologies] == [
        ("TypeScript", "seen"),
        ("Python", "not_seen"),
        ("Zod", "not_visible"),
    ]
    assert [(a.project, a.outcome) for a in check.artifacts] == [("p1", "merged")]


def test_without_an_account_the_cross_check_is_empty() -> None:
    check = cross_check(None, WorkProfile(payload=sample(), artifact_checks=[]))

    assert check.github is None and check.months == [] and check.technologies == []


def test_a_merge_drops_the_absorbed_cards_account_and_the_checks_made_against_it(
    clean_hub: Session,
) -> None:
    _, survivor = person(clean_hub)
    _, absorbed = person(clean_hub, "grace@studio.it")
    assert survivor is not None and absorbed is not None
    GithubLinkService(clean_hub, _reader(FakeGithub()), lambda: NOW).link(
        absorbed, PERSON_TOKEN, "public"
    )
    profile = _profile(clean_hub, survivor, sample())
    profile.artifact_checks = [{"project": "p1", "outcome": "merged"}]
    profile.artifacts_checked_at = NOW
    clean_hub.commit()

    drop_absorbed_account(clean_hub, survivor, absorbed)
    clean_hub.commit()

    assert clean_hub.scalars(select(GithubAccount)).all() == []
    clean_hub.refresh(profile)
    assert profile.artifact_checks == [] and profile.artifacts_checked_at is None
