"""REST surface of the historical import (slice 9 §3.6)."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url
from sqlalchemy.orm import Session

from pigrocrm.core.legal_entities.models import LegalEntity

COLLABORATOR_PASSWORD = "supersegreta1"


def _second_actor(admin_client: TestClient, role: str) -> TestClient:
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
    email = f"{role}-{id(admin_client)}@pigro.it"
    created = admin_client.post(
        "/api/users",
        json={"email": email, "password": COLLABORATOR_PASSWORD, "name": "Test", "role": role},
    )
    assert created.status_code == 201, created.text
    client = TestClient(admin_client.app, base_url="https://testserver")
    response = client.post(
        "/api/auth/login", json={"email": email, "password": COLLABORATOR_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


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
            "legal_name": "Studio Rossi",
            "vat_number": "01234567890",
            "tax_code": "HMCRFT00A01H501K",
            "address": "Via Vittorio Veneto 12",
            "cap": "20124",
            "city": "Milano",
            "province": "MI",
            "country": "IT",
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
            "legal_name": "Acme S.r.l.",
            "vat_number": "12345678901",
            "sdi_code": "ABCDEFG",
            "address": "Corso Italia 5",
            "cap": "00100",
            "city": "Roma",
            "province": "RM",
            "country": "IT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _body(customer_id: str, number: int, day: str) -> dict[str, Any]:
    return {
        "year": 2026,
        "number": number,
        "issue_date": day,
        "customer_id": customer_id,
        "memo": "900142/0426/Consulenza AI CTO progetto Aurora",
        "lines": [
            {
                "description": "900142/0426/Consulenza AI CTO progetto Aurora",
                "quantity": "9",
                "unit_price": "300",
                "total_price": "2700.00",
                "vat_rate": "0",
                "vat_nature": "N2.2",
            }
        ],
        "taxable_amount": "2700.00",
        "vat_amount": "0.00",
        # The stamp is declared beside the total, never inside it: the identity the
        # service checks is `imponibile + imposta == totale` (slice 3 `sum_totals`, and
        # The previous system's own register, whose «Totale» column always equals «Imp. Reddito»).
        "stamp_duty": "2.00",
        "total": "2700.00",
        "payment_status": "paid",
        "payment_date": "2026-05-20",
    }


def test_an_admin_imports_and_sees_the_undeclared_gaps(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
) -> None:
    # Numero 1 first, so the register starts where a register starts: the undeclared-gap
    # scan runs from 1, so importing a 7 on its own would (correctly) report the six
    # holes underneath it, which is a different fact from the one this test is about.
    first = logged_in.post("/api/invoices/import", json=_body(customer["id"], 1, "2026-05-05"))
    assert first.status_code == 201, first.text
    assert first.json()["invoice"]["imported_from"] == "external"
    assert first.json()["undeclared_gaps"] == []

    second = logged_in.post("/api/invoices/import", json=_body(customer["id"], 3, "2026-06-05"))
    assert second.status_code == 201, second.text
    assert second.json()["undeclared_gaps"] == [2]

    gaps = logged_in.post(
        "/api/invoices/register/2026/gaps", json={"gaps": [{"number": 2, "reason": "cancelled"}]}
    )
    assert gaps.status_code == 200, gaps.text
    assert [g["number"] for g in gaps.json()] == [2]
    assert logged_in.get("/api/invoices/register/2026/gaps").json()[0]["reason"] == "cancelled"

    # No artefact is produced by an import (the router's own docstring says so): the
    # XML the customer holds is the original, and this CRM never generated it, so
    # there is nothing behind `xml_document_id` to serve. That is a 404, not a 409 --
    # the same "not found, not a conflict" verdict `test_a_proforma_refuses_to_produce_xml`
    # (`test_invoices_api.py`) already accepts for a fattura with no XML yet. A 409
    # would only be right for a caller that asked this CRM to *produce* a fresh XML for
    # an imported row (`POST /{id}/artifacts` -> `export_xml`, covered at the service
    # layer by `packages/core/tests/test_invoice_import.py`); plain `GET .../xml` never
    # takes that path.
    xml = logged_in.get(f"/api/invoices/{first.json()['invoice']['id']}/xml")
    assert xml.status_code == 404, xml.text
    assert xml.json()["code"] == "not_found"


def test_to_caller_cannot_hand_declare_imported_from_as_fatturapa(
    logged_in: TestClient,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
) -> None:
    """`InvoiceImport.imported_from` stays fixed to `Literal["external"]` (REB-368,
    design §5 item 4/§7 item 6): a caller on this hand-declare door has parsed no
    document and holds no `xml_document_id`, so letting it claim `"fatturapa"` would
    be an unbacked claim the invoice list's own badge would then repeat dishonestly.
    Only `InvoiceService.confirm_import` (which has actually parsed a document) can
    set that value, through `import_issued`'s own keyword-only parameter -- never
    through this schema."""
    body = _body(customer["id"], 1, "2026-05-05")
    body["imported_from"] = "fatturapa"
    response = logged_in.post("/api/invoices/import", json=body)
    assert response.status_code == 422, response.text


def test_a_collaborator_cannot_import(logged_in: TestClient, customer: dict[str, Any]) -> None:
    """`collaborator_client` is deliberately not used here: it shares its cookie jar
    with `logged_in`, and `customer` needs `logged_in` -- combining the two would leave
    whichever login resolves last active for the whole test (see `_second_actor`'s
    docstring above), so the request would silently run as the wrong actor instead of
    testing anything. `_second_actor` builds a truly independent session instead.
    """
    collaborator = _second_actor(logged_in, "collaborator")
    response = collaborator.post(
        "/api/invoices/import", json=_body(customer["id"], 7, "2026-05-05")
    )
    assert response.status_code == 403, response.text


def test_a_number_beyond_the_register_is_a_422(
    logged_in: TestClient, customer: dict[str, Any]
) -> None:
    """`MAX_NUMBER` is a bound on `InvoiceImport`, so FastAPI refuses the body before the
    router builds a service or the service takes the year's counter lock -- a 422 from
    the schema, not a 409 from the register."""
    response = logged_in.post(
        "/api/invoices/import", json=_body(customer["id"], 900142, "2026-05-05")
    )
    assert response.status_code == 422, response.text


def test_the_import_and_the_gaps_are_keyed_by_the_legal_entity_named(
    logged_in: TestClient,
    api_session: Session,
    customer: dict[str, Any],
    fiscal_profile: dict[str, Any],
    legal_entity: dict[str, Any],
) -> None:
    """REB-620, spec §1.4: a second azienda, written by row since no route creates one
    before milestone 5, has a register of its own. The same number lands on both, and
    the gaps of one never show on the other's route."""
    second = LegalEntity(
        name="rebase",
        legal_name="Rebase S.r.l.",
        vat_number="09876543210",
        address="Via Po 1",
        cap="10100",
        city="Torino",
        province="TO",
        country="IT",
    )
    api_session.add(second)
    api_session.flush()
    saved = logged_in.put(
        f"/api/legal-entities/{second.id}/fiscal-profile", json={"regime_code": "RF19"}
    )
    assert saved.status_code == 200, saved.text

    second_customer = logged_in.post(
        "/api/customers",
        json={"legal_name": "Acme Ltd", "country": "GB", "legal_entity_id": str(second.id)},
    ).json()
    refused = logged_in.post(
        "/api/invoices/import",
        params={"legal_entity_id": str(second.id)},
        json=_body(customer["id"], 1, "2026-05-05"),
    )
    assert refused.status_code == 422, refused.text
    assert "altra azienda" in refused.text
    assert logged_in.get("/api/invoices").json()["items"] == []

    default = logged_in.post("/api/invoices/import", json=_body(customer["id"], 1, "2026-05-05"))
    assert default.status_code == 201, default.text
    assert default.json()["invoice"]["legal_entity_id"] == legal_entity["id"]

    other = logged_in.post(
        "/api/invoices/import",
        params={"legal_entity_id": str(second.id)},
        json=_body(second_customer["id"], 1, "2026-05-05"),
    )
    assert other.status_code == 201, other.text
    assert other.json()["invoice"]["legal_entity_id"] == str(second.id)
    assert other.json()["invoice"]["number"] == 1

    third = logged_in.post(
        "/api/invoices/import",
        params={"legal_entity_id": str(second.id)},
        json=_body(second_customer["id"], 3, "2026-06-05"),
    )
    assert third.status_code == 201, third.text
    assert third.json()["undeclared_gaps"] == [2]
    declared = logged_in.post(
        "/api/invoices/register/2026/gaps",
        params={"legal_entity_id": str(second.id)},
        json={"gaps": [{"number": 2, "reason": "cancelled"}]},
    )
    assert declared.status_code == 200, declared.text
    assert [g["number"] for g in declared.json()] == [2]
    assert logged_in.get("/api/invoices/register/2026/gaps").json() == []
    listed = logged_in.get(
        "/api/invoices/register/2026/gaps", params={"legal_entity_id": str(second.id)}
    )
    assert [g["number"] for g in listed.json()] == [2]
    # An azienda that does not exist is a 404, never the default's register in silence.
    missing = logged_in.get(
        "/api/invoices/register/2026/gaps",
        params={"legal_entity_id": "00000000-0000-7000-8000-000000000bad"},
    )
    assert missing.status_code == 404, missing.text
