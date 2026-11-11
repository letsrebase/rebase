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


def test_a_vat_number_is_found_whatever_its_punctuation(logged_in: TestClient) -> None:
    _create(
        logged_in,
        legal_name="Olandese",
        country="NL",
        vat_number="NL 1234.56.789 B01",
        roles=["supplier"],
    )

    for term in ("NL123456789B01", "nl1234.56789b01", "NL 1234.56.789 B01"):
        found = logged_in.get("/api/organizations", params={"role": "any", "search": term}).json()
        assert [item["legal_name"] for item in found["items"]] == ["Olandese"], term


def test_a_freelancer_is_a_supplier_whatever_writes_it(logged_in: TestClient) -> None:
    """REB-1061: `freelancer` never comes without `supplier`: on «Fornitori», not «Clienti»."""
    made = _create(logged_in, legal_name="Giulia Verdi", roles=["freelancer"])
    assert made["roles"] == ["freelancer", "supplier"]
    mixed = _create(logged_in, legal_name="Studio Neri", roles=["customer"])
    updated = logged_in.patch(
        f"/api/organizations/{mixed['id']}", json={"roles": ["customer", "freelancer"]}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["roles"] == ["customer", "freelancer", "supplier"]

    suppliers = logged_in.get("/api/organizations", params={"role": "supplier"}).json()
    assert {item["legal_name"] for item in suppliers["items"]} == {"Giulia Verdi", "Studio Neri"}
    customers = logged_in.get("/api/organizations", params={"role": "customer"}).json()
    assert {item["legal_name"] for item in customers["items"]} == {"Studio Neri"}


def test_the_supplier_role_of_a_freelancer_cannot_be_removed(logged_in: TestClient) -> None:
    made = _create(logged_in, legal_name="Giulia Verdi", roles=["freelancer"])
    refused = logged_in.patch(
        f"/api/organizations/{made['id']}", json={"roles": ["freelancer", "customer"]}
    )
    assert refused.status_code == 422, refused.text
    assert "fornitore" in refused.text
    kept = logged_in.get(f"/api/organizations/{made['id']}").json()
    assert kept["roles"] == ["freelancer", "supplier"]


def test_the_retired_customer_or_neither_role_is_refused(logged_in: TestClient) -> None:
    response = logged_in.get("/api/organizations", params={"role": "customer_or_neither"})
    assert response.status_code == 422
