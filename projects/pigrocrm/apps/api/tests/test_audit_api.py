"""R5's read half: an audit nobody can read closes nothing.

`packages/core/tests/test_config_audit.py` proves the entries are written; these
prove an administrator (and, for their own account, an ordinary user) can actually
get at them, and that nobody else can.
"""

from typing import Any

from fastapi.testclient import TestClient

COLLABORATOR = {"email": "collab@pigro.it", "password": "supersegreta1"}


def _create_collaborator(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.post(
        "/api/users",
        json={**COLLABORATOR, "name": "Collaboratrice", "role": "collaborator"},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _login(client: TestClient, credentials: dict[str, str]) -> None:
    """Replaces whatever session the shared client is carrying. The cookie jar is per
    client, so logging in as somebody else is exactly how a second identity is
    exercised without a second fixture."""
    assert client.post("/api/auth/login", json=credentials).status_code == 200


def test_the_owner_sees_their_tokens_whole_life_on_their_own_timeline(
    logged_in: TestClient, admin_user: Any
) -> None:
    """Issuing a PAT, using it, and revoking it are three separate events, and until
    R5 all three were invisible. `pat_first_used` is recorded by `resolve()` itself,
    so it appears here only because the token really did authenticate a request."""
    created = logged_in.post("/api/tokens", json={"name": "Claude locale"})
    assert created.status_code == 201, created.text
    token = created.json()["token"]

    authenticated = logged_in.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert authenticated.status_code == 200
    assert logged_in.delete(f"/api/tokens/{created.json()['id']}").status_code == 204

    response = logged_in.get(f"/api/users/{admin_user.id}/timeline")
    assert response.status_code == 200, response.text
    kinds = [entry["kind"] for entry in response.json()]
    assert kinds == ["pat_revoked", "pat_first_used", "pat_created", "created"], kinds


def test_the_timeline_never_carries_the_token_it_describes(
    logged_in: TestClient, admin_user: Any
) -> None:
    """The endpoint is the widest audience the audit payload will ever have. The raw
    value is returned exactly once, by the create call; it must not come back here."""
    token = logged_in.post("/api/tokens", json={"name": "Claude locale"}).json()["token"]
    logged_in.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    response = logged_in.get(f"/api/users/{admin_user.id}/timeline")

    assert "pat_first_used" in response.text, "positive control: the entries are really here"
    assert token not in response.text
    assert token.removeprefix("pgc_") not in response.text


def test_a_collaborator_cannot_read_another_accounts_timeline(
    logged_in: TestClient, admin_user: Any
) -> None:
    """Who holds which token on which account is not a collaborator's business."""
    _create_collaborator(logged_in)
    _login(logged_in, COLLABORATOR)

    response = logged_in.get(f"/api/users/{admin_user.id}/timeline")

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_a_collaborator_can_read_their_own(logged_in: TestClient) -> None:
    """The owner is the one who has to see that a token they thought was dead is
    still being presented, so refusing them their own timeline would make the alarm
    useless to the only person able to act on it."""
    collaborator = _create_collaborator(logged_in)
    _login(logged_in, COLLABORATOR)

    response = logged_in.get(f"/api/users/{collaborator['id']}/timeline")

    assert response.status_code == 200
    assert [e["kind"] for e in response.json()] == ["created"]


def test_a_field_definitions_rename_is_readable_and_distinguishable_from_its_archive(
    logged_in: TestClient,
) -> None:
    field = logged_in.post(
        "/api/field-definitions",
        json={
            "entity_type": "organization",
            "key": "settore",
            "label": "Settore",
            "field_type": "text",
        },
    ).json()

    assert (
        logged_in.patch(
            f"/api/field-definitions/{field['id']}", json={"label": "Settore merceologico"}
        ).status_code
        == 200
    )
    assert logged_in.post(f"/api/field-definitions/{field['id']}/archive").status_code == 200

    entries = logged_in.get(f"/api/field-definitions/{field['id']}/timeline").json()
    assert [e["kind"] for e in entries] == ["archived", "updated", "created"]
    assert entries[1]["payload"]["before"] == {"label": "Settore"}
    assert entries[1]["payload"]["after"] == {"label": "Settore merceologico"}


def test_a_collaborator_cannot_read_a_field_definitions_timeline(logged_in: TestClient) -> None:
    """Matching exactly who may change one."""
    field = logged_in.post(
        "/api/field-definitions",
        json={
            "entity_type": "organization",
            "key": "riservato",
            "label": "Riservato",
            "field_type": "text",
        },
    ).json()
    _create_collaborator(logged_in)
    _login(logged_in, COLLABORATOR)

    response = logged_in.get(f"/api/field-definitions/{field['id']}/timeline")

    assert response.status_code == 403


def test_a_deleted_stages_timeline_is_the_only_record_that_it_existed(
    logged_in: TestClient,
) -> None:
    """`delete` is the one hard DELETE in the domain: the row is gone, and 404 on the
    stage itself proves it, so the timeline entry has to stand on its own."""
    stage = logged_in.post(
        "/api/pipeline-stages",
        json={"name": "Da eliminare", "position": 8, "code": "da_eliminare"},
    ).json()

    assert logged_in.delete(f"/api/pipeline-stages/{stage['id']}").status_code == 204

    assert logged_in.get(f"/api/pipeline-stages/{stage['id']}/timeline").status_code == 200
    entries = logged_in.get(f"/api/pipeline-stages/{stage['id']}/timeline").json()
    assert [e["kind"] for e in entries] == ["deleted", "created"]
    assert entries[0]["payload"] == {"name": "Da eliminare", "code": "da_eliminare", "type": "open"}


def test_a_timeline_still_refuses_an_out_of_range_limit(
    logged_in: TestClient, admin_user: Any
) -> None:
    """The same `Query(ge=1, le=200)` bound every other timeline route declares."""
    assert logged_in.get(f"/api/users/{admin_user.id}/timeline?limit=0").status_code == 422
    assert logged_in.get(f"/api/users/{admin_user.id}/timeline?limit=201").status_code == 422


def test_a_timeline_requires_authentication(client: TestClient, admin_user: Any) -> None:
    assert client.get(f"/api/users/{admin_user.id}/timeline").status_code == 401
