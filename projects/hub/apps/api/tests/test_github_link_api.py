"""«Collega GitHub» over HTTP (REB-1166 to REB-1169): the start from the member area, the
sign-in's callback telling a link apart, the unlink, and the admin's cross-check, against GitHub
as a fake HTTP seam."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from contract_api_helpers import ADMIN_EMAIL, login_as
from fastapi.testclient import TestClient
from github_fakes import GITHUB_ID, HUB_TOKEN, LOGIN, PERSON_TOKEN, FakeGithub, pull, pull_answer
from sqlalchemy import select
from sqlalchemy.orm import Session
from work_profile_api_helpers import CARD_EMAIL, apply_card
from work_profile_helpers import sample

from rebase_api.deps import get_http_call, get_session_opener
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer, GithubAccount, User, WorkProfile

HUB = "https://preview.letsrebase.com/hub"
LINK = "/api/hub/auth/oauth/github/link"
CALLBACK = "/api/hub/auth/oauth/github/callback"
PR = "https://github.com/acme/api/pull/12"


@pytest.fixture
def github(client: TestClient, api_session: Session) -> FakeGithub:
    fake = FakeGithub()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        github_oauth_client_id="gh-id",
        github_oauth_client_secret="gh-secret",
        github_token=HUB_TOKEN,
        hub_url=HUB,
    )
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_settings] = lambda: settings
    overrides[get_http_call] = lambda: fake
    overrides[get_session_opener] = lambda: lambda: nullcontext(api_session)
    return fake


@pytest.fixture
def ada(client: TestClient, sender: RecordingSender, github: FakeGithub) -> None:
    apply_card(client)
    login_as(client, sender, CARD_EMAIL)


def _go(client: TestClient, path: str) -> httpx.Response:
    return client.get(path, follow_redirects=False)


def _link(client: TestClient, scope: str = "public") -> httpx.Response:
    started = _go(client, f"{LINK}?scope={scope}")
    assert started.status_code == 302, started.text
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    return _go(client, CALLBACK + "?" + urlencode({"code": "c1", "state": state}))


def _card(api_session: Session) -> Freelancer:
    return api_session.scalars(select(Freelancer)).one()


def test_a_freelancer_links_github_sharing_private_counts_when_they_chose_to(
    client: TestClient, api_session: Session, github: FakeGithub, ada: None
) -> None:
    started = _go(client, f"{LINK}?scope=private")
    query = parse_qs(urlsplit(started.headers["location"]).query)
    assert query["scope"] == ["read:user"]
    assert query["redirect_uri"] == ["https://preview.letsrebase.com" + CALLBACK]

    done = _go(client, CALLBACK + "?" + urlencode({"code": "c1", "state": query["state"][0]}))

    assert done.headers["location"] == f"{HUB}/me?github=linked"
    me = client.get("/api/hub/me").json()
    assert me["github"]["login"] == LOGIN and me["github"]["scope"] == "private"
    assert github.snapshot_tokens() == [PERSON_TOKEN]
    api_session.expire_all()
    row = api_session.scalars(select(GithubAccount)).one()
    assert row.github_id == GITHUB_ID and PERSON_TOKEN not in str(row.activity)


def test_a_public_link_asks_github_for_nothing_and_reads_with_the_hubs_token(
    client: TestClient, github: FakeGithub, ada: None
) -> None:
    started = _go(client, LINK)
    assert "scope" not in parse_qs(urlsplit(started.headers["location"]).query)

    assert _link(client).headers["location"] == f"{HUB}/me?github=linked"
    assert client.get("/api/hub/me").json()["github"]["scope"] == "public"
    assert github.snapshot_tokens() == [HUB_TOKEN]


def test_a_scope_unticked_on_githubs_page_links_as_public(
    client: TestClient, github: FakeGithub, ada: None
) -> None:
    github.granted = "user:email"

    _link(client, "private")

    assert client.get("/api/hub/me").json()["github"]["scope"] == "public"
    assert github.snapshot_tokens() == [HUB_TOKEN]


def test_the_link_needs_a_session_and_a_card(
    client: TestClient, api_session: Session, sender: RecordingSender, github: FakeGithub
) -> None:
    assert _go(client, LINK).headers["location"] == f"{HUB}/login"
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    login_as(client, sender, ADMIN_EMAIL)

    assert _go(client, LINK).headers["location"] == f"{HUB}/me?github=no_card"


def test_a_callback_without_the_session_that_started_it_links_nothing(
    client: TestClient, api_session: Session, github: FakeGithub, ada: None
) -> None:
    started = _go(client, LINK)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    client.cookies.delete("rebase_user")

    done = _go(client, CALLBACK + "?" + urlencode({"code": "c1", "state": state}))

    assert done.headers["location"] == f"{HUB}/me?github=expired"
    assert api_session.scalars(select(GithubAccount)).all() == []


def test_an_account_another_card_holds_is_refused(
    client: TestClient, api_session: Session, github: FakeGithub, ada: None
) -> None:
    other = User(email="grace@studio.it", first_name="Grace", last_name="Hopper")
    api_session.add(other)
    api_session.flush()
    card = Freelancer(user_id=other.id)
    api_session.add(card)
    api_session.flush()
    api_session.add(
        GithubAccount(freelancer_id=card.id, github_id=GITHUB_ID, login=LOGIN, scope="public")
    )
    api_session.commit()

    assert _link(client).headers["location"] == f"{HUB}/me?github=taken"
    assert client.get("/api/hub/me").json()["github"] is None


def test_a_github_that_does_not_answer_links_nothing(
    client: TestClient, api_session: Session, github: FakeGithub, ada: None
) -> None:
    github.graphql_errors = True

    assert _link(client).headers["location"] == f"{HUB}/me?github=failed"
    assert api_session.scalars(select(GithubAccount)).all() == []


def test_unlinking_removes_the_account_and_asking_twice_is_fine(
    client: TestClient, api_session: Session, github: FakeGithub, ada: None
) -> None:
    _link(client)

    first = client.delete("/api/hub/me/github")
    second = client.delete("/api/hub/me/github")

    assert first.status_code == 200 and first.json()["github"] is None
    assert second.status_code == 200
    assert api_session.scalars(select(GithubAccount)).all() == []


def test_the_admin_reads_the_cross_check_and_can_check_the_artifacts_again(
    client: TestClient,
    api_session: Session,
    sender: RecordingSender,
    github: FakeGithub,
    ada: None,
) -> None:
    card = _card(api_session)
    payload = sample()
    payload["projects"][0]["artifact"] = {"type": "url", "url": PR}
    api_session.add(
        WorkProfile(
            freelancer_id=card.id,
            payload=payload,
            groundedness=90,
            unsupported_anchors=[],
            expires_at=datetime.now(UTC) + timedelta(days=300),
        )
    )
    api_session.commit()
    github.rest[pull("acme", "api", 12)] = (502, {})
    _link(client)  # the artifacts are checked after the answer: GitHub fails this time
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    login_as(client, sender, ADMIN_EMAIL)
    url = f"/api/hub/freelancers/{card.id}/work-profile"

    first = client.get(url).json()["cross_check"]
    github.rest[pull("acme", "api", 12)] = (200, pull_answer(GITHUB_ID, merged_by=7, repo_owner=7))
    again = client.post(f"{url}/cross-check")

    assert first["github"]["login"] == LOGIN
    assert first["artifacts"] == [{"project": "p1", "outcome": "unavailable"}]
    assert again.status_code == 200
    assert again.json()["cross_check"]["artifacts"] == [
        {"project": "p1", "outcome": "merged_by_other"}
    ]
    nobody = "/api/hub/freelancers/00000000-0000-0000-0000-000000000000/work-profile/cross-check"
    assert client.post(nobody).status_code == 404
