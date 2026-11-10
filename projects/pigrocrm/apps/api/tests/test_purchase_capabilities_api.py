"""`GET /api/purchases/capabilities` (REB-1023): the buyer's pack as the purchase form reads it."""

from uuid import uuid4

from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url

LTD = {
    "name": "rebase ltd",
    "legal_name": "Rebase Ltd",
    "country": "GB",
    "fiscal_profile": {"pack_id": "non-it", "default_vat_rate": "20.00"},
}
EVERYTHING = {"vat_recap": True, "withholding": True, "reverse_charge": True, "split_payment": True}


def _set_pack(client: TestClient, pack_id: str, **extra: str) -> str:
    url = legal_entity_url(client, "/fiscal-profile")
    saved = client.put(url, json={"pack_id": pack_id, **extra})
    assert saved.status_code == 200, saved.text
    return url.split("/")[3]


def test_the_default_entity_answers_when_none_is_named(logged_in: TestClient) -> None:
    entity = _set_pack(logged_in, "it-flat-rate", regime_code="RF19")

    response = logged_in.get("/api/purchases/capabilities")

    assert response.status_code == 200, response.text
    assert response.json() == {**EVERYTHING, "legal_entity_id": entity, "withholding": False}


def test_a_named_entity_answers_for_its_own_pack(logged_in: TestClient) -> None:
    default = _set_pack(logged_in, "it-flat-rate", regime_code="RF19")
    second = logged_in.post("/api/legal-entities", json=LTD).json()["id"]

    flat = logged_in.get("/api/purchases/capabilities", params={"legal_entity_id": default})
    foreign = logged_in.get("/api/purchases/capabilities", params={"legal_entity_id": second})

    assert flat.json()["withholding"] is False and flat.json()["legal_entity_id"] == default
    assert foreign.json() == {**EVERYTHING, "legal_entity_id": second}


def test_with_two_entities_and_none_named_nothing_is_resolved_and_all_is_true(
    logged_in: TestClient,
) -> None:
    _set_pack(logged_in, "it-flat-rate", regime_code="RF19")
    assert logged_in.post("/api/legal-entities", json=LTD).status_code == 201

    response = logged_in.get("/api/purchases/capabilities")

    assert response.status_code == 200, response.text
    assert response.json() == {**EVERYTHING, "legal_entity_id": None}


def test_an_unknown_entity_is_a_404(logged_in: TestClient) -> None:
    response = logged_in.get(
        "/api/purchases/capabilities", params={"legal_entity_id": str(uuid4())}
    )

    assert response.status_code == 404, response.text


def test_the_route_is_the_purchases_modules(logged_in: TestClient) -> None:
    assert logged_in.put("/api/settings/modules", json={"off": ["purchases"]}).status_code == 200

    response = logged_in.get("/api/purchases/capabilities")

    assert response.status_code == 404
    assert response.json()["code"] == "module_off"
