"""The azienda, the regime, a customer, a contract with a rate card: what the contract billing
tests (REB-751, REB-752, REB-753) start from, as plain functions so each test file declares
its own fixtures over them (`aziende_helpers.py` is the same shape)."""

from typing import Any

from fastapi.testclient import TestClient
from legal_entities_helpers import legal_entity_url


def set_up_fiscal(logged_in: TestClient) -> None:
    assert (
        logged_in.put(
            legal_entity_url(logged_in),
            json={
                "legal_name": "Studio Rossi",
                "vat_number": "01234567890",
                "tax_code": "HMCRFT00A01H501K",
                "address": "Via Vittorio Veneto 12",
                "postal_code": "20124",
                "city": "Milano",
                "province": "MI",
                "country": "IT",
                "email": "mario@example.com",
            },
        ).status_code
        == 200
    )
    assert (
        logged_in.put(
            legal_entity_url(logged_in, "/fiscal-profile"), json={"regime_code": "RF19"}
        ).status_code
        == 200
    )


def create_contract(logged_in: TestClient) -> dict[str, Any]:
    customer = logged_in.post(
        "/api/organizations",
        json={
            "legal_name": "Acme S.r.l.",
            "vat_number": "12345678901",
            "sdi_code": "ABCDEFG",
            "address": "Corso Italia 5",
            "postal_code": "00100",
            "city": "Roma",
            "province": "RM",
            "country": "IT",
        },
    )
    assert customer.status_code == 201, customer.text
    created = logged_in.post(
        "/api/contracts",
        json={
            "customer_id": customer.json()["id"],
            "title": "Consulenza CTO",
            "start": "2026-01-01",
            "renewal_type": "none",
            "termination_notice_days": 30,
            "billing_cadence": "monthly",
            "expense_policy": {"kind": "not_reimbursable"},
        },
    )
    assert created.status_code == 201, created.text
    card = logged_in.post(
        f"/api/contracts/{created.json()['id']}/rate-cards",
        json={
            "valid_from": "2026-01-01",
            "type": "daily",
            "amount": "500.00",
            "unit": "day",
        },
    )
    assert card.status_code == 201, card.text
    return created.json()
