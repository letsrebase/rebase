"""REB-1032 at the wire: a contract's recurring fee occurrences, the draft that bills one, and the
forecast its issue replaces. The rules are the core's (`test_fee_billing.py`); this proves the
routes answer them in the shapes the contract page reads, with 201 for a draft made and 200 for
one already there, and that the gates hold."""

from datetime import timedelta
from typing import Any

import pytest
from contract_billing_helpers import create_contract, set_up_fiscal
from fastapi.testclient import TestClient

from pigrocrm.core.db.clock import add_months, today_local

TODAY = today_local()


@pytest.fixture
def contract(logged_in: TestClient) -> dict[str, Any]:
    """An active contract: one is born a draft, and only an active one is billed from here."""
    set_up_fiscal(logged_in)
    created = create_contract(logged_in)
    active = logged_in.post(f"/api/contracts/{created['id']}/state", json={"status": "active"})
    assert active.status_code == 200, active.text
    return active.json()


def _another_contract(client: TestClient) -> dict[str, Any]:
    customer = client.post(
        "/api/organizations",
        json={
            "legal_name": "Beta S.r.l.",
            "vat_number": "98765432109",
            "sdi_code": "ABCDEFG",
            "address": "Via Roma 1",
            "postal_code": "20100",
            "city": "Milano",
            "province": "MI",
            "country": "IT",
        },
    )
    assert customer.status_code == 201, customer.text
    created = client.post(
        "/api/contracts",
        json={
            "customer_id": customer.json()["id"],
            "title": "Altro",
            "start": "2026-01-01",
            "renewal_type": "none",
            "termination_notice_days": 30,
            "billing_cadence": "monthly",
            "expense_policy": {"kind": "not_reimbursable"},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _fee_card(client: TestClient, contract: dict[str, Any], **overrides: object) -> dict[str, Any]:
    body = {
        "valid_from": (TODAY - timedelta(days=40)).isoformat(),
        "type": "fixed_recurring",
        "amount": "1000.00",
        "unit": "month",
        "delivery_period": "monthly",
    }
    created = client.post(f"/api/contracts/{contract['id']}/rate-cards", json={**body, **overrides})
    assert created.status_code == 201, created.text
    return created.json()


def _forecasts(client: TestClient) -> list[dict[str, Any]]:
    page = client.get("/api/due-items", params={"state": "forecast", "limit": 200})
    assert page.status_code == 200, page.text
    return page.json()["items"]


def test_the_occurrences_are_listed_with_what_bills_each(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    card = _fee_card(logged_in, contract)

    listed = logged_in.get(f"/api/contracts/{contract['id']}/fee-occurrences")

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["billable"] is True and body["block_reason"] is None
    assert body["currency"] == "EUR"
    first = body["occurrences"][0]
    assert first["rate_card_id"] == card["id"]
    assert first["period_start"] == card["valid_from"]
    assert (first["state"], first["amount"], first["invoice"]) == ("to_bill", "1000.00", None)
    assert (
        first["period_end"]
        == (add_months(TODAY - timedelta(days=40), 1) - timedelta(days=1)).isoformat()
    )
    assert [row["state"] for row in body["occurrences"]] == ["to_bill"] * len(body["occurrences"])
    narrow = logged_in.get(
        f"/api/contracts/{contract['id']}/fee-occurrences",
        params={"from": card["valid_from"], "to": card["valid_from"]},
    ).json()
    assert [row["period_start"] for row in narrow["occurrences"]] == [card["valid_from"]]


def test_billing_makes_the_draft_then_answers_it_and_issuing_removes_the_forecast(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    card = _fee_card(logged_in, contract, valid_from=TODAY.isoformat())
    day = card["valid_from"]
    label = f"Canone Consulenza CTO del {TODAY.strftime('%d/%m/%Y')}"
    assert label in {item["description"] for item in _forecasts(logged_in)}
    url = f"/api/contracts/{contract['id']}/invoice-fee"

    made = logged_in.post(url, json={"rate_card_id": card["id"], "period_start": day})

    assert made.status_code == 201, made.text
    body = made.json()
    assert body["created"] is True
    invoice = body["invoice"]
    assert (invoice["type"], invoice["status"], invoice["taxable_amount"]) == (
        "invoice",
        "draft",
        "1000.00",
    )
    assert invoice["customer_id"] == contract["customer_id"]
    lines = logged_in.get(f"/api/invoices/{invoice['id']}/lines").json()
    assert [
        (line["description"].startswith("Canone Consulenza CTO dal"), line["unit_price"])
        for line in lines
    ] == [(True, "1000.000000")]
    # The draft stands for the occurrence: its own forecast, not the contract's besides it.
    assert label not in {item["description"] for item in _forecasts(logged_in)}
    assert any(
        item["source_kind"] == "sales_draft" and item["source_id"] == invoice["id"]
        for item in _forecasts(logged_in)
    )

    again = logged_in.post(url, json={"rate_card_id": card["id"], "period_start": day})

    assert again.status_code == 200, again.text
    assert again.json()["created"] is False
    assert again.json()["invoice"]["id"] == invoice["id"]
    listed = logged_in.get(
        f"/api/contracts/{contract['id']}/fee-occurrences", params={"from": day, "to": day}
    ).json()
    (row,) = listed["occurrences"]
    assert (row["state"], row["invoice"]["id"], row["invoice"]["status"]) == (
        "drafted",
        invoice["id"],
        "draft",
    )

    issued = logged_in.post(f"/api/invoices/{invoice['id']}/issue", json={})
    assert issued.status_code == 200, issued.text

    assert all(
        item["source_id"] != invoice["id"] and item["description"] != label
        for item in _forecasts(logged_in)
    )
    listed = logged_in.get(
        f"/api/contracts/{contract['id']}/fee-occurrences", params={"from": day, "to": day}
    ).json()
    (row,) = listed["occurrences"]
    assert (row["state"], row["invoice"]["number"]) == ("issued", issued.json()["number"])
    refused = logged_in.post(url, json={"rate_card_id": card["id"], "period_start": day})
    assert refused.status_code == 409, refused.text
    assert refused.json()["invoice_id"] == invoice["id"]


def test_a_date_that_is_not_an_occurrence_is_a_422_that_names_the_dates_that_are(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    card = _fee_card(logged_in, contract)
    off = (TODAY - timedelta(days=39)).isoformat()

    refused = logged_in.post(
        f"/api/contracts/{contract['id']}/invoice-fee",
        json={"rate_card_id": card["id"], "period_start": off},
    )

    assert refused.status_code == 422, refused.text
    assert card["valid_from"] in refused.text


def test_a_rate_card_of_another_contract_is_a_404_and_a_day_card_a_422(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    other = _another_contract(logged_in)
    foreign = _fee_card(logged_in, other)
    day_card = logged_in.get(f"/api/contracts/{contract['id']}/rate-cards").json()[0]
    url = f"/api/contracts/{contract['id']}/invoice-fee"

    assert (
        logged_in.post(
            url, json={"rate_card_id": foreign["id"], "period_start": foreign["valid_from"]}
        ).status_code
        == 404
    )
    assert (
        logged_in.post(
            url, json={"rate_card_id": day_card["id"], "period_start": day_card["valid_from"]}
        ).status_code
        == 422
    )


def test_a_collaborator_may_read_the_occurrences_but_not_bill_one(
    logged_in: TestClient, contract: dict[str, Any]
) -> None:
    card = _fee_card(logged_in, contract)
    created = logged_in.post(
        "/api/users",
        json={
            "email": "collaboratore-canoni@pigro.it",
            "password": "supersegreta1",
            "name": "Test",
            "role": "collaborator",
        },
    )
    assert created.status_code == 201, created.text
    collaborator = TestClient(logged_in.app, base_url="https://testserver")
    login = collaborator.post(
        "/api/auth/login",
        json={"email": "collaboratore-canoni@pigro.it", "password": "supersegreta1"},
    )
    assert login.status_code == 200, login.text

    assert collaborator.get(f"/api/contracts/{contract['id']}/fee-occurrences").status_code == 200
    refused = collaborator.post(
        f"/api/contracts/{contract['id']}/invoice-fee",
        json={"rate_card_id": card["id"], "period_start": card["valid_from"]},
    )
    assert refused.status_code == 403, refused.text


def test_the_occurrences_of_a_contract_that_does_not_exist_are_a_404(logged_in: TestClient) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    assert logged_in.get(f"/api/contracts/{missing}/fee-occurrences").status_code == 404
    assert (
        logged_in.post(
            f"/api/contracts/{missing}/invoice-fee",
            json={"rate_card_id": missing, "period_start": TODAY.isoformat()},
        ).status_code
        == 404
    )
