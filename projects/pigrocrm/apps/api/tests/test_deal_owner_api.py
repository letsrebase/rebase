"""REB-780 over HTTP: a deal's owner, and the directory a picker draws on."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService


@pytest.fixture(autouse=True)
def _stages(api_session: Session) -> None:
    PipelineService(api_session).seed_defaults(Actor(id=None, type="system", role="admin"))
    api_session.commit()


def _me(client: TestClient) -> str:
    response = client.get("/api/auth/me")
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _deal(client: TestClient) -> dict:
    customer = client.post("/api/customers", json={"ragione_sociale": "ACME Srl"})
    assert customer.status_code == 201, customer.text
    created = client.post("/api/deals", json={"nome": "Sito", "customer_id": customer.json()["id"]})
    assert created.status_code == 201, created.text
    return created.json()


def test_a_deal_created_over_http_is_owned_by_its_creator(
    collaborator_client: TestClient,
) -> None:
    assert _deal(collaborator_client)["owner_id"] == _me(collaborator_client)


def test_a_member_hands_a_deal_to_a_colleague_found_in_the_directory(
    collaborator_client: TestClient, admin_user
) -> None:
    deal = _deal(collaborator_client)

    directory = collaborator_client.get("/api/users/directory")
    assert directory.status_code == 200, directory.text
    rows = directory.json()
    assert str(admin_user.id) in {row["id"] for row in rows}
    assert all(set(row) == {"id", "nome"} for row in rows)

    moved = collaborator_client.patch(
        f"/api/deals/{deal['id']}", json={"owner_id": str(admin_user.id)}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["owner_id"] == str(admin_user.id)
