"""Closing and reopening an IVA period over REST (REB-1036): an unscoped administrator closes and
reopens, a write dated in a closed period is a 409 that names the period, and both acts are on the
timeline. The rule itself is proven in `packages/core/tests/test_iva_lock.py`."""

from decimal import Decimal
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.repository import LegalEntityRepository


def _quarterly_entity(session: Session) -> None:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-standard",
            regime_code="RF01",
            default_vat_rate=Decimal("22.00"),
            iva_frequency="quarterly",
        )
    )
    session.commit()


def _purchase_invoice(client: TestClient, category_id: str, supplier_id: str, day: str):  # noqa: ANN202
    return client.post(
        "/api/purchases",
        json={
            "kind": "invoice",
            "supplier_id": supplier_id,
            "number": f"FA/{day}",
            "category_id": category_id,
            "date": day,
            "amount": "122.00",
            "description": "Consulenza",
            "vat_lines": [{"rate": "22.00", "taxable_amount": "100.00", "vat_amount": "22.00"}],
        },
    )


def test_a_closed_period_refuses_a_purchase_and_reopening_allows_it(
    logged_in: TestClient, api_session: Session
) -> None:
    _quarterly_entity(api_session)
    category_id = logged_in.post("/api/cost-categories", json={"name": "Servizi"}).json()["id"]
    supplier_id = logged_in.post(
        "/api/organizations", json={"legal_name": "Vendor Srl", "roles": ["supplier"]}
    ).json()["id"]

    closed = logged_in.post("/api/iva-period-locks", json={"year": 2026, "number": 1})
    assert closed.status_code == 201, closed.text
    lock = closed.json()
    assert (lock["frequency"], lock["year"], lock["number"]) == ("quarterly", 2026, 1)
    assert lock["label"] == "1° trimestre 2026" and lock["closed_by"] is not None
    period = logged_in.get("/api/iva-periods", params={"year": 2026}).json()[0]
    assert (period["closed"], period["closed_at"]) == (True, lock["closed_at"])

    refused = _purchase_invoice(logged_in, category_id, supplier_id, "2026-02-10")
    assert refused.status_code == 409, refused.text
    assert "il periodo IVA 1° trimestre 2026 è chiuso" in refused.text
    # The next quarter is open.
    assert _purchase_invoice(logged_in, category_id, supplier_id, "2026-05-10").status_code == 201

    reopened = logged_in.delete("/api/iva-period-locks/2026/1")
    assert reopened.status_code == 204
    assert logged_in.get("/api/iva-periods", params={"year": 2026}).json()[0]["closed"] is False
    allowed = _purchase_invoice(logged_in, category_id, supplier_id, "2026-02-10")
    assert allowed.status_code == 201, allowed.text

    timeline = ActivityService(api_session).timeline("iva_period_lock", UUID(lock["id"]))
    assert sorted(entry.kind for entry in timeline) == ["iva_period_closed", "iva_period_reopened"]


def test_a_period_closes_once_and_an_open_one_cannot_be_reopened(
    logged_in: TestClient, api_session: Session
) -> None:
    _quarterly_entity(api_session)
    assert (
        logged_in.post("/api/iva-period-locks", json={"year": 2026, "number": 1}).status_code == 201
    )
    twice = logged_in.post("/api/iva-period-locks", json={"year": 2026, "number": 1})
    assert twice.status_code == 409 and "già chiuso" in twice.text
    assert logged_in.delete("/api/iva-period-locks/2026/2").status_code == 404
    # A period that has not ended cannot be closed, and the number is bounded.
    assert (
        logged_in.post("/api/iva-period-locks", json={"year": 2030, "number": 1}).status_code == 422
    )
    assert (
        logged_in.post("/api/iva-period-locks", json={"year": 2026, "number": 13}).status_code
        == 422
    )


def test_only_an_administrator_closes_or_reopens(
    collaborator_client: TestClient, readonly_client: TestClient
) -> None:
    for client in (collaborator_client, readonly_client):
        closed = client.post("/api/iva-period-locks", json={"year": 2026, "number": 1})
        assert closed.status_code == 403, closed.text
        assert closed.json()["code"] == "permission_denied"
        assert client.delete("/api/iva-period-locks/2026/1").status_code == 403


def test_a_visitor_with_no_session_is_refused(client: TestClient) -> None:
    assert client.post("/api/iva-period-locks", json={"year": 2026, "number": 1}).status_code == 401
