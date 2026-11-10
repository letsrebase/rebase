"""The IVA settlements of a year over REST (REB-1021): an admin reads what the daily pass
wrote, everyone else is refused. The arithmetic is proven in `test_iva_settlement.py`."""

import datetime as dt
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.fiscal.iva_settlement import IvaSettlementService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.schemas import IvaPeriod
from pigrocrm.core.invoices.models import Invoice
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
    session.add(
        Invoice(
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
    )
    session.commit()


def test_an_admin_reads_the_years_settlements_with_their_due_item(
    logged_in: TestClient, api_session: Session
) -> None:
    _ordinary_entity_with_a_sale(api_session)
    IvaSettlementService(api_session).settle_iva_period(
        IvaPeriod(year=2026, number=1), Actor(id=uuid4(), type="user", role="admin")
    )

    response = logged_in.get("/api/iva-settlements", params={"year": 2026})

    assert response.status_code == 200, response.text
    (row,) = response.json()
    assert (row["frequency"], row["period_number"], row["outcome"]) == ("quarterly", 1, "due")
    assert (row["sales_vat"], row["surcharge"], row["result"]) == ("220.00", "2.20", "222.20")
    assert row["payment_due_on"] == "2026-05-18" and row["due_item_id"]
    assert logged_in.get("/api/iva-settlements", params={"year": 2025}).json() == []


def test_the_year_is_required(logged_in: TestClient) -> None:
    assert logged_in.get("/api/iva-settlements").status_code == 422


def test_a_collaborator_and_a_reader_are_refused(
    collaborator_client: TestClient, readonly_client: TestClient
) -> None:
    for client in (collaborator_client, readonly_client):
        refused = client.get("/api/iva-settlements", params={"year": 2026})
        assert refused.status_code == 403
        assert refused.json()["code"] == "permission_denied"


def test_a_visitor_with_no_session_is_refused(client: TestClient) -> None:
    assert client.get("/api/iva-settlements", params={"year": 2026}).status_code == 401
