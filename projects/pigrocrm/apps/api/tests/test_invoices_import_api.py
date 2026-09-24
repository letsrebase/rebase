"""REST surface of the historical import (slice 9 §3.6)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

COLLABORATORE_PASSWORD = "supersegreta1"


def _second_actor(admin_client: TestClient, ruolo: str) -> TestClient:
    """A genuinely independent session, not `admin_client`'s own cookie jar.

    Copied from `test_invoices_api.py`: `logged_in` and `collaborator_client`
    (`apps/api/tests/conftest.py`) both build their `TestClient` from the same
    function-scoped `client` fixture, so requesting both in one test -- as this test
    would if it asked for `collaborator_client` alongside `customer` (which itself
    needs `logged_in`) -- leaves exactly one login active on that shared cookie jar,
    whichever fixture's own `/api/auth/login` call happened to resolve last. A fresh
    `TestClient(admin_client.app)` over the same ASGI app sidesteps that: same database
    session and storage overrides, independent cookie jar.
    """
    email = f"{ruolo}-{id(admin_client)}@pigro.it"
    created = admin_client.post(
        "/api/users",
        json={"email": email, "password": COLLABORATORE_PASSWORD, "nome": "Test", "ruolo": ruolo},
    )
    assert created.status_code == 201, created.text
    client = TestClient(admin_client.app, base_url="https://testserver")
    response = client.post(
        "/api/auth/login", json={"email": email, "password": COLLABORATORE_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


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
            "ragione_sociale": "Studio Rossi",
            "partita_iva": "01234567890",
            "codice_fiscale": "HMCRFT00A01H501K",
            "indirizzo": "Via Vittorio Veneto 12",
            "cap": "20124",
            "comune": "Milano",
            "provincia": "MI",
            "nazione": "IT",
            "email": "mario@example.com",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def customer(logged_in: TestClient) -> dict[str, Any]:
    response = logged_in.post(
        "/api/customers",
        json={
            "ragione_sociale": "Acme S.r.l.",
            "partita_iva": "12345678901",
            "codice_sdi": "ABCDEFG",
            "indirizzo": "Corso Italia 5",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _body(customer_id: str, numero: int, giorno: str) -> dict[str, Any]:
    return {
        "anno": 2026,
        "numero": numero,
        "data_emissione": giorno,
        "customer_id": customer_id,
        "causale": "900142/0426/Consulenza AI CTO progetto Aurora",
        "righe": [
            {
                "descrizione": "900142/0426/Consulenza AI CTO progetto Aurora",
                "quantita": "9",
                "prezzo_unitario": "300",
                "prezzo_totale": "2700.00",
                "aliquota_iva": "0",
                "natura": "N2.2",
            }
        ],
        "imponibile": "2700.00",
        "imposta": "0.00",
        # The stamp is declared beside the total, never inside it: the identity the
        # service checks is `imponibile + imposta == totale` (slice 3 `sum_totals`, and
        # The previous system's own register, whose «Totale» column always equals «Imp. Reddito»).
        "bollo": "2.00",
        "totale": "2700.00",
        "stato_pagamento": "incassato",
        "data_incasso": "2026-05-20",
    }


def test_an_admin_imports_and_sees_the_undeclared_gaps(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    # Numero 1 first, so the register starts where a register starts: the undeclared-gap
    # scan runs from 1, so importing a 7 on its own would (correctly) report the six
    # holes underneath it, which is a different fact from the one this test is about.
    first = logged_in.post("/api/invoices/import", json=_body(customer["id"], 1, "2026-05-05"))
    assert first.status_code == 201, first.text
    assert first.json()["fattura"]["importata_da"] == "esterno"
    assert first.json()["buchi_non_dichiarati"] == []

    second = logged_in.post("/api/invoices/import", json=_body(customer["id"], 3, "2026-06-05"))
    assert second.status_code == 201, second.text
    assert second.json()["buchi_non_dichiarati"] == [2]

    gaps = logged_in.post(
        "/api/invoices/register/2026/gaps", json={"buchi": [{"numero": 2, "motivo": "annullata"}]}
    )
    assert gaps.status_code == 200, gaps.text
    assert [g["numero"] for g in gaps.json()] == [2]
    assert logged_in.get("/api/invoices/register/2026/gaps").json()[0]["motivo"] == "annullata"

    # No artefact is produced by an import (the router's own docstring says so): the
    # XML the customer holds is the original, and this CRM never generated it, so
    # there is nothing behind `xml_document_id` to serve. That is a 404, not a 409 --
    # the same "not found, not a conflict" verdict `test_a_proforma_refuses_to_produce_xml`
    # (`test_invoices_api.py`) already accepts for a fattura with no XML yet. A 409
    # would only be right for a caller that asked this CRM to *produce* a fresh XML for
    # an imported row (`POST /{id}/artifacts` -> `export_xml`, covered at the service
    # layer by `packages/core/tests/test_invoice_import.py`); plain `GET .../xml` never
    # takes that path.
    xml = logged_in.get(f"/api/invoices/{first.json()['fattura']['id']}/xml")
    assert xml.status_code == 404, xml.text
    assert xml.json()["code"] == "not_found"


def test_a_caller_cannot_hand_declare_importata_da_as_fatturapa(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    emitter: dict[str, Any],
) -> None:
    """`InvoiceImport.importata_da` stays fixed to `Literal["esterno"]` (REB-368,
    design §5 item 4/§7 item 6): a caller on this hand-declare door has parsed no
    document and holds no `xml_document_id`, so letting it claim `"fatturapa"` would
    be an unbacked claim the invoice list's own badge would then repeat dishonestly.
    Only `InvoiceService.confirm_import` (which has actually parsed a document) can
    set that value, through `import_issued`'s own keyword-only parameter -- never
    through this schema."""
    body = _body(customer["id"], 1, "2026-05-05")
    body["importata_da"] = "fatturapa"
    response = logged_in.post("/api/invoices/import", json=body)
    assert response.status_code == 422, response.text


def test_a_collaborator_cannot_import(logged_in: TestClient, customer: dict[str, Any]) -> None:
    """`collaborator_client` is deliberately not used here: it shares its cookie jar
    with `logged_in`, and `customer` needs `logged_in` -- combining the two would leave
    whichever login resolves last active for the whole test (see `_second_actor`'s
    docstring above), so the request would silently run as the wrong actor instead of
    testing anything. `_second_actor` builds a truly independent session instead.
    """
    collaboratore = _second_actor(logged_in, "collaboratore")
    response = collaboratore.post(
        "/api/invoices/import", json=_body(customer["id"], 7, "2026-05-05")
    )
    assert response.status_code == 403, response.text


def test_a_number_beyond_the_register_is_a_422(
    logged_in: TestClient, customer: dict[str, Any]
) -> None:
    """`MAX_NUMERO` is a bound on `InvoiceImport`, so FastAPI refuses the body before the
    router builds a service or the service takes the year's counter lock -- a 422 from
    the schema, not a 409 from the register."""
    response = logged_in.post(
        "/api/invoices/import", json=_body(customer["id"], 900142, "2026-05-05")
    )
    assert response.status_code == 422, response.text
