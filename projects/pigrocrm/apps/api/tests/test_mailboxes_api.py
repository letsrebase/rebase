"""The REST surface of IMAP and PEC mailboxes, over real HTTP (REB-1015), against a fake IMAP
server of the core suite's own: a real socket and a real TLS handshake, never a real account.

What only the adapter can show: a personal access token is refused on connecting, re-keying and
disconnecting whatever it carries, a password is never in an answer, and the connection options
are the dependency a test replaces (`imap_options`), so production's public-address rule is not
bypassed by a flag in the request.
"""

import base64
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.fake_imap import (  # noqa: E402
    HOST,
    PASSWORD,
    USERNAME,
    FakeImap,
    ServerState,
    loopback,
)

from pigrocrm.core.config import Settings, get_settings  # noqa: E402
from pigrocrm_api.routers.mailboxes import ImapOptions, imap_options  # noqa: E402

KEY = base64.b64encode(b"m" * 32).decode()


@pytest.fixture
def imap() -> Iterator[FakeImap]:
    fake = FakeImap(ServerState())
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def mailboxes_ready(logged_in: TestClient, imap: FakeImap) -> Iterator[TestClient]:
    logged_in.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,  # type: ignore[call-arg]
        mailbox_token_key=KEY,
    )
    logged_in.app.dependency_overrides[imap_options] = lambda: ImapOptions(
        resolve=loopback, context=imap.client_context()
    )
    yield logged_in
    logged_in.app.dependency_overrides.pop(get_settings, None)
    logged_in.app.dependency_overrides.pop(imap_options, None)


def _body(imap: FakeImap, **overrides: object) -> dict[str, object]:
    return {
        "address": USERNAME,
        "host": HOST,
        "port": imap.port,
        "security": "tls",
        "username": USERNAME,
        "password": PASSWORD,
        "purposes": ["billing"],
        "space_owned": True,
        **overrides,
    }


def _agent(client: TestClient, *, full_access: bool) -> TestClient:
    issued = client.post("/api/tokens", json={"name": "agente", "full_access": full_access})
    assert issued.status_code == 201, issued.text
    agent = TestClient(client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {issued.json()['token']}"
    return agent


def test_an_admin_connects_lists_re_keys_and_disconnects_a_mailbox(
    mailboxes_ready: TestClient, imap: FakeImap
) -> None:
    created = mailboxes_ready.post("/api/mailboxes/imap", json=_body(imap))
    assert created.status_code == 201, created.text
    mailbox = created.json()
    assert (mailbox["health"], mailbox["purposes"], mailbox["space_owned"]) == (
        "active",
        ["billing"],
        True,
    )
    assert PASSWORD not in created.text and "password" not in mailbox

    listed = mailboxes_ready.get("/api/mailboxes").json()
    assert [m["id"] for m in listed] == [mailbox["id"]]

    imap.state.password = "rotated"
    wrong = mailboxes_ready.put(f"/api/mailboxes/{mailbox['id']}/password", json={"password": "x"})
    assert wrong.status_code == 422 and "password" in wrong.json()["detail"]
    right = mailboxes_ready.put(
        f"/api/mailboxes/{mailbox['id']}/password", json={"password": "rotated"}
    )
    assert right.status_code == 200 and right.json()["health"] == "active"

    gone = mailboxes_ready.delete(f"/api/mailboxes/{mailbox['id']}")
    assert (gone.status_code, gone.json()["health"]) == (200, "disconnected")


def test_a_token_cannot_connect_change_or_disconnect_a_mailbox_even_with_full_access(
    mailboxes_ready: TestClient, imap: FakeImap
) -> None:
    mailbox = mailboxes_ready.post("/api/mailboxes/imap", json=_body(imap)).json()
    mailboxes_ready.put("/api/settings/space", json={"mcp_full_access": True})
    logins = imap.state.logins

    for agent in (
        _agent(mailboxes_ready, full_access=False),
        _agent(mailboxes_ready, full_access=True),
    ):
        for response in (
            agent.post("/api/mailboxes/imap", json=_body(imap, address="otra@studio.test")),
            agent.put(f"/api/mailboxes/{mailbox['id']}/password", json={"password": PASSWORD}),
            agent.delete(f"/api/mailboxes/{mailbox['id']}"),
        ):
            assert response.status_code == 403, response.text
            assert response.json()["code"] == "agent_forbidden"
    assert imap.state.logins == logins, "a refused token never reaches the server"


def test_a_plaintext_choice_and_a_host_that_is_not_public_are_refused(
    mailboxes_ready: TestClient, imap: FakeImap
) -> None:
    assert (
        mailboxes_ready.post("/api/mailboxes/imap", json=_body(imap, security="none")).status_code
        == 422
    )
    assert (
        mailboxes_ready.post("/api/mailboxes/imap", json=_body(imap, port=143)).status_code == 422
    ), "port 143 is not TLS"
    mailboxes_ready.app.dependency_overrides.pop(imap_options)
    refused = mailboxes_ready.post("/api/mailboxes/imap", json=_body(imap, host="localhost"))
    assert refused.status_code == 422, refused.text
    assert imap.state.logins == 0


def test_a_collaborator_connects_their_own_mailbox_and_not_the_spaces(
    collaborator_client: TestClient, imap: FakeImap
) -> None:
    collaborator_client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,  # type: ignore[call-arg]
        mailbox_token_key=KEY,
    )
    collaborator_client.app.dependency_overrides[imap_options] = lambda: ImapOptions(
        resolve=loopback, context=imap.client_context()
    )
    try:
        spaces = collaborator_client.post("/api/mailboxes/imap", json=_body(imap))
        assert spaces.status_code == 403, spaces.text
        own = collaborator_client.post(
            "/api/mailboxes/imap",
            json=_body(imap, space_owned=False, purposes=["correspondence"]),
        )
        assert own.status_code == 201, own.text
        assert [m["id"] for m in collaborator_client.get("/api/mailboxes").json()] == [
            own.json()["id"]
        ]
    finally:
        collaborator_client.app.dependency_overrides.pop(get_settings, None)
        collaborator_client.app.dependency_overrides.pop(imap_options, None)


def test_a_refused_body_does_not_echo_the_password(
    mailboxes_ready: TestClient, imap: FakeImap
) -> None:
    body = _body(imap)
    del body["host"]

    response = mailboxes_ready.post("/api/mailboxes/imap", json=body)

    assert response.status_code == 422
    assert PASSWORD not in response.text


def test_the_app_boots_without_the_mailbox_key_and_connecting_says_why(
    logged_in: TestClient, imap: FakeImap, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose passes `PIGROCRM_MAILBOX_TOKEN_KEY` as an empty string when it is unset. Nothing
    reads the key at boot (the boot validator and `create_app` never decode it), so every
    container starts; a connection is refused in words, before it reaches any server."""
    from pigrocrm.core.config import require_production_secrets

    monkeypatch.setenv("PIGROCRM_MAILBOX_TOKEN_KEY", "")
    require_production_secrets(
        Settings(_env_file=None, jwt_secret="j" * 40, mailbox_token_key="")  # type: ignore[call-arg]
    )
    settings = Settings(_env_file=None, mailbox_token_key="")  # type: ignore[call-arg]
    logged_in.app.dependency_overrides[get_settings] = lambda: settings
    logged_in.app.dependency_overrides[imap_options] = lambda: ImapOptions(
        resolve=loopback, context=imap.client_context()
    )
    try:
        refused = logged_in.post("/api/mailboxes/imap", json=_body(imap))
    finally:
        logged_in.app.dependency_overrides.pop(get_settings, None)
        logged_in.app.dependency_overrides.pop(imap_options, None)
    assert refused.status_code == 409
    assert "PIGROCRM_MAILBOX_TOKEN_KEY" in refused.json()["detail"]
    assert imap.state.logins == 0
