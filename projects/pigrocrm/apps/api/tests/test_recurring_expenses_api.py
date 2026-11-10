"""The recurrence rules of money going out over REST (REB-1022): create, list and stop. The
forecasts and the replacement by a purchase are proven in `test_recurring_expenses.py`."""

import datetime as dt
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.timetracking.models import CostCategory

NEXT_MONTH = (dt.date.today().replace(day=1) + dt.timedelta(days=32)).replace(day=1)


def _supplier_and_category(session: Session) -> tuple[UUID, UUID]:
    supplier = Organization(legal_name="Affitti Srl", country="IT", roles=["supplier"])
    category = CostCategory(name="Affitto", position=0, code=None)
    session.add_all([supplier, category])
    session.commit()
    return supplier.id, category.id


def _body(supplier: UUID, category: UUID) -> dict[str, object]:
    return {
        "supplier_id": str(supplier),
        "category_id": str(category),
        "description": "Affitto ufficio",
        "amount": "800.00",
        "cadence": "monthly",
        "first_due_date": NEXT_MONTH.isoformat(),
    }


def test_a_rule_is_created_listed_and_stopped_with_its_forecasts(
    logged_in: TestClient, api_session: Session
) -> None:
    supplier, category = _supplier_and_category(api_session)

    created = logged_in.post("/api/recurring-expenses", json=_body(supplier, category))
    assert created.status_code == 201, created.text
    rule = created.json()
    assert (rule["amount"], rule["cadence"], rule["active"]) == ("800.00", "monthly", True)
    forecasts = list(
        api_session.scalars(
            select(DueItem).where(
                DueItem.source_id == UUID(rule["id"]), DueItem.state == "forecast"
            )
        )
    )
    assert forecasts and all(item.direction == "out" for item in forecasts)

    assert [row["id"] for row in logged_in.get("/api/recurring-expenses").json()] == [rule["id"]]

    stopped = logged_in.post(f"/api/recurring-expenses/{rule['id']}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["active"] is False
    assert logged_in.get("/api/recurring-expenses").json() == []
    assert len(logged_in.get("/api/recurring-expenses?include_stopped=true").json()) == 1
    api_session.expire_all()
    assert not list(
        api_session.scalars(
            select(DueItem).where(
                DueItem.source_id == UUID(rule["id"]), DueItem.state == "forecast"
            )
        )
    )
    assert logged_in.post(f"/api/recurring-expenses/{rule['id']}/stop").status_code == 409


def test_a_customer_is_not_a_supplier_and_an_unknown_cadence_is_a_422(
    logged_in: TestClient, api_session: Session
) -> None:
    _supplier, category = _supplier_and_category(api_session)
    customer = Organization(legal_name="Cliente Srl", country="IT")
    api_session.add(customer)
    api_session.commit()

    refused = logged_in.post("/api/recurring-expenses", json=_body(customer.id, category))
    assert refused.status_code == 422
    body = _body(_supplier, category) | {"cadence": "weekly"}
    assert logged_in.post("/api/recurring-expenses", json=body).status_code == 422


def test_a_reader_reads_and_cannot_write(readonly_client: TestClient, api_session: Session) -> None:
    supplier, category = _supplier_and_category(api_session)
    assert readonly_client.get("/api/recurring-expenses").status_code == 200
    refused = readonly_client.post("/api/recurring-expenses", json=_body(supplier, category))
    assert refused.status_code == 403


def test_a_visitor_with_no_session_is_refused(client: TestClient) -> None:
    assert client.get("/api/recurring-expenses").status_code == 401
