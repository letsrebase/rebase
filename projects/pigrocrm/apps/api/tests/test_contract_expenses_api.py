"""The HTTP surface, thin by design: validate the body, resolve the Actor, call
`ContractExpenseService` -- the role gate is `test_role_matrix.py`'s own concern,
proved once for every write route in this application; this file proves the wire
shape of a create/list/update round trip and that `rimborsabile` reaches the
response as the database computed it, not merely as the caller asked for it."""

from typing import Any

from fastapi.testclient import TestClient


def _customer_id(logged_in: TestClient) -> str:
    response = logged_in.post("/api/customers", json={"ragione_sociale": "ACME Srl"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _contract_id(logged_in: TestClient, *, politica_spese: dict[str, Any]) -> str:
    response = logged_in.post(
        "/api/contracts",
        json={
            "customer_id": _customer_id(logged_in),
            "titolo": "Consulenza",
            "inizio": "2026-01-01",
            "tipo_rinnovo": "nessuno",
            "preavviso_disdetta_giorni": 30,
            "cadenza_fatturazione": "mensile",
            "politica_spese": politica_spese,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _category_id(logged_in: TestClient) -> str:
    response = logged_in.post("/api/cost-categories", json={"nome": "Trasferte"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_create_reports_the_trigger_computed_rimborsabile(logged_in: TestClient) -> None:
    contract_id = _contract_id(logged_in, politica_spese={"kind": "rimborsabile"})
    category_id = _category_id(logged_in)

    response = logged_in.post(
        f"/api/contracts/{contract_id}/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "importo": "42.00",
            "descrizione": "Biglietto treno",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["rimborsabile"] is True
    assert body["contract_id"] == contract_id


def test_create_flags_non_reimbursable_without_a_422(logged_in: TestClient) -> None:
    contract_id = _contract_id(
        logged_in,
        politica_spese={"kind": "rimborsabile", "richiede_preautorizzazione": True},
    )
    category_id = _category_id(logged_in)

    response = logged_in.post(
        f"/api/contracts/{contract_id}/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "importo": "42.00",
            "descrizione": "Biglietto treno",
            "pre_autorizzata": False,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["rimborsabile"] is False


def test_pre_autorizzata_without_a_reference_is_a_422_naming_the_field(
    logged_in: TestClient,
) -> None:
    contract_id = _contract_id(logged_in, politica_spese={"kind": "rimborsabile"})
    category_id = _category_id(logged_in)

    response = logged_in.post(
        f"/api/contracts/{contract_id}/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "importo": "42.00",
            "descrizione": "Biglietto treno",
            "pre_autorizzata": True,
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
            "importo": "42.00",
            "descrizione": "Biglietto treno",
        },
    )
    assert response.status_code == 404, response.text


def test_list_and_update_round_trip(logged_in: TestClient) -> None:
    contract_id = _contract_id(logged_in, politica_spese={"kind": "rimborsabile"})
    category_id = _category_id(logged_in)
    created = logged_in.post(
        f"/api/contracts/{contract_id}/expenses",
        json={
            "category_id": category_id,
            "data": "2026-03-05",
            "importo": "42.00",
            "descrizione": "Biglietto treno",
        },
    ).json()

    listed = logged_in.get(f"/api/contracts/{contract_id}/expenses")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [created["id"]]

    updated = logged_in.patch(
        f"/api/contracts/{contract_id}/expenses/{created['id']}",
        json={"importo": "99.00"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["importo"] == "99.00"
    assert updated.json()["descrizione"] == "Biglietto treno"


def test_the_openapi_document_describes_the_new_routes(logged_in: TestClient) -> None:
    schema = logged_in.get("/openapi.json").json()
    paths = schema["paths"]
    assert "post" in paths["/api/contracts/{contract_id}/expenses"]
    assert "get" in paths["/api/contracts/{contract_id}/expenses"]
    assert "patch" in paths["/api/contracts/{contract_id}/expenses/{expense_id}"]
