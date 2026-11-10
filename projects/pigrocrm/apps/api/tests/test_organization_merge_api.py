"""REB-995 over HTTP: folding two organizations of one company into one."""

from fastapi.testclient import TestClient


def _admin(client: TestClient) -> None:
    login = client.post(
        "/api/auth/login", json={"email": "admin@pigro.it", "password": "supersegreta1"}
    )
    assert login.status_code == 200, login.text


def _organization(client: TestClient, name: str) -> str:
    created = client.post("/api/organizations", json={"legal_name": name})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def test_a_member_cannot_merge(collaborator_client: TestClient) -> None:
    survivor = _organization(collaborator_client, "Acme Srl")
    loser = _organization(collaborator_client, "ACME s.r.l.")

    refused = collaborator_client.post(
        f"/api/organizations/{survivor}/merge", json={"loser_id": loser, "dry_run": False}
    )

    assert refused.status_code == 403, refused.text
    assert collaborator_client.get(f"/api/organizations/{loser}").status_code == 200


def test_the_default_is_a_preview_and_the_merge_moves_what_hangs_on_the_loser(
    collaborator_client: TestClient, admin_user: object, client: TestClient
) -> None:
    survivor = _organization(collaborator_client, "Acme Srl")
    loser = _organization(collaborator_client, "ACME s.r.l.")
    person = collaborator_client.post(
        "/api/people", json={"first_name": "Giulia", "last_name": "Rossi", "customer_id": loser}
    )
    assert person.status_code == 201, person.text
    _admin(client)

    preview = client.post(f"/api/organizations/{survivor}/merge", json={"loser_id": loser})
    assert preview.status_code == 200, preview.text
    assert preview.json()["dry_run"] is True
    assert preview.json()["moved"]["people"] == 1
    assert client.get(f"/api/organizations/{loser}").status_code == 200

    done = client.post(
        f"/api/organizations/{survivor}/merge", json={"loser_id": loser, "dry_run": False}
    )
    assert done.status_code == 200, done.text
    assert done.json()["dry_run"] is False
    assert done.json()["survivor"]["id"] == survivor
    assert client.get(f"/api/organizations/{loser}").status_code == 404
    moved = client.get(f"/api/people/{person.json()['id']}")
    assert moved.json()["customer_id"] == survivor

    again = client.post(
        f"/api/organizations/{survivor}/merge", json={"loser_id": loser, "dry_run": False}
    )
    assert again.status_code in {404, 409}


def test_an_organization_does_not_merge_into_itself(
    collaborator_client: TestClient, admin_user: object, client: TestClient
) -> None:
    only = _organization(collaborator_client, "Acme Srl")
    _admin(client)
    refused = client.post(f"/api/organizations/{only}/merge", json={"loser_id": only})
    assert refused.status_code == 422, refused.text
