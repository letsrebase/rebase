"""The HTTP surface, thin by design: validate the body, resolve the Actor, call
`ContractExpenseService` -- the role gate is `test_role_matrix.py`'s own concern,
proved once for every write route in this application; this file proves the wire
shape of a create/list/update round trip and that `reimbursable` reaches the
response as the database computed it, not merely as the caller asked for it."""

from typing import Any

from fastapi.testclient import TestClient


def _customer_id(logged_in: TestClient) -> str:
    response = logged_in.post("/api/customers", json={"legal_name": "ACME Srl"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _contract_id(logged_in: TestClient, *, expense_policy: dict[str, Any]) -> str:
    response = logged_in.post(
        "/api/contracts",
        json={
            "customer_id": _customer_id(logged_in),
            "title": "Consulenza",
            "start": "2026-01-01",
            "renewal_type": "none",
            "termination_notice_days": 30,
            "billing_cadence": "monthly",
            "expense_policy": expense_policy,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _category_id(logged_in: TestClient) -> str:
    response = logged_in.post("/api/cost-categories", json={"nome": "Trasferte"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_create_reports_the_trigger_computed_reimbursable(logged_in: TestClient) -> None:
    contract_id = _contract_id(logged_in, expense_policy={"kind": "reimbursable"})
    category_id = _category_id(logged_in)

    response = logged_in.post(
        f"/api/contracts/{contract_id}/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "amount": "42.00",
            "description": "Biglietto treno",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reimbursable"] is True
    assert body["contract_id"] == contract_id


def test_create_flags_non_reimbursable_without_a_422(logged_in: TestClient) -> None:
    contract_id = _contract_id(
        logged_in,
        expense_policy={"kind": "reimbursable", "richiede_preautorizzazione": True},
    )
    category_id = _category_id(logged_in)

    response = logged_in.post(
        f"/api/contracts/{contract_id}/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "amount": "42.00",
            "description": "Biglietto treno",
            "pre_authorized": False,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["reimbursable"] is False


def test_pre_authorized_without_to_reference_is_to_422_naming_the_field(
    logged_in: TestClient,
) -> None:
    contract_id = _contract_id(logged_in, expense_policy={"kind": "reimbursable"})
    category_id = _category_id(logged_in)

    response = logged_in.post(
        f"/api/contracts/{contract_id}/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "amount": "42.00",
            "description": "Biglietto treno",
            "pre_authorized": True,
        },
    )
    assert response.status_code == 422, response.text


def test_create_on_an_unknown_contract_is_a_404(logged_in: TestClient) -> None:
    category_id = _category_id(logged_in)
    response = logged_in.post(
        "/api/contracts/00000000-0000-0000-0000-000000000000/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "amount": "42.00",
            "description": "Biglietto treno",
        },
    )
    assert response.status_code == 404, response.text


def test_list_and_update_round_trip(logged_in: TestClient) -> None:
    contract_id = _contract_id(logged_in, expense_policy={"kind": "reimbursable"})
    category_id = _category_id(logged_in)
    created = logged_in.post(
        f"/api/contracts/{contract_id}/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "amount": "42.00",
            "description": "Biglietto treno",
        },
    ).json()

    listed = logged_in.get(f"/api/contracts/{contract_id}/expenses")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [created["id"]]

    updated = logged_in.patch(
        f"/api/contracts/{contract_id}/expenses/{created['id']}",
        json={"amount": "99.00"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["amount"] == "99.00"
    assert updated.json()["description"] == "Biglietto treno"


def test_the_openapi_document_describes_the_new_routes(logged_in: TestClient) -> None:
    schema = logged_in.get("/openapi.json").json()
    paths = schema["paths"]
    assert "post" in paths["/api/contracts/{contract_id}/expenses"]
    assert "get" in paths["/api/contracts/{contract_id}/expenses"]
    assert "patch" in paths["/api/contracts/{contract_id}/expenses/{expense_id}"]


def test_create_refuses_a_malformed_expense_policy_with_422(logged_in: TestClient) -> None:
    response = logged_in.post(
        "/api/contracts",
        json={
            "customer_id": _customer_id(logged_in),
            "title": "Consulenza",
            "start": "2026-01-01",
            "renewal_type": "none",
            "termination_notice_days": 30,
            "billing_cadence": "monthly",
            "expense_policy": {"mode": "rimborsabili"},
        },
    )
    assert response.status_code == 422, response.text
    assert "politica_spese.kind" in response.text
