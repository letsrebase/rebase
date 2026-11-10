"""The IVA periods and registers over REST (REB-1024): an administrator reads, everyone else is
refused. What they add up to is proven in `test_iva_register.py`."""

import datetime as dt
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization


def _ordinary_entity_with_a_sale(session: Session) -> None:
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
    customer = Organization(legal_name="Acme S.r.l.", country="IT")
    session.add(customer)
    session.flush()
    invoice = Invoice(
        customer_id=customer.id,
        legal_entity_id=entity.id,
        type="invoice",
        status="issued",
        year=2026,
        number=1,
        issue_date=dt.date(2026, 2, 3),
        taxable_amount=Decimal("1000.00"),
        vat_amount=Decimal("220.00"),
        total=Decimal("1220.00"),
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            line_number=1,
            description="Consulenza",
            quantity=Decimal("1"),
            unit_price=Decimal("1000.00"),
            total_price=Decimal("1000.00"),
            vat_rate=Decimal("22.00"),
        )
    )
    session.commit()


def test_an_admin_reads_the_entities_the_periods_and_both_registers(
    logged_in: TestClient, api_session: Session
) -> None:
    _ordinary_entity_with_a_sale(api_session)

    entities = logged_in.get("/api/iva-periods/entities")
    periods = logged_in.get("/api/iva-periods", params={"year": 2026})
    sales = logged_in.get("/api/iva-registers/sales", params={"year": 2026, "number": 1})
    purchases = logged_in.get("/api/iva-registers/purchases", params={"year": 2026, "number": 1})

    assert entities.status_code == 200, entities.text
    assert [e["frequency"] for e in entities.json()] == ["quarterly"]
    assert periods.status_code == 200, periods.text
    assert [(p["number"], p["state"]) for p in periods.json()][:2] == [(1, "ended"), (2, "ended")]
    assert periods.json()[0]["ladder"]["sales_vat"] == "220.00"
    assert sales.status_code == 200, sales.text
    (row,) = sales.json()["items"]
    assert (row["number"], row["customer_name"], row["vat_amount"]) == (1, "Acme S.r.l.", "220.00")
    assert row["groups"] == [
        {"rate": "22.00", "nature": None, "taxable_amount": "1000.00", "vat_amount": "220.00"}
    ]
    assert sales.json()["totals"]["vat_due"] == "220.00"
    assert purchases.status_code == 200, purchases.text
    assert purchases.json()["items"] == []


def test_the_year_and_the_period_are_required_and_bounded(logged_in: TestClient) -> None:
    assert logged_in.get("/api/iva-periods").status_code == 422
    assert logged_in.get("/api/iva-registers/sales", params={"year": 2026}).status_code == 422
    assert (
        logged_in.get("/api/iva-registers/sales", params={"year": 2026, "number": 13}).status_code
        == 422
    )
    assert (
        logged_in.get(
            "/api/iva-registers/purchases", params={"year": 2026, "number": 1, "limit": 0}
        ).status_code
        == 422
    )


def test_a_flat_rate_space_has_no_entity_and_no_period(logged_in: TestClient) -> None:
    assert logged_in.get("/api/iva-periods/entities").json() == []
    assert logged_in.get("/api/iva-periods", params={"year": 2026}).json() == []
    refused = logged_in.get("/api/iva-registers/sales", params={"year": 2026, "number": 1})
    assert refused.status_code == 422


def test_a_collaborator_and_a_reader_are_refused(
    collaborator_client: TestClient, readonly_client: TestClient
) -> None:
    for client in (collaborator_client, readonly_client):
        for path, params in (
            ("/api/iva-periods/entities", {}),
            ("/api/iva-periods", {"year": 2026}),
            ("/api/iva-registers/sales", {"year": 2026, "number": 1}),
            ("/api/iva-registers/purchases", {"year": 2026, "number": 1}),
        ):
            refused = client.get(path, params=params)
            assert refused.status_code == 403, path
            assert refused.json()["code"] == "permission_denied"


def test_a_visitor_with_no_session_is_refused(client: TestClient) -> None:
    assert client.get("/api/iva-periods", params={"year": 2026}).status_code == 401
