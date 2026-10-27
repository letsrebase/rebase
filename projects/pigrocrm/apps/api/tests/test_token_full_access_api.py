"""The privileged surface is chosen per token, over HTTP (REB-810).

`Settings.mcp_full_access` (Impostazioni → Spazio) is the ceiling; each token carries its own
`full_access`, chosen when it is made and changeable by its owner and by an administrator.
The REST surface is where this has to hold without any tool in front of it: the same
`Bearer pgc_...` is accepted on every route.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from pigrocrm_api.deps import invalidate_space_settings


@pytest.fixture
def open_space(logged_in: TestClient) -> TestClient:
    """The space's switch opened by its admin, through the page that holds it."""
    invalidate_space_settings(None)
    response = logged_in.put("/api/settings/space", json={"mcp_full_access": True})
    assert response.status_code == 200, response.text
    return logged_in


def _bearer(client: TestClient, raw: str) -> TestClient:
    agent = TestClient(client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {raw}"
    return agent


def _make(client: TestClient, nome: str, **body: Any) -> dict[str, Any]:
    response = client.post("/api/tokens", json={"nome": nome, **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_token_is_made_closed_unless_asked_and_the_list_says_so(open_space: TestClient) -> None:
    plain = _make(open_space, "chiacchiere")
    full = _make(open_space, "fatturazione", full_access=True)

    assert plain["full_access"] is False
    assert full["full_access"] is True
    listed = {token["nome"]: token["full_access"] for token in open_space.get("/api/tokens").json()}
    assert listed == {"chiacchiere": False, "fatturazione": True}


def test_asking_for_full_access_on_a_space_that_did_not_open_it_is_refused(
    logged_in: TestClient,
) -> None:
    invalidate_space_settings(None)
    assert logged_in.get("/api/tokens/options").json() == {"full_access_available": False}

    refused = logged_in.post("/api/tokens", json={"nome": "fatturazione", "full_access": True})

    assert refused.status_code == 422, refused.text
    assert "Impostazioni → Spazio" in refused.text
    assert logged_in.get("/api/tokens").json() == []


def test_any_member_is_told_whether_the_space_offers_it(
    open_space: TestClient, collaborator_client: TestClient
) -> None:
    """The settings page that holds the switch is an admin's; the box in the dialog is
    every member's, so its state is not."""
    assert collaborator_client.get("/api/tokens/options").json() == {"full_access_available": True}


def test_a_token_without_the_flag_is_refused_a_fiscal_act_over_rest_and_one_with_it_is_not(
    open_space: TestClient,
) -> None:
    """The card's own check on the REST side: one admin, one open space, two tokens. The
    same `POST /api/period-locks` (closing a month, which no tool-less caller should reach
    by accident) is `agent_forbidden` for the one and answered for the other."""
    closed = _make(open_space, "chiacchiere")["token"]
    opened = _make(open_space, "fatturazione", full_access=True)["token"]

    refused = _bearer(open_space, closed).post("/api/period-locks", json={"anno": 2026, "mese": 1})
    allowed = _bearer(open_space, opened).post("/api/period-locks", json={"anno": 2026, "mese": 1})

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "agent_forbidden"
    assert allowed.status_code == 201, allowed.text


def test_the_owner_closes_a_token_and_the_next_call_follows(open_space: TestClient) -> None:
    created = _make(open_space, "fatturazione", full_access=True)
    agent = _bearer(open_space, created["token"])
    assert agent.post("/api/period-locks", json={"anno": 2026, "mese": 2}).status_code == 201

    closed = open_space.patch(f"/api/tokens/{created['id']}", json={"full_access": False})

    assert closed.status_code == 200, closed.text
    assert closed.json()["full_access"] is False
    assert agent.post("/api/period-locks", json={"anno": 2026, "mese": 3}).status_code == 403


def test_a_token_cannot_open_itself_nor_mint_an_open_one(open_space: TestClient) -> None:
    """An agent credential is accepted on `PATCH /api/tokens/{id}` and `POST /api/tokens`
    like on any route; neither may hand the privileged surface to an agent."""
    created = _make(open_space, "chiacchiere")
    agent = _bearer(open_space, created["token"])

    opened = agent.patch(f"/api/tokens/{created['id']}", json={"full_access": True})
    minted = agent.post("/api/tokens", json={"nome": "figlio", "full_access": True})

    assert opened.status_code == 403, opened.text
    assert opened.json()["code"] == "agent_forbidden"
    assert minted.status_code == 403, minted.text
    assert minted.json()["code"] == "agent_forbidden"
    assert [t["full_access"] for t in open_space.get("/api/tokens").json()] == [False]


def test_every_change_of_the_flag_is_on_the_owners_timeline(
    open_space: TestClient, admin_user: Any
) -> None:
    created = _make(open_space, "claude")
    open_space.patch(f"/api/tokens/{created['id']}", json={"full_access": True})
    open_space.patch(f"/api/tokens/{created['id']}", json={"full_access": False})

    timeline = open_space.get(f"/api/users/{admin_user.id}/timeline").json()

    changes = [entry for entry in timeline if entry["kind"] == "pat_full_access_changed"]
    assert [entry["payload"]["full_access"] for entry in changes] == [False, True]
    assert all(entry["actor_type"] == "user" for entry in changes)
    assert all(entry["actor_name"] == "Admin" for entry in changes)
    assert created["token"] not in str(timeline)


def test_an_administrator_sees_and_closes_a_colleagues_token_and_a_collaboratore_cannot(
    open_space: TestClient, collaborator_client: TestClient, api_session: Any, admin_user: Any
) -> None:
    # `collaborator_client` is the same client with the collaboratore's cookie: it makes the
    # token, then the cookie is swapped back for the admin's.
    mine = _make(collaborator_client, "di colla", full_access=True)
    assert collaborator_client.get("/api/tokens/team").status_code == 403
    assert collaborator_client.get("/api/tokens/team").json()["code"] == "permission_denied"

    login = open_space.post(
        "/api/auth/login", json={"email": admin_user.email, "password": "supersegreta1"}
    )
    assert login.status_code == 200, login.text
    team = open_space.get("/api/tokens/team")
    assert [(t["owner_nome"], t["full_access"]) for t in team.json()] == [("Test", True)]

    closed = open_space.patch(f"/api/tokens/{mine['id']}", json={"full_access": False})
    assert closed.status_code == 200, closed.text
    assert closed.json()["full_access"] is False
