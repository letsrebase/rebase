"""REST surface of the read-only review step (REB-365): `POST /api/invoices/import/
review`. `test_role_matrix.py` owns the admin-only gate; this file proves the read
itself -- matching the customer, never writing, and staying idempotent -- through the
real HTTP surface."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

FIXTURES = (
    Path(__file__).resolve().parents[3] / "packages" / "core" / "tests" / "fixtures" / "fatturapa"
)
CONSULENZA = "fpr12-consulenza-marzo.xml"
# The fixture's own CedentePrestatore (fornitore) -- matching this on the emitter
# profile is what makes it "outgoing" for the account holder.
FORNITORE_PIVA = "01234567890"
FORNITORE_CF = "BNCCHR85M41H501Z"
# Its CessionarioCommittente (cliente).
CLIENTE_PIVA = "09876543210"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def emitter(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put(
        "/api/emitter",
        json={
            "ragione_sociale": "Chiara Bianchi",
            "partita_iva": FORNITORE_PIVA,
            "codice_fiscale": FORNITORE_CF,
            "indirizzo": "Via delle Officine 10",
            "cap": "20121",
            "comune": "Milano",
            "provincia": "MI",
            "nazione": "IT",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def customer(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Esempio Servizi S.r.l.",
            "partita_iva": CLIENTE_PIVA,
            "indirizzo": "Piazza dei Modelli 4",
            "cap": "00187",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(logged_in: TestClient, *, customer_id: str, content: bytes) -> str:
    created = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "titolo": "Fattura ricevuta"}
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    uploaded = logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("fattura.xml", content, "application/xml")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return str(document_id)


def test_review_matches_the_customer_and_is_idempotent(
    logged_in: TestClient, emitter: dict[str, Any], customer: dict[str, Any]
) -> None:
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(CONSULENZA))

    first = logged_in.post("/api/invoices/import/review", json={"document_ids": [document_id]})
    assert first.status_code == 200, first.text
    second = logged_in.post("/api/invoices/import/review", json={"document_ids": [document_id]})
    assert second.status_code == 200, second.text
    assert first.json() == second.json()

    [row] = first.json()["righe"]
    assert row["outcome"] == "ready"
    assert row["matched_customer_id"] == customer["id"]

    # And the review itself never touched the register: nothing to page through.
    listed = logged_in.get("/api/invoices")
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []


def test_a_document_no_adapter_recognises_is_unclaimed_not_a_500(
    logged_in: TestClient, emitter: dict[str, Any], customer: dict[str, Any]
) -> None:
    document_id = _upload(logged_in, customer_id=customer["id"], content=b"not a fattura at all")
    response = logged_in.post("/api/invoices/import/review", json={"document_ids": [document_id]})
    assert response.status_code == 200, response.text
    [row] = response.json()["righe"]
    assert row["outcome"] == "unclaimed"


def test_a_collaborator_cannot_review(
    logged_in: TestClient, emitter: dict[str, Any], customer: dict[str, Any]
) -> None:
    """Mirrors `test_invoices_import_api.py::test_a_collaborator_cannot_import`: a
    fresh session on the same app, never the shared cookie jar `logged_in` already
    holds (see that file's own `_second_actor` for why)."""
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(CONSULENZA))
    email = f"collaboratore-{id(logged_in)}@pigro.it"
    created = logged_in.post(
        "/api/users",
        json={
            "email": email,
            "password": "supersegreta1",
            "nome": "Test",
            "ruolo": "collaboratore",
        },
    )
    assert created.status_code == 201, created.text
    client = TestClient(logged_in.app, base_url="https://testserver")
    login = client.post("/api/auth/login", json={"email": email, "password": "supersegreta1"})
    assert login.status_code == 200, login.text

    response = client.post("/api/invoices/import/review", json={"document_ids": [document_id]})
    assert response.status_code == 403, response.text


def test_document_ids_must_be_non_empty(logged_in: TestClient, emitter: dict[str, Any]) -> None:
    response = logged_in.post("/api/invoices/import/review", json={"document_ids": []})
    assert response.status_code == 422, response.text
