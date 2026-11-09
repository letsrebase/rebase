"""REB-928 over HTTP: a deal's value lines, its detail read and the value preview."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService

UPFRONT = {"unit": "flat", "delivery_period": "one_off", "unit_price": "20000.00"}
MAINTENANCE = {"unit": "flat", "delivery_period": "monthly", "unit_price": "500.00"}


@pytest.fixture(autouse=True)
def _stages(api_session: Session) -> None:
    PipelineService(api_session).seed_defaults(Actor(id=None, type="system", role="admin"))
    api_session.commit()


def _customer(client: TestClient) -> str:
    response = client.post("/api/organizations", json={"legal_name": "ACME Srl"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_a_deal_is_created_with_lines_and_read_back_with_them(
    collaborator_client: TestClient,
) -> None:
    created = collaborator_client.post(
        "/api/deals",
        json={
            "name": "Sito",
            "customer_id": _customer(collaborator_client),
            "value_lines": [UPFRONT, MAINTENANCE],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["expected_value"], body["one_off_value"], body["monthly_recurring_value"]) == (
        "26000.00",
        "20000.00",
        "500.00",
    )

    detail = collaborator_client.get(f"/api/deals/{body['id']}").json()
    assert [line["position"] for line in detail["value_lines"]] == [0, 1]
    assert detail["value_lines"][1]["delivery_period"] == "monthly"

    listed = collaborator_client.get("/api/deals").json()["items"][0]
    assert listed["one_off_value"] == "20000.00"
    assert "value_lines" not in listed


def test_expected_value_is_refused_on_create_and_update_over_http(
    collaborator_client: TestClient,
) -> None:
    customer = _customer(collaborator_client)
    refused = collaborator_client.post(
        "/api/deals", json={"name": "X", "customer_id": customer, "expected_value": "10.00"}
    )
    assert refused.status_code == 422, refused.text
    assert "expected_value" in refused.text

    deal = collaborator_client.post(
        "/api/deals", json={"name": "X", "customer_id": customer}
    ).json()
    refused = collaborator_client.patch(
        f"/api/deals/{deal['id']}", json={"expected_value": "10.00"}
    )
    assert refused.status_code == 422, refused.text


def test_a_refused_line_is_a_422_naming_the_lines_not_a_database_error(
    collaborator_client: TestClient,
) -> None:
    customer = _customer(collaborator_client)
    response = collaborator_client.post(
        "/api/deals",
        json={
            "name": "X",
            "customer_id": customer,
            "value_lines": [{"unit": "day", "delivery_period": "monthly", "unit_price": "100"}],
        },
    )
    assert response.status_code == 422, response.text
    assert "value_lines" in response.text


def test_the_preview_answers_the_figures_and_saves_nothing(
    collaborator_client: TestClient,
) -> None:
    response = collaborator_client.post(
        "/api/deals/value-preview",
        json={"value_lines": [UPFRONT, MAINTENANCE], "expected_close_date": "2026-11-01"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "expected_value": "26000.00",
        "one_off_value": "20000.00",
        "monthly_recurring_value": "500.00",
    }
    assert collaborator_client.get("/api/deals").json()["items"] == []

    empty = collaborator_client.post("/api/deals/value-preview", json={"value_lines": []})
    assert empty.json() == {
        "expected_value": None,
        "one_off_value": None,
        "monthly_recurring_value": None,
    }
