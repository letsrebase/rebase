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

FIXTURES = (
    Path(__file__).resolve().parents[3] / "packages" / "core" / "tests" / "fixtures" / "fatturapa"
)
CONSULENZA = "fpr12-consulenza-marzo.xml"
LOTTO = "fpr12-lotto-due-fatture.xml"
# The fixtures' own `CedentePrestatore` (fornitore) -- matching this on the emitter
# profile is what makes them "outgoing" for the account holder.
FORNITORE_PIVA = "01234567890"
FORNITORE_CF = "BNCCHR85M41H501Z"
# Their `CessionarioCommittente` (cliente).
CLIENTE_PIVA = "09876543210"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def fiscal_profile(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.put("/api/fiscal-profile", json={"codice_regime": "RF19"})
    assert response.status_code == 200, response.text
    return response.json()


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


def test_confirming_a_fresh_document_writes_the_register_and_a_second_confirm_is_a_no_op(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(CONSULENZA))

    first = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert first.status_code == 200, first.text
    [row] = first.json()["righe"]
    assert row["outcome"] == "imported"
    assert row["fattura"]["numero"] == 6
    assert row["fattura"]["customer_id"] == customer["id"]
    # REB-368: a document this CRM parsed itself gets "fatturapa", not InvoiceImport's
    # own hand-declared "esterno" default.
    assert row["fattura"]["importata_da"] == "fatturapa"
    assert row["fattura"]["xml_document_id"] == document_id
    assert row["fattura"]["xml_hash_sha256"] == hashlib.sha256(_fixture(CONSULENZA)).hexdigest()

    second = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert second.status_code == 200, second.text
    [second_row] = second.json()["righe"]
    assert second_row["outcome"] == "already_present"
    assert second_row["fattura"]["id"] == row["fattura"]["id"]

    # The write really landed on the register, unlike the read-only review.
    listed = logged_in.get("/api/invoices")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [row["fattura"]["id"]]


def test_a_batch_sourced_document_leaves_xml_document_id_null(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(LOTTO))

    response = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert response.status_code == 200, response.text
    righe = response.json()["righe"]
    assert len(righe) == 2
    assert all(row["outcome"] == "imported" for row in righe)
    assert all(row["fattura"]["xml_document_id"] is None for row in righe)
    assert all(row["fattura"]["xml_hash_sha256"] is None for row in righe)


def test_a_document_no_adapter_recognises_is_unclaimed_not_a_500(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    document_id = _upload(logged_in, customer_id=customer["id"], content=b"not a fattura at all")
    response = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert response.status_code == 200, response.text
    [row] = response.json()["righe"]
    assert row["outcome"] == "unclaimed"
    assert row["fattura"] is None


def test_no_matching_customer_reports_needs_customer_confirmation_and_writes_nothing(
    logged_in: TestClient, fiscal_profile: dict[str, Any], emitter: dict[str, Any]
) -> None:
    customer = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Cliente Senza Match",
            "indirizzo": "Via Roma 1",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        },
    ).json()
    document_id = _upload(logged_in, customer_id=customer["id"], content=_fixture(CONSULENZA))

    response = logged_in.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert response.status_code == 200, response.text
    [row] = response.json()["righe"]
    assert row["outcome"] == "needs_customer_confirmation"
    assert row["fattura"] is None

    listed = logged_in.get("/api/invoices")
    assert listed.json()["items"] == []


def test_an_explicit_customer_id_resolves_needs_customer_confirmation(
    logged_in: TestClient, fiscal_profile: dict[str, Any], emitter: dict[str, Any]
) -> None:
    uploader = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Cliente Caricatore",
            "indirizzo": "Via Roma 1",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        },
    ).json()
    chosen = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Cliente Scelto",
            "indirizzo": "Via Milano 2",
            "cap": "20100",
            "comune": "Milano",
            "provincia": "MI",
            "nazione": "IT",
        },
    ).json()
    document_id = _upload(logged_in, customer_id=uploader["id"], content=_fixture(CONSULENZA))

    response = logged_in.post(
        "/api/invoices/import/confirm",
        json={"document_id": document_id, "customer_id": chosen["id"]},
    )
    assert response.status_code == 200, response.text
    [row] = response.json()["righe"]
    assert row["outcome"] == "imported"
    assert row["fattura"]["customer_id"] == chosen["id"]


def test_create_customer_writes_one_new_customer_for_the_whole_batch(
    logged_in: TestClient, fiscal_profile: dict[str, Any], emitter: dict[str, Any]
) -> None:
    """REB-367: no `customer_id` and no exact match, but `create_customer` is
    true -- the matched party becomes a new `Customer`, and every invoice in
    the `lotto` batch attaches to that same freshly-created row."""
    uploader = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Cliente Caricatore",
            "indirizzo": "Via Roma 1",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        },
    ).json()
    document_id = _upload(logged_in, customer_id=uploader["id"], content=_fixture(LOTTO))

    response = logged_in.post(
        "/api/invoices/import/confirm",
        json={"document_id": document_id, "create_customer": True},
    )
    assert response.status_code == 200, response.text
    righe = response.json()["righe"]
    assert [row["outcome"] for row in righe] == ["imported", "imported"]
    customer_ids = {row["fattura"]["customer_id"] for row in righe}
    assert len(customer_ids) == 1
    [customer_id] = customer_ids
    assert customer_id != uploader["id"]

    created = logged_in.get(f"/api/customers/{customer_id}")
    assert created.status_code == 200, created.text
    assert created.json()["partita_iva"] == CLIENTE_PIVA


def test_a_collaborator_cannot_confirm(
    logged_in: TestClient,
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    """Mirrors `test_invoices_review_api.py::test_a_collaborator_cannot_review`: a
    fresh session for the second role, since `logged_in` and a collaborator session
    both build from the same `client` fixture and would otherwise share one cookie
    jar (see that file's own `_second_actor`)."""
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

    response = client.post("/api/invoices/import/confirm", json={"document_id": document_id})
    assert response.status_code == 403, response.text


def test_document_id_is_required(logged_in: TestClient, fiscal_profile: dict[str, Any]) -> None:
    response = logged_in.post("/api/invoices/import/confirm", json={})
    assert response.status_code == 422, response.text
