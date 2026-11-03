"""The azienda over HTTP (REB-624, spec 2026-10-03 §1.6, §3): the proposal route, the
customer routes that take an `legal_entity_id`, and every list that narrows to one azienda.

The second azienda is written by row, since no route creates one before milestone 5
(§9). The rows under test are made through the routes where a route exists and through
the core services where creating them over HTTP would need the fiscal set-up the test
is not about (a contract, an invoice).
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.schemas import ContractCreate
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")


def _default_id(client: TestClient) -> str:
    legal_entities = client.get("/api/legal-entities").json()
    assert legal_entities and legal_entities[0]["is_default"]
    return str(legal_entities[0]["id"])


def _second_legal_entity(session: Session, name: str = "rebase ltd", country: str = "GB") -> str:
    row = LegalEntity(name=name, legal_name=f"{name.title()}", country=country)
    session.add(row)
    session.flush()
    return str(row.id)


def _customer(client: TestClient, name: str, **extra: Any) -> dict[str, Any]:
    created = client.post("/api/customers", json={"legal_name": name, **extra})
    assert created.status_code == 201, created.text
    return dict(created.json())


def _deal(client: TestClient, customer_id: str, name: str) -> str:
    assert client.post("/api/pipeline-stages/seed").status_code == 200
    created = client.post("/api/deals", json={"name": name, "customer_id": customer_id})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _ids(client: TestClient, path: str, **params: Any) -> set[str]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"]}


# --- the proposal -----------------------------------------------------------------


def test_the_proposal_route_answers_the_legal_entity_the_nation_proposes(
    logged_in: TestClient, api_session: Session
) -> None:
    default = _default_id(logged_in)
    british = _second_legal_entity(api_session)

    def proposed(**params: Any) -> str:
        response = logged_in.get("/api/legal-entities/suggestion", params=params)
        assert response.status_code == 200, response.text
        return str(response.json()["id"])

    assert proposed(country="GB") == british
    assert proposed(country="gb") == british
    # No azienda of that nation: the one foreign azienda for a foreign customer.
    assert proposed(country="DE") == british
    assert proposed(country="IT") == default
    assert proposed() == default


def test_the_proposal_is_readable_by_every_role_and_refuses_a_malformed_nation(
    readonly_client: TestClient,
) -> None:
    assert readonly_client.get("/api/legal-entities/suggestion").status_code == 200
    assert (
        readonly_client.get("/api/legal-entities/suggestion", params={"country": "ITA"}).status_code
        == 422
    )


# --- the customer -----------------------------------------------------------------


def test_to_customer_gets_the_proposal_or_keeps_the_legal_entity_named(
    logged_in: TestClient, api_session: Session
) -> None:
    default = _default_id(logged_in)
    british = _second_legal_entity(api_session)
    proposed = _customer(logged_in, "Overseas Ltd", country="GB")
    assert proposed["legal_entity_id"] == british
    named = _customer(logged_in, "Oltre Ltd", country="GB", legal_entity_id=default)
    assert named["legal_entity_id"] == default
    read_back = logged_in.get(f"/api/customers/{named['id']}").json()
    assert read_back["legal_entity_id"] == default

    moved = logged_in.patch(f"/api/customers/{named['id']}", json={"legal_entity_id": british})
    assert moved.status_code == 200, moved.text
    assert moved.json()["legal_entity_id"] == british

    unknown = logged_in.post(
        "/api/customers", json={"legal_name": "Nessuna", "legal_entity_id": str(uuid4())}
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "not_found"


def test_an_inactive_legal_entity_is_refused_on_to_customer_naming_the_field(
    logged_in: TestClient, api_session: Session
) -> None:
    closed = LegalEntity(name="chiusa", legal_name="Chiusa S.r.l.", active=False)
    api_session.add(closed)
    api_session.flush()
    refused = logged_in.post(
        "/api/customers", json={"legal_name": "X", "legal_entity_id": str(closed.id)}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["field"] == "legal_entity_id"


# --- the lists, the calendar and the search -----------------------------------------


def test_every_list_route_narrows_to_one_legal_entity(
    logged_in: TestClient, api_session: Session, tmp_path: Path
) -> None:
    default = _default_id(logged_in)
    second = _second_legal_entity(api_session, name="rebase", country="IT")
    fiscal = FiscalProfileService(api_session)
    fiscal.upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    fiscal.upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN, legal_entity_id=UUID(second))

    acme = _customer(logged_in, "Acme S.r.l.")["id"]
    beta = _customer(logged_in, "Beta S.r.l.", legal_entity_id=second)["id"]
    deal_a = _deal(logged_in, acme, "Acme deal")
    deal_b = _deal(logged_in, beta, "Beta deal")
    for customer in (acme, beta):
        ContractService(api_session).create(
            ContractCreate(
                customer_id=UUID(customer),
                title="Consulenza",
                start=date(2026, 1, 1),
                renewal_type="none",
                termination_notice_days=30,
                billing_cadence="monthly",
                expense_policy={"kind": "not_reimbursable"},
            ),
            ADMIN,
        )
        InvoiceService(api_session, LocalFileStorage(tmp_path)).create(
            InvoiceCreate(
                customer_id=UUID(customer),
                lines=[InvoiceLineIn(description="Consulenza", unit_price=Decimal("1.00"))],
            ),
            ADMIN,
        )
    for customer, title in ((acme, "Acme brief"), (beta, "Beta brief")):
        created = logged_in.post(
            "/api/documents", json={"customer_id": customer, "type": "document", "title": title}
        )
        assert created.status_code == 201, created.text
    category = logged_in.post("/api/cost-categories", json={"name": "Viaggi"}).json()["id"]
    me = str(logged_in.get("/api/auth/me").json()["id"])
    for deal, day in ((deal_a, "2026-03-02"), (deal_b, "2026-03-03")):
        cost = logged_in.post(
            "/api/costs",
            json={
                "deal_id": deal,
                "category_id": category,
                "date": day,
                "amount": "10.00",
                "description": "Treno",
            },
        )
        assert cost.status_code == 201, cost.text
        hours = logged_in.post(
            "/api/time-entries",
            json={
                "deal_id": deal,
                "user_id": me,
                "date": day,
                "hours": "1.00",
                "description": "Sviluppo",
            },
        )
        assert hours.status_code == 201, hours.text
    shared = logged_in.post(
        "/api/costs",
        json={
            "category_id": category,
            "date": "2026-03-04",
            "amount": "5.00",
            "description": "Software",
        },
    )
    assert shared.status_code == 201, shared.text
    assert shared.json()["legal_entity_id"] is None

    for path in (
        "/api/customers",
        "/api/deals",
        "/api/contracts",
        "/api/documents",
        "/api/invoices",
        "/api/costs",
        "/api/time-entries",
    ):
        on_default = _ids(logged_in, path, legal_entity_id=default)
        on_second = _ids(logged_in, path, legal_entity_id=second)
        everything = _ids(logged_in, path)
        assert on_default and on_second, path
        assert on_default.isdisjoint(on_second), path
        assert on_default | on_second <= everything, path
        assert _ids(logged_in, path, legal_entity_id=str(uuid4())) == set(), path
        assert logged_in.get(path, params={"legal_entity_id": "non-un-id"}).status_code == 422, path
    # The shared cost is in the whole list and in neither azienda's.
    assert shared.json()["id"] in _ids(logged_in, "/api/costs")
    assert shared.json()["id"] not in _ids(logged_in, "/api/costs", legal_entity_id=default)

    # The calendar: the hours through their deal, the activities untouched.
    whole = logged_in.get("/api/calendar", params={"month": "2026-03", "all": "true"}).json()
    narrowed = logged_in.get(
        "/api/calendar", params={"month": "2026-03", "all": "true", "legal_entity_id": second}
    ).json()
    assert [g["day"] for g in whole["days"] if g["hours"]] == ["2026-03-02", "2026-03-03"]
    assert [g["day"] for g in narrowed["days"] if g["hours"]] == ["2026-03-03"]

    # The search: «brief» finds both documents, and one under the second azienda.
    everyone = logged_in.get("/api/search", params={"q": "brief"}).json()
    narrowed_search = logged_in.get(
        "/api/search", params={"q": "brief", "legal_entity_id": second}
    ).json()
    documents = next(g for g in everyone["groups"] if g["entity"] == "document")
    assert documents["total"] == 2
    narrowed_documents = next(g for g in narrowed_search["groups"] if g["entity"] == "document")
    assert [h["label"] for h in narrowed_documents["hits"]] == ["Beta brief"]


def test_the_openapi_document_declares_the_legal_entity_on_every_list_and_the_proposal(
    logged_in: TestClient,
) -> None:
    schema = logged_in.get("/openapi.json").json()
    for path in (
        "/api/customers",
        "/api/deals",
        "/api/contracts",
        "/api/documents",
        "/api/invoices",
        "/api/costs",
        "/api/time-entries",
        "/api/calendar",
        "/api/search",
    ):
        names = {p["name"] for p in schema["paths"][path]["get"].get("parameters", [])}
        assert "legal_entity_id" in names, path
    assert "get" in schema["paths"]["/api/legal-entities/suggestion"]
    for model in ("CustomerRead", "DealRead", "ContractRead", "DocumentRead", "CostRead"):
        assert "legal_entity_id" in schema["components"]["schemas"][model]["properties"], model
