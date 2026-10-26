"""REB-773 over HTTP: the owner a customer is born with and the admin's change of it."""

from fastapi.testclient import TestClient


def _me(client: TestClient) -> str:
    response = client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_a_customer_created_over_http_is_owned_by_its_creator(
    collaborator_client: TestClient,
) -> None:
    created = collaborator_client.post("/api/customers", json={"ragione_sociale": "ACME Srl"})

    assert created.status_code == 201
    assert created.json()["owner_id"] == _me(collaborator_client)


def test_a_member_is_refused_the_change_and_an_admin_makes_it(
    collaborator_client: TestClient, admin_user, client: TestClient
) -> None:
    member_id = _me(collaborator_client)
    customer_id = collaborator_client.post(
        "/api/customers", json={"ragione_sociale": "ACME Srl"}
    ).json()["id"]

    refused = collaborator_client.patch(
        f"/api/customers/{customer_id}", json={"owner_id": str(admin_user.id)}
    )
    assert refused.status_code == 403, refused.text
    assert collaborator_client.get(f"/api/customers/{customer_id}").json()["owner_id"] == member_id

    login = client.post(
        "/api/auth/login", json={"email": "admin@pigro.it", "password": "supersegreta1"}
    )
    assert login.status_code == 200, login.text
    moved = client.patch(f"/api/customers/{customer_id}", json={"owner_id": str(admin_user.id)})
    assert moved.status_code == 200, moved.text
    assert moved.json()["owner_id"] == str(admin_user.id)
