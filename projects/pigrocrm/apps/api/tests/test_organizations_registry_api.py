"""The registry page's reads: one list over the roles, with the total each view shows on its
tab and the fiscal kind of a supplier as a filter."""

from fastapi.testclient import TestClient


def _create(client: TestClient, **body: object) -> dict:
    response = client.post("/api/organizations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_the_list_says_how_many_match_whatever_the_page(logged_in: TestClient) -> None:
    _create(logged_in, legal_name="Cliente Uno")
    _create(logged_in, legal_name="Cliente Due")
    _create(logged_in, legal_name="Fornitore Uno", roles=["supplier"], supplier_kind="company")
    _create(logged_in, legal_name="Entrambi", roles=["customer", "supplier"])

    customers = logged_in.get("/api/organizations", params={"limit": 1}).json()
    assert len(customers["items"]) == 1 and customers["total"] == 3
    suppliers = logged_in.get("/api/organizations", params={"role": "supplier"}).json()
    assert suppliers["total"] == 2
    everyone = logged_in.get("/api/organizations", params={"role": "any", "limit": 1}).json()
    assert everyone["total"] == 4
    searched = logged_in.get(
        "/api/organizations", params={"role": "any", "search": "Fornitore"}
    ).json()
    assert searched["total"] == 1


def test_a_supplier_list_filters_by_fiscal_kind(logged_in: TestClient) -> None:
    _create(logged_in, legal_name="Societa", roles=["supplier"], supplier_kind="company")
    _create(logged_in, legal_name="Studio", roles=["supplier"], supplier_kind="professional")

    professionals = logged_in.get(
        "/api/organizations", params={"role": "supplier", "supplier_kind": "professional"}
    ).json()
    assert [item["legal_name"] for item in professionals["items"]] == ["Studio"]
    assert professionals["total"] == 1
