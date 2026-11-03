"""REST surface of the confirm step (REB-366): `POST /api/invoices/import/confirm`.
`test_role_matrix.py` owns the admin-only gate; this file proves the write itself --
converging onto `import_issued`, staying a no-op on a second confirm, and setting a
hash-verified `xml_document_id` for a single-invoice source -- through the real HTTP
surface."""

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url

FIXTURES = (
    Path(__file__).resolve().parents[3] / "packages" / "core" / "tests" / "fixtures" / "fatturapa"
)
CONSULTING = "fpr-12-march-consulting.xml"
LOTTO = "fpr-12-batch-due-invoices.xml"
# The fixtures' own `CedentePrestatore` (fornitore) -- matching this on the emitter
# profile is what makes them "outgoing" for the account holder.
SUPPLIER_VAT_NUMBER = "01234567890"
SUPPLIER_CF = "BNCCHR85M41H501Z"
# Their `CessionarioCommittente` (cliente).
CUSTOMER_VAT_NUMBER = "09876543210"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def fiscal_profile(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put(
        legal_entity_url(logged_in, "/fiscal-profile"), json={"regime_code": "RF19"}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def legal_entity(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put(
        legal_entity_url(logged_in),
        json={
            "legal_name": "Chiara Bianchi",
            "vat_number": SUPPLIER_VAT_NUMBER,
            "tax_code": SUPPLIER_CF,
            "address": "Via delle Officine 10",
            "postal_code": "20121",
            "city": "Milano",
            "province": "MI",
            "country": "IT",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def customer(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.post(
        "/api/customers",
        json={
            "legal_name": "Esempio Servizi S.r.l.",
            "vat_number": CUSTOMER_VAT_NUMBER,
            "address": "Piazza dei Modelli 4",
            "postal_code": "00187",
            "city": "Roma",
            "province": "RM",
            "country": "IT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(logged_in: TestClient, *, customer_id: str, content: bytes) -> str:
    created = logged_in.post(
        "/api/documents", json={"customer_id": customer_id, "title": "Fattura ricevuta"}
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    uploaded = logged_in.post(
        f"/api/documents/{document_id}/versions",
        files={"file": ("fattura.xml", content, "application/xml")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return str(document_id)


def test_confirming_a_fresh_document_writes_the_register_and_a_second_confirm_is_a_no_op(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(CONSULTING))

    first = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert first.status_code == 200, first.text
    [row] = first.json()["lines"]
    assert row["outcome"] == "imported"
    assert row["invoice"]["number"] == 6
    assert row["invoice"]["customer_id"] == customer["id"]
    # REB-368: a document this CRM parsed itself gets "fatturapa", not InvoiceImport's
    # own hand-declared "esterno" default.
    assert row["invoice"]["imported_from"] == "fatturapa"
    assert row["invoice"]["xml_document_id"] == document_id
    assert row["invoice"]["xml_hash_sha256"] == hashlib.sha256(_fixture(CONSULTING)).hexdigest()

    second = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert second.status_code == 200, second.text
    [second_row] = second.json()["lines"]
    assert second_row["outcome"] == "already_present"
    assert second_row["invoice"]["id"] == row["invoice"]["id"]

    # The write really landed on the register, unlike the read-only review.
    listed = logged_in.get("/api/invoices")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [row["invoice"]["id"]]


def test_a_batch_sourced_document_leaves_xml_document_id_null(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(LOTTO))

    response = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert response.status_code == 200, response.text
    lines = response.json()["lines"]
    assert len(lines) == 2
    assert all(row["outcome"] == "imported" for row in lines)
    assert all(row["invoice"]["xml_document_id"] is None for row in lines)
    assert all(row["invoice"]["xml_hash_sha256"] is None for row in lines)


def test_a_document_no_adapter_recognises_is_unclaimed_not_a_500(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    document_id = _upload(logged_in, customer_id=customer["id"], content=b"not a fattura at all")
    response = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert response.status_code == 200, response.text
    [row] = response.json()["lines"]
    assert row["outcome"] == "unclaimed"
    assert row["invoice"] is None


def test_no_matching_customer_reports_needs_customer_confirmation_and_writes_nothing(
    logged_in: TestClient, fiscal_profile: dict[str, Any], legal_entity: dict[str, Any]
) -> None:
    customer = logged_in.post(
        "/api/customers",
        json={
            "legal_name": "Cliente Senza Match",
            "address": "Via Roma 1",
            "postal_code": "00100",
            "city": "Roma",
            "province": "RM",
            "country": "IT",
        },
    ).json()
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(CONSULTING))

    response = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert response.status_code == 200, response.text
    [row] = response.json()["lines"]
    assert row["outcome"] == "needs_customer_confirmation"
    assert row["invoice"] is None

    listed = logged_in.get("/api/invoices")
    assert listed.json()["items"] == []


def test_an_explicit_customer_id_resolves_needs_customer_confirmation(
    logged_in: TestClient, fiscal_profile: dict[str, Any], legal_entity: dict[str, Any]
) -> None:
    uploader = logged_in.post(
        "/api/customers",
        json={
            "legal_name": "Cliente Caricatore",
            "address": "Via Roma 1",
            "postal_code": "00100",
            "city": "Roma",
            "province": "RM",
            "country": "IT",
        },
    ).json()
    chosen = logged_in.post(
        "/api/customers",
        json={
            "legal_name": "Cliente Scelto",
            "address": "Via Milano 2",
            "postal_code": "20100",
            "city": "Milano",
            "province": "MI",
            "country": "IT",
        },
    ).json()
    document_id = _upload(logged_in, customer_id=uploader["id"], content=_fixture(CONSULTING))

    response = logged_in.post(
        "/api/invoices/import/confirm",
        json={"document_id": document_id, "customer_id": chosen["id"]},
    )
    assert response.status_code == 200, response.text
    [row] = response.json()["lines"]
    assert row["outcome"] == "imported"
    assert row["invoice"]["customer_id"] == chosen["id"]


def test_create_customer_writes_one_new_customer_for_the_whole_batch(
    logged_in: TestClient, fiscal_profile: dict[str, Any], legal_entity: dict[str, Any]
) -> None:
    """REB-367: no `customer_id` and no exact match, but `create_customer` is
    true -- the matched party becomes a new `Customer`, and every invoice in
    the `lotto` batch attaches to that same freshly-created row."""
    uploader = logged_in.post(
        "/api/customers",
        json={
            "legal_name": "Cliente Caricatore",
            "address": "Via Roma 1",
            "postal_code": "00100",
            "city": "Roma",
            "province": "RM",
            "country": "IT",
        },
    ).json()
    document_id = _upload(logged_in, customer_id=uploader["id"], content=_fixture(LOTTO))

    response = logged_in.post(
        "/api/invoices/import/confirm",
        json={"document_id": document_id, "create_customer": True},
    )
    assert response.status_code == 200, response.text
    lines = response.json()["lines"]
    assert [row["outcome"] for row in lines] == ["imported", "imported"]
    customer_ids = {row["invoice"]["customer_id"] for row in lines}
    assert len(customer_ids) == 1
    [customer_id] = customer_ids
    assert customer_id != uploader["id"]

    created = logged_in.get(f"/api/customers/{customer_id}")
    assert created.status_code == 200, created.text
    assert created.json()["vat_number"] == CUSTOMER_VAT_NUMBER


def test_a_collaborator_cannot_confirm(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    """Mirrors `test_invoices_review_api.py::test_a_collaborator_cannot_review`: a
    fresh session for the second role, since `logged_in` and a collaborator session
    both build from the same `client` fixture and would otherwise share one cookie
    jar (see that file's own `_second_actor`)."""
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(CONSULTING))

    email = f"collaboratore-{id(logged_in)}@pigro.it"
    created = logged_in.post(
        "/api/users",
        json={
            "email": email,
            "password": "supersegreta1",
            "name": "Test",
            "role": "collaborator",
        },
    )
    assert created.status_code == 201, created.text
    client = TestClient(logged_in.app, base_url="https://testserver")
    login = client.post("/api/auth/login", json={"email": email, "password": "supersegreta1"})
    assert login.status_code == 200, login.text

    response = client.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert response.status_code == 403, response.text


def test_document_id_is_required(logged_in: TestClient, fiscal_profile: dict[str, Any]) -> None:
    response = logged_in.post("/api/invoices/import/confirm", json={})
    assert response.status_code == 422, response.text
